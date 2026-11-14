import codecs
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .cancellation import CancellationToken
from .events import EventBus
from .plugins import Plugin, ServiceContext
from .seatbelt import SeatbeltService
from .session import SessionStore


MAX_CONCURRENT_JOBS = 10
RETAIN_BYTES = 256 * 1024
SETTLED_RETAIN_BYTES = 16 * 1024


class OutputChannel:
    def __init__(self, path: Path, retain_bytes: int = RETAIN_BYTES) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("ab")
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._text = b""
        self._earliest = 0
        self.total = 0
        self.retain_bytes = retain_bytes

    def restore(self) -> None:
        data = self.path.read_bytes() if self.path.exists() else b""
        self.total = len(data)
        self._text = data[-self.retain_bytes:]
        self._earliest = self.total - len(self._text)
        self._file.seek(0, os.SEEK_END)

    def append(self, data: bytes) -> None:
        if not data:
            return
        self._file.write(data)
        self._file.flush()
        self.total += len(data)
        self._text += self._decoder.decode(data).encode("utf-8")
        self._trim(self.retain_bytes)

    def finish(self) -> None:
        tail = self._decoder.decode(b"", final=True)
        if tail:
            data = tail.encode("utf-8")
            self._file.write(data)
            self._file.flush()
            self.total += len(data)
            self._text += data
            self._trim(self.retain_bytes)
        self._file.close()

    def read_from(self, offset: int) -> Tuple[str, bool, Optional[str]]:
        lossy = offset < self._earliest
        start = max(0, offset - self._earliest)
        text = self._text[start:].decode("utf-8", errors="replace")
        return text, lossy, str(self.path) if lossy else None

    def trim_settled(self) -> None:
        self._trim(SETTLED_RETAIN_BYTES)

    def _trim(self, retain_bytes: int) -> None:
        overflow = len(self._text) - retain_bytes
        if overflow > 0:
            self._text = self._text[overflow:]
            self._earliest += overflow


@dataclass
class JobRecord:
    id: str
    kind: str
    label: str
    owner: Optional[str]
    process: Optional[subprocess.Popen]
    stdout: OutputChannel
    stderr: OutputChannel
    started_at: int
    session: Optional[SessionStore]
    call_id: Optional[str]
    status: str = "running"
    detail: Optional[str] = None
    finished_at: Optional[int] = None
    kill_reason: Optional[str] = None
    stdout_cursor: int = 0
    stderr_cursor: int = 0
    result_delivered: bool = False
    on_cancel: Optional[Callable[[Optional[str]], None]] = None
    recovered: bool = False
    condition: threading.Condition = field(default_factory=threading.Condition)


class JobService:
    def __init__(self, root: Path, events: Optional[EventBus] = None, kernel_sandbox: Optional[SeatbeltService] = None) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.events = events
        self.kernel_sandbox = kernel_sandbox
        self._jobs: Dict[str, JobRecord] = {}

    def start(
        self,
        command: str,
        cwd: Path,
        env: Dict[str, str],
        owner: Optional[str],
        session: Optional[SessionStore],
        call_id: Optional[str],
        sandbox_mode: str = "read-only",
    ) -> JobRecord:
        active = sum(
            job.owner == owner and job.status in {"running", "stopping"}
            for job in self._jobs.values()
        )
        if active >= MAX_CONCURRENT_JOBS:
            raise ValueError(f"background job limit reached for this owner (limit: {MAX_CONCURRENT_JOBS})")

        job_id = f"bash-{uuid.uuid4().hex[:16]}"
        while job_id in self._jobs:
            job_id = f"bash-{uuid.uuid4().hex[:16]}"
        directory = self.root / job_id
        stdout = OutputChannel(directory / "stdout.log")
        stderr = OutputChannel(directory / "stderr.log")
        argv = self.kernel_sandbox.argv(sandbox_mode, command) if self.kernel_sandbox is not None else ["/bin/bash", "-c", command]
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        job = JobRecord(
            id=job_id,
            kind="bash",
            label=command,
            owner=owner,
            process=process,
            stdout=stdout,
            stderr=stderr,
            started_at=int(time.time() * 1000),
            session=session,
            call_id=call_id,
        )
        self._jobs[job_id] = job
        self._record(job, "job/registered")
        stdout_thread = threading.Thread(target=self._read_stream, args=(process.stdout, stdout), daemon=True)
        stderr_thread = threading.Thread(target=self._read_stream, args=(process.stderr, stderr), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        threading.Thread(target=self._wait_process, args=(job, stdout_thread, stderr_thread), daemon=True).start()
        return job

    def start_managed(
        self,
        job_id: str,
        process: subprocess.Popen,
        kind: str,
        label: str,
        owner: str,
        session: SessionStore,
        call_id: Optional[str],
        on_cancel: Callable[[Optional[str]], None],
    ) -> JobRecord:
        active = sum(job.owner == owner and job.status in {"running", "stopping"} for job in self._jobs.values())
        if active >= MAX_CONCURRENT_JOBS:
            raise ValueError(f"background job limit reached for this owner (limit: {MAX_CONCURRENT_JOBS})")
        if not job_id.startswith("workflow-") or job_id in self._jobs:
            raise ValueError(f"invalid or duplicate managed job id: {job_id}")
        directory = self.root / job_id
        job = JobRecord(
            id=job_id,
            kind=kind,
            label=label,
            owner=owner,
            process=process,
            stdout=OutputChannel(directory / "stdout.log"),
            stderr=OutputChannel(directory / "stderr.log"),
            started_at=int(time.time() * 1000),
            session=session,
            call_id=call_id,
            on_cancel=on_cancel,
        )
        self._jobs[job_id] = job
        self._record(job, "job/registered")
        return job

    def append(self, job_id: str, text: str, error: bool = False) -> None:
        job = self._jobs[job_id]
        if job.status not in {"running", "stopping"}:
            return
        channel = job.stderr if error else job.stdout
        data = text.encode("utf-8")
        with job.condition:
            channel.append(data)

    def settle(self, job_id: str, status: str, detail: str, result: Optional[str] = None) -> None:
        job = self._jobs[job_id]
        if status not in {"completed", "failed", "killed"}:
            raise ValueError(f"invalid managed job status: {status}")
        with job.condition:
            if job.status in {"completed", "failed", "killed"}:
                return
            if result is not None:
                job.stdout.append(result.encode("utf-8"))
            job.status = status
            job.detail = detail
            job.finished_at = int(time.time() * 1000)
            job.stdout.finish()
            job.stderr.finish()
            self._record(job, "job/settled")
            if self.events is not None:
                self.events.emit("job/settled", {"jobId": job.id, "owner": job.owner, "status": status, "detail": detail})
            job.condition.notify_all()

    @staticmethod
    def _read_stream(stream: object, channel: OutputChannel) -> None:
        try:
            while True:
                data = stream.read(4096)  # type: ignore[attr-defined]
                if not data:
                    break
                channel.append(data)
        finally:
            channel.finish()
            stream.close()  # type: ignore[attr-defined]

    def _wait_process(self, job: JobRecord, stdout: object, stderr: object) -> None:
        return_code = job.process.wait()
        stdout.join()  # type: ignore[attr-defined]
        stderr.join()  # type: ignore[attr-defined]
        with job.condition:
            if job.status in {"killed", "completed", "failed"}:
                return
            try:
                if job.status == "stopping":
                    job.status = "killed"
                    job.detail = job.kill_reason or f"signal: SIGTERM"
                else:
                    job.status = "completed"
                    job.detail = f"exit code: {return_code}"
                job.finished_at = int(time.time() * 1000)
                self._record(job, "job/settled")
                if self.events is not None:
                    self.events.emit(
                        "job/settled",
                        {"jobId": job.id, "owner": job.owner, "status": job.status, "detail": job.detail},
                    )
            finally:
                job.condition.notify_all()

    @staticmethod
    def _record(job: JobRecord, event_type: str) -> None:
        if job.session is None or job.owner is None:
            return
        data: Dict[str, object] = {
            "jobId": job.id,
            "owner": job.owner,
            "kind": job.kind,
            "label": job.label,
            "startedAt": job.started_at,
        }
        if job.call_id is not None:
            data["toolCall"] = {"callId": job.call_id}
        if event_type == "job/settled":
            data.update(
                {
                    "finishedAt": job.finished_at,
                    "status": job.status,
                    "cause": "recovery" if job.recovered else ("kill" if job.kill_reason is not None else "producer"),
                    "producerReported": not job.recovered,
                }
            )
            if job.detail is not None:
                data["detail"] = job.detail
        job.session.append(job.owner, event_type, data)

    def wait(self, job: JobRecord, timeout_ms: int, cancel_token: Optional[CancellationToken] = None) -> JobRecord:
        deadline = time.monotonic() + timeout_ms / 1000

        def wake() -> None:
            with job.condition:
                job.condition.notify_all()

        unregister = cancel_token.on_cancel(wake) if cancel_token is not None else lambda: None
        try:
            with job.condition:
                while job.status in {"running", "stopping"}:
                    if cancel_token is not None:
                        cancel_token.throw_if_cancelled()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    job.condition.wait(remaining)
            return job
        finally:
            unregister()

    def read(
        self,
        job_id: str,
        owner: Optional[str],
        wait: bool = False,
        timeout_ms: int = 30_000,
        include_status: bool = True,
        allow_empty: bool = False,
        cancel_token: Optional[CancellationToken] = None,
    ) -> str:
        job = self.expect(job_id, owner)
        if wait:
            job = self.wait(job, timeout_ms, cancel_token)
        with job.condition:
            out, out_lossy, out_path = job.stdout.read_from(job.stdout_cursor)
            err, err_lossy, err_path = job.stderr.read_from(job.stderr_cursor)
            job.stdout_cursor = job.stdout.total
            job.stderr_cursor = job.stderr.total
            if job.status in {"completed", "killed", "failed"} and not job.result_delivered:
                job.result_delivered = True
                job.stdout.trim_settled()
                job.stderr.trim_settled()
            body = out
            if err:
                if body and not body.endswith("\n"):
                    body += "\n"
                body += f"[stderr]\n{err}"
            lossy_paths = [path for path in (out_path, err_path) if path is not None]
            if out_lossy or err_lossy:
                paths = ", ".join(lossy_paths) if lossy_paths else "(unavailable)"
                if body and not body.endswith("\n"):
                    body += "\n"
                body += f"[some output was dropped from memory; full output: {paths}]"
            if not body and not allow_empty:
                body = "(no new output)"
            if body and not body.endswith("\n"):
                body += "\n"
            return f"{body}{self.status_line(job)}" if include_status else body

    def list(self, owner: Optional[str]) -> List[JobRecord]:
        return [job for job in self._jobs.values() if job.owner is None or job.owner == owner]

    def kill(self, job_id: str, owner: Optional[str], reason: Optional[str] = None) -> str:
        job = self.expect(job_id, owner)
        if job.status in {"completed", "killed", "failed"}:
            return "already-finished"
        with job.condition:
            job.status = "stopping"
            job.kill_reason = reason
            if job.on_cancel is None:
                self._terminate(job.process)
            job.condition.notify_all()
        if job.on_cancel is not None:
            job.on_cancel(reason)
        return "requested"

    def remove(self, job_id: str, owner: Optional[str]) -> None:
        job = self.expect(job_id, owner)
        if job.status in {"running", "stopping"}:
            raise ValueError(f"job {job_id} is still {job.status}")
        del self._jobs[job_id]

    def close(self) -> None:
        for job in list(self._jobs.values()):
            if job.status in {"running", "stopping"}:
                job.kill_reason = "jobs service disposed"
                with job.condition:
                    job.status = "stopping"
                if job.on_cancel is not None:
                    job.on_cancel(job.kill_reason)
                else:
                    self._terminate(job.process)
        deadline = time.monotonic() + 5
        for job in list(self._jobs.values()):
            remaining = max(0.0, deadline - time.monotonic())
            if job.status in {"running", "stopping"}:
                with job.condition:
                    job.condition.wait(remaining)
            if job.status in {"running", "stopping"}:
                self._kill(job.process)
            if job.recovered:
                job.stdout.finish()
                job.stderr.finish()

    def recover(self, session: SessionStore) -> None:
        for row in session.list_sessions():
            if row.get("state") != "ok":
                continue
            owner = str(row["session_id"])
            events = session.load(owner)
            registered: Dict[str, Dict[str, object]] = {}
            settled: Dict[str, Dict[str, object]] = {}
            for event in events:
                payload = event["payload"]
                if event["type"] == "job/registered" and payload.get("kind") == "workflow":
                    registered[str(payload["jobId"])] = payload
                elif event["type"] == "job/settled" and payload.get("kind") == "workflow":
                    settled[str(payload["jobId"])] = payload
            for job_id, registration in registered.items():
                if job_id in self._jobs:
                    continue
                outcome = settled.get(job_id)
                interrupted = outcome is None
                status = "failed" if interrupted else str(outcome["status"])
                detail = "interrupted: host restarted" if interrupted else outcome.get("detail")
                stdout = OutputChannel(self.root / job_id / "stdout.log")
                stderr = OutputChannel(self.root / job_id / "stderr.log")
                stdout.restore()
                stderr.restore()
                call = registration.get("toolCall", {})
                job = JobRecord(
                    id=job_id,
                    kind="workflow",
                    label=str(registration.get("label", job_id)),
                    owner=owner,
                    process=None,
                    stdout=stdout,
                    stderr=stderr,
                    started_at=int(registration.get("startedAt", 0)),
                    session=session,
                    call_id=str(call.get("callId")) if isinstance(call, dict) and call.get("callId") else None,
                    status=status,
                    detail=str(detail) if detail is not None else None,
                    finished_at=int(outcome["finishedAt"]) if outcome is not None else int(time.time() * 1000),
                    recovered=True,
                )
                self._jobs[job_id] = job
                if interrupted:
                    self._record(job, "job/settled")

    def expect(self, job_id: str, owner: Optional[str]) -> JobRecord:
        if not job_id:
            raise ValueError("invalid job_id: expected a non-empty string")
        if job_id not in self._jobs:
            raise ValueError(f"unknown job {job_id}")
        job = self._jobs[job_id]
        if job.owner is not None and job.owner != owner:
            raise ValueError(f"job {job_id} belongs to another session")
        return job

    @staticmethod
    def status_line(job: JobRecord) -> str:
        if job.detail is not None:
            return f"[status: {job.status}, {job.detail}]"
        return f"[status: {job.status}]"

    @staticmethod
    def _terminate(process: Optional[subprocess.Popen]) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    @staticmethod
    def _kill(process: Optional[subprocess.Popen]) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class JobPlugin(Plugin):
    name = "core.jobs"
    provides = ("jobs",)

    def __init__(self, root: Path) -> None:
        self.root = root

    def activate(self, ctx: ServiceContext) -> None:
        kernel = ctx.optional("kernelSandbox")
        assert kernel is None or isinstance(kernel, SeatbeltService)
        ctx.provide("jobs", JobService(self.root, ctx.events, kernel), self.name)

    def deactivate(self, ctx: ServiceContext) -> None:
        jobs = ctx.require("jobs")
        assert isinstance(jobs, JobService)
        jobs.close()
