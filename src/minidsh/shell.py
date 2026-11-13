import os
import re
from pathlib import Path
from typing import Dict, Optional

from .cancellation import RunCancelled
from .jobs import JobRecord, JobService
from .plugins import Plugin, ServiceContext
from .system_prompt import SystemPrompt
from .tools import Execution, SingleToolPlugin, Tool, ToolError, ToolRegistry, workspace_path


DEFAULT_TIMEOUT_MS = 120_000
JOB_WAIT_DEFAULT_MS = 30_000
JOB_WAIT_MAX_MS = 600_000


BASH_DESCRIPTION = (
    "Execute a bash command (`bash -c`) and return its stdout/stderr. "
    "Each call runs in a fresh shell; pass `workdir` instead of using `cd`. "
    "Managed `DSH_*` variables expose current harness environment facts. "
    "Long output is truncated to its tail; the full output is saved to a file whose path is reported when available. "
    "Provide `description` before `command` in the arguments. "
    "Before any delete or move, verify that the resolved absolute target path is the intended one; "
    "never run it against a computed path you have not checked. "
    "An unset variable expands to an empty string, so guard variables in such paths with `${VAR:?}.`"
    "Commands may run under a file sandbox; a blocked file operation is reported as "
    "`[sandbox: file access denied under <mode> mode]`, a policy denial: do not retry another way."
)


def scrub_environment(session_id: Optional[str]) -> Dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("DSH_") and not re.search(r"KEY|TOKEN|SECRET|PASSWORD", key, re.IGNORECASE)
    }
    env["DSH_HOME"] = str(Path.home() / ".dsh")
    if session_id is not None:
        env["DSH_SESSION_ID"] = session_id
    return env


def read_job_output(job: JobRecord) -> str:
    out, out_lossy, out_path = job.stdout.read_from(0)
    err, err_lossy, err_path = job.stderr.read_from(0)
    body = out
    if err:
        if body and not body.endswith("\n"):
            body += "\n"
        body += f"[stderr]\n{err}"
    if out_lossy or err_lossy:
        paths = ", ".join(path for path in (out_path, err_path) if path is not None)
        if body and not body.endswith("\n"):
            body += "\n"
        body += f"[some output was dropped from memory; full output: {paths or '(unavailable)'}]"
    return body


def render_foreground(job: JobRecord) -> str:
    body = read_job_output(job) or "(no output)"
    if job.status == "killed":
        marker = f"[stopped: {job.kill_reason}]" if job.kill_reason is not None else "[killed by signal: SIGTERM]"
    elif job.status == "completed" and job.detail != "exit code: 0":
        marker = f"[{job.detail}]"
    else:
        marker = ""
    if not marker:
        return body
    if not body.endswith("\n"):
        body += "\n"
    return body + marker


def make_bash_tool(workspace: Path) -> Tool:
    def bash(arguments: Dict[str, object], execution: Execution) -> str:
        command = arguments.get("command")
        description = arguments.get("description")
        timeout_ms = arguments.get("timeoutMs", DEFAULT_TIMEOUT_MS)
        background = arguments.get("run_in_background", False)
        if not isinstance(command, str) or not command.strip():
            raise ToolError("invalid command: expected a non-empty string")
        if not isinstance(description, str) or not description.strip():
            raise ToolError("invalid description: expected a non-empty string")
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
            raise ToolError("invalid timeoutMs: expected a positive number")
        if not isinstance(background, bool):
            raise ToolError("run_in_background must be a boolean")

        sandbox = execution.service("sandboxPolicy")
        if execution.session_id is not None:
            session = execution.session()
            assert session is not None
            sandbox = sandbox.resolve(session, execution.session_id)
        allow_outside = getattr(sandbox, "mode") == "danger-full-access"
        workdir = workspace_path(workspace, arguments.get("workdir", "."), "workdir", allow_outside=allow_outside)
        if not workdir.is_dir():
            raise ToolError(f"workdir 不是目录: {workdir}")
        jobs = execution.service("jobs")
        assert isinstance(jobs, JobService)
        try:
            job = jobs.start(
                command=command,
                cwd=workdir,
                env=scrub_environment(execution.session_id),
                owner=execution.session_id,
                session=execution.session(),
                call_id=execution.call_id,
                sandbox_mode=sandbox.mode,
            )
        except (OSError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

        if background:
            return f"Started background job {job.id}"
        try:
            job = jobs.wait(job, timeout_ms, execution.cancel_token)
        except RunCancelled:
            jobs.kill(job.id, execution.session_id, "run cancelled")
            jobs.wait(job, 5_000)
            raise
        if job.status in {"running", "stopping"}:
            output = jobs.read(job.id, execution.session_id, include_status=False, allow_empty=True)
            if output and not output.endswith("\n"):
                output += "\n"
            return f"{output}[still running after {timeout_ms}ms; moved to background job {job.id}]"
        result = render_foreground(job)
        jobs.remove(job.id, execution.session_id)
        return result

    return Tool(
        name="bash",
        description=BASH_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": (
                        "Clear, concise description of what this command does in active voice, 5-10 words (shown in the UI). "
                        "Examples: \"ls\" → \"List files in current directory\"; \"git status\" → \"Show working tree status\"; "
                        "\"npm install\" → \"Install package dependencies\"."
                    ),
                },
                "command": {"type": "string", "description": "The bash command to execute."},
                "timeoutMs": {
                    "type": "number",
                    "description": "Timeout in milliseconds. The executor applies its configured default and cap; on expiry the command moves to the background as a job instead of being killed.",
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory for this command. Defaults to the session workspace; a relative path resolves against it.",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "Run in the background and return a job id immediately (collect with job_output, stop with job_kill). No timeout applies.",
                },
            },
            "required": ["description", "command"],
        },
        handler=bash,
        mutating=True,
        kind="shell",
    )


def make_job_output_tool() -> Tool:
    def output(arguments: Dict[str, object], execution: Execution) -> str:
        job_id = arguments.get("job_id")
        wait = arguments.get("wait", False)
        timeout_ms = arguments.get("timeout_ms", JOB_WAIT_DEFAULT_MS)
        if not isinstance(job_id, str) or not job_id:
            raise ToolError("invalid job_id: expected a non-empty string")
        if not isinstance(wait, bool):
            raise ToolError("wait must be a boolean")
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
            raise ToolError("invalid timeout_ms")
        jobs = execution.service("jobs")
        assert isinstance(jobs, JobService)
        try:
            return jobs.read(
                job_id,
                execution.session_id,
                wait,
                min(timeout_ms, JOB_WAIT_MAX_MS),
                cancel_token=execution.cancel_token,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return Tool(
        name="job_output",
        description="Read a background job: output since the previous read for stream jobs, or the result of a finished final-output job.",
        parameters={
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job id returned by the tool that started the background work."},
                "wait": {
                    "type": "boolean",
                    "description": "Block until the job finishes or the timeout expires; a timed-out wait leaves the job running. Defaults to false.",
                },
                "timeout_ms": {
                    "type": "number",
                    "description": "Max wait in milliseconds with wait: true. Defaults to and is capped by configuration.",
                },
            },
            "required": ["job_id"],
        },
        handler=output,
    )


def make_job_list_tool() -> Tool:
    def list_jobs(arguments: Dict[str, object], execution: Execution) -> str:
        jobs = execution.service("jobs")
        assert isinstance(jobs, JobService)
        records = jobs.list(execution.session_id)
        if not records:
            return "(no background jobs)"
        return "\n".join(f"{job.id} [{job.kind}] {job.status} — {job.label}" for job in records)

    return Tool(
        name="job_list",
        description="List your background jobs (running and finished) with their ids, kinds, and statuses.",
        parameters={"type": "object", "properties": {}},
        handler=list_jobs,
    )


def make_job_kill_tool() -> Tool:
    def kill(arguments: Dict[str, object], execution: Execution) -> str:
        job_id = arguments.get("job_id")
        reason = arguments.get("reason")
        if not isinstance(job_id, str) or not job_id:
            raise ToolError("invalid job_id: expected a non-empty string")
        if reason is not None and not isinstance(reason, str):
            raise ToolError("reason must be a string")
        jobs = execution.service("jobs")
        assert isinstance(jobs, JobService)
        try:
            outcome = jobs.kill(job_id, execution.session_id, reason)
            job = jobs.expect(job_id, execution.session_id)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if outcome == "already-finished":
            return f"job {job.id} had already finished {jobs.status_line(job)}"
        return f"requested cancellation of job {job.id}"

    return Tool(
        name="job_kill",
        description="Request cancellation of a running background job.",
        parameters={
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job id returned by the tool that started the background work."},
                "reason": {"type": "string", "description": "Optional short reason, recorded in the log and forwarded to the job."},
            },
            "required": ["job_id"],
        },
        handler=kill,
    )


class BashToolPlugin(SingleToolPlugin):
    requires = ("tools", "systemPrompt", "jobs")


class JobToolsPlugin(Plugin):
    name = "tools.jobs"
    requires = ("tools", "systemPrompt", "jobs")

    def __init__(self, prompt_section: tuple) -> None:
        self.prompt_section = prompt_section
        self.tools = [make_job_output_tool(), make_job_list_tool(), make_job_kill_tool()]

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        assert isinstance(registry, ToolRegistry)
        assert isinstance(prompt, SystemPrompt)
        for tool in self.tools:
            registry.register(tool)
        prompt.register(*self.prompt_section)

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        assert isinstance(registry, ToolRegistry)
        assert isinstance(prompt, SystemPrompt)
        for tool in self.tools:
            registry.unregister(tool.name)
        prompt.unregister(self.prompt_section[0])
