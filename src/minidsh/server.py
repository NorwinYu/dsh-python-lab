import json
import queue
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from .agent import Agent
from .app import Application
from .cancellation import CancellationToken, RunCancelled
from .jobs import JobRecord, JobService


EVENT_NAMES = (
    "turn/start",
    "turn/end",
    "assistant/message",
    "llm/retry",
    "tools/pre-execute",
    "tools/post-execute",
    "job/settled",
)


class SseBroker:
    def __init__(self, agent: Agent, cancellation_enabled: bool = True) -> None:
        self.agent = agent
        self.cancellation_enabled = cancellation_enabled
        self._listeners: List[queue.Queue] = []
        self._cancellations: Dict[str, CancellationToken] = {}
        self._cancellation_lock = threading.Lock()
        for name in EVENT_NAMES:
            agent.events.on(name, lambda payload, event=name: self.publish(event, payload))

    def publish(self, event: str, payload: Dict[str, object]) -> None:
        message = {"event": event, "payload": payload}
        for listener in list(self._listeners):
            listener.put(message)

    def subscribe(self) -> queue.Queue:
        listener: queue.Queue = queue.Queue()
        self._listeners.append(listener)
        return listener

    def unsubscribe(self, listener: queue.Queue) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def cancel(self, run_id: str) -> bool:
        if not self.cancellation_enabled:
            raise RuntimeError("core.cancellation 当前被禁用")
        with self._cancellation_lock:
            token = self._cancellations.get(run_id)
        return token.cancel() if token is not None else False

    def start(self, prompt: str, session_id: Optional[str]) -> queue.Queue:
        listener = self.subscribe()
        actual_session_id = session_id
        operation_id = uuid.uuid4().hex
        token = CancellationToken(operation_id)
        with self._cancellation_lock:
            self._cancellations[operation_id] = token

        def run() -> None:
            nonlocal actual_session_id
            stop_capture = None
            try:
                if actual_session_id is None:
                    def capture(payload: Dict[str, object]) -> None:
                        nonlocal actual_session_id
                        if payload.get("run_id") == operation_id:
                            actual_session_id = str(payload["session_id"])
                    stop_capture = self.agent.events.on("turn/start", capture)
                result = self.agent.run(prompt, session_id, operation_id, token)
                self.publish("run/result", {"session_id": actual_session_id, "result": result, "run_id": operation_id})
            except RunCancelled:
                self.publish("run/cancelled", {"session_id": actual_session_id, "run_id": operation_id})
            except Exception as exc:
                self.publish("run/error", {"session_id": actual_session_id, "error": str(exc), "run_id": operation_id})
            finally:
                if stop_capture is not None:
                    stop_capture()
                with self._cancellation_lock:
                    self._cancellations.pop(operation_id, None)
                self.publish("run/done", {"session_id": actual_session_id, "run_id": operation_id})

        listener.operation_id = operation_id
        threading.Thread(target=run, daemon=True).start()
        return listener

    def wait(self, prompt: str, session_id: Optional[str]) -> Dict[str, object]:
        listener = self.start(prompt, session_id)
        operation_id = listener.operation_id if hasattr(listener, "operation_id") else None
        try:
            while True:
                message = listener.get()
                if message["payload"].get("run_id") != operation_id:
                    continue
                if message["event"] == "run/result":
                    return message["payload"]
                if message["event"] == "run/error":
                    raise RuntimeError(str(message["payload"]["error"]))
                if message["event"] == "run/cancelled":
                    raise RunCancelled("运行已取消")
                if message["event"] == "run/done":
                    raise RuntimeError("run finished without a result")
        finally:
            self.unsubscribe(listener)


def job_view(job: JobRecord) -> Dict[str, object]:
    return {
        "id": job.id,
        "kind": job.kind,
        "label": job.label,
        "status": job.status,
        "detail": job.detail,
        "startedAt": job.started_at,
        "finishedAt": job.finished_at,
    }


class ApiServer:
    def __init__(self, app: Application, host: str = "127.0.0.1", port: int = 3080, token: Optional[str] = None) -> None:
        self.app = app
        self.broker = SseBroker(app.agent, app.config.features["core.cancellation"])
        self.token = token
        self.http = ThreadingHTTPServer((host, port), self._handler())
        self.host = host
        self.port = self.http.server_address[1]
        self.url = f"http://{host}:{self.port}"

    def serve_forever(self) -> None:
        self.http.serve_forever()

    def close(self) -> None:
        self.http.shutdown()
        self.http.server_close()

    def _handler(self) -> type:
        app = self.app
        broker = self.broker
        configured_token = self.token

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                try:
                    if not self._authorized():
                        return
                    path, query = self._request()
                    if path == "/":
                        self._send_file(Path(__file__).with_name("web").joinpath("index.html"), "text/html; charset=utf-8")
                    elif path == "/health":
                        self._send_json({"ok": True, "service": "minidsh"})
                    elif path == "/v1/plugins":
                        self._send_json(app.manager.status())
                    elif path == "/v1/tools":
                        self._send_json(app.tools.definitions())
                    elif path == "/v1/models":
                        if not app.config.features["llm.model-listing"]:
                            raise RuntimeError("llm.model-listing 当前被禁用")
                        self._send_json(app.model.list_models())
                    elif path == "/v1/prompt":
                        self._send_json({"system": app.system_prompt.render(), "runtime_context": app.runtime_context})
                    elif path == "/v1/sessions":
                        self._send_json(app.session.list_sessions() if app.session is not None else [])
                    elif path.startswith("/v1/sessions/"):
                        self._session_get(path)
                    elif path == "/v1/jobs":
                        jobs = app.ctx.require("jobs")
                        assert isinstance(jobs, JobService)
                        self._send_json([job_view(job) for job in jobs.list(None)])
                    elif path.startswith("/v1/jobs/"):
                        jobs = app.ctx.require("jobs")
                        assert isinstance(jobs, JobService)
                        job_id = path.split("/")[3]
                        wait = query.get("wait", ["false"])[0].lower() == "true"
                        timeout = min(int(query.get("timeout_ms", ["30000"])[0]), 600_000)
                        self._send_json({"text": jobs.read(job_id, None, wait, timeout)})
                    elif path == "/v1/runs/stream":
                        prompt = query.get("prompt", [""])[0]
                        session_id = query.get("session_id", [None])[0] or None
                        self._stream(prompt, session_id)
                    else:
                        self._send_json({"error": "not found"}, 404)
                except RunCancelled as exc:
                    self._send_json({"error": str(exc), "code": "ABORTED"}, 409)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, 400)

            def do_POST(self) -> None:
                try:
                    if not self._authorized():
                        return
                    path, query = self._request()
                    body = self._json_body()
                    if path == "/v1/runs":
                        prompt = self._prompt(body)
                        session_id = body.get("session_id") or None
                        self._send_json(broker.wait(prompt, session_id))
                    elif path == "/v1/runs/stream":
                        self._stream(self._prompt(body), body.get("session_id") or None)
                    elif path.startswith("/v1/runs/"):
                        parts = path.split("/")
                        if len(parts) == 5 and parts[4] == "cancel":
                            self._send_json({"run_id": parts[3], "cancelled": broker.cancel(parts[3])})
                        else:
                            self._send_json({"error": "not found"}, 404)
                    elif path.startswith("/v1/sessions/"):
                        parts = path.split("/")
                        session_id = parts[3]
                        action = parts[4] if len(parts) > 4 else ""
                        if action == "resume":
                            self._send_json(broker.wait(self._prompt(body), session_id))
                        elif action == "fork":
                            self._send_json({"session_id": app.session.fork(session_id, body.get("at_seq"))})
                        elif action == "compact":
                            self._send_json({"result": app.agent.compact(session_id)})
                        else:
                            self._send_json({"error": "not found"}, 404)
                    elif path.startswith("/v1/jobs/"):
                        parts = path.split("/")
                        if len(parts) == 5 and parts[4] == "kill":
                            jobs = app.ctx.require("jobs")
                            assert isinstance(jobs, JobService)
                            outcome = jobs.kill(parts[3], None, body.get("reason"))
                            self._send_json({"outcome": outcome})
                        else:
                            self._send_json({"error": "not found"}, 404)
                    else:
                        self._send_json({"error": "not found"}, 404)
                except RunCancelled as exc:
                    self._send_json({"error": str(exc), "code": "ABORTED"}, 409)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, 400)

            def _authorized(self) -> bool:
                if configured_token is None:
                    return True
                expected = f"Bearer {configured_token}"
                if self.headers.get("Authorization") != expected:
                    self._send_json({"error": "unauthorized"}, 401)
                    return False
                return True

            def _request(self) -> Tuple[str, Dict[str, List[str]]]:
                parsed = urlsplit(self.path)
                return parsed.path, parse_qs(parsed.query)

            def _json_body(self) -> Dict[str, object]:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 1_000_000:
                    raise ValueError("request body too large")
                if length == 0:
                    return {}
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("JSON body must be an object")
                return data

            @staticmethod
            def _prompt(body: Dict[str, object]) -> str:
                prompt = body.get("prompt", "")
                if not isinstance(prompt, str) or not prompt.strip():
                    raise ValueError("prompt 必须是非空字符串")
                return prompt

            def _session_get(self, path: str) -> None:
                if app.session is None:
                    self._send_json({"error": "session.jsonl is disabled"}, 400)
                    return
                parts = path.split("/")
                session_id = parts[3]
                if len(parts) == 5 and parts[4] == "transcript":
                    self._send_json({"text": app.session.transcript(session_id)})
                elif len(parts) == 4:
                    self._send_json(app.session.load(session_id))
                else:
                    self._send_json({"error": "not found"}, 404)

            def _stream(self, prompt: str, session_id: Optional[str]) -> None:
                if not prompt.strip():
                    raise ValueError("prompt 必须是非空字符串")
                listener = broker.start(prompt, session_id)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                sequence = 0
                try:
                    while True:
                        try:
                            message = listener.get(timeout=15)
                        except queue.Empty:
                            self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                            continue
                        event = message["event"]
                        payload = message["payload"]
                        operation_id = listener.operation_id
                        if event.startswith("run/") and payload.get("run_id") != operation_id:
                            continue
                        if event != "job/settled" and payload.get("run_id") != operation_id:
                            continue
                        if event == "run/done":
                            break
                        sequence += 1
                        data = json.dumps(payload, ensure_ascii=False)
                        self.wfile.write(f"id: {sequence}\nevent: {event}\ndata: {data}\n\n".encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    broker.unsubscribe(listener)

            def _send_json(self, value: object, status: int = 200) -> None:
                data = json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _send_file(self, path: Path, content_type: str) -> None:
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler
