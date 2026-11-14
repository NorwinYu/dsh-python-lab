import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .cancellation import CancellationToken, RunCancelled
from .plugins import Plugin, ServiceContext
from .system_prompt import SystemPrompt
from .tools import Execution, Tool, ToolError, ToolRegistry


SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
PUBLIC_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
CREDENTIAL = re.compile(r"\$\{credential:([A-Za-z_][A-Za-z0-9_]*)\}")
PROTOCOL_VERSIONS = ("2026-07-28", "2025-06-18", "2025-03-26", "2024-11-05")


class McpError(ToolError):
    pass


def public_tool_name(server: str, raw: str) -> str:
    candidate = f"mcp__{server}__{raw}"
    if len(candidate) <= 64 and PUBLIC_NAME.fullmatch(candidate):
        return candidate
    digest = hashlib.sha256(f"{server}\0{raw}".encode()).hexdigest()[:12]
    normalized = re.sub(r"[^A-Za-z0-9_-]", "_", candidate)
    return f"{normalized[:51]}-{digest}"


def scrub_environment(extra: Dict[str, str]) -> Dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("DSH_") and not re.search(r"KEY|TOKEN|SECRET|PASSWORD", key, re.IGNORECASE)
    }
    env.update(extra)
    return env


def render_content(result: Dict[str, object]) -> str:
    rendered: List[str] = []
    content = result.get("content", [])
    if not isinstance(content, list):
        raise McpError("MCP tool result content must be an array")
    for block in content:
        if not isinstance(block, dict):
            rendered.append("[unsupported MCP content block]")
            continue
        kind = block.get("type")
        if kind == "text":
            rendered.append(str(block.get("text", "")))
        elif kind == "resource_link":
            rendered.append(f"[resource: {block.get('name', '')} {block.get('uri', '')}]")
        elif kind in {"image", "audio"}:
            data = str(block.get("data", ""))
            rendered.append(f"[{kind}: {block.get('mimeType', 'unknown')}, {len(data)} base64 characters]")
        elif kind == "resource":
            resource = block.get("resource", {})
            if isinstance(resource, dict) and isinstance(resource.get("text"), str):
                rendered.append(resource["text"])
            else:
                rendered.append("[embedded non-text resource]")
        else:
            rendered.append(f"[unsupported MCP content: {kind}]")
    if result.get("structuredContent") is not None:
        rendered.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    return "\n".join(rendered)


def render_resource(server: str, value: object) -> str:
    def project(item: object) -> object:
        if isinstance(item, dict):
            return {key: f"[binary resource: {len(child)} base64 characters; available to programmatic callers]" if key == "blob" and isinstance(child, str) else project(child) for key, child in item.items()}
        if isinstance(item, list):
            return [project(child) for child in item]
        return item
    return f"MCP server: {server}\n{json.dumps(project(value), ensure_ascii=False, indent=2)}"


class McpConnection:
    def __init__(
        self,
        manager: "McpManager",
        name: str,
        config: Dict[str, object],
        register: bool = True,
    ) -> None:
        self.manager = manager
        self.name = name
        self.config = config
        self.register = register
        self.status = "disconnected"
        self.error: Optional[str] = None
        self.close_confirmed = False
        self.server_info: Dict[str, object] = {}
        self.instructions = ""
        self.tool_names: List[str] = []
        self.capabilities: Dict[str, object] = {}
        self._process: Optional[subprocess.Popen] = None
        self._session_id: Optional[str] = None
        self._write_lock = threading.Lock()
        self._sync_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._next_id = 0
        self._pending: Dict[int, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._closed = False
        self._secrets: List[str] = []
        self._stderr: List[str] = []

    def start(self) -> None:
        self.status = "connecting"
        try:
            if self.config.get("transport") == "stdio":
                self._spawn()
            for version in PROTOCOL_VERSIONS:
                try:
                    result = self.request("initialize", {
                        "protocolVersion": version,
                        "capabilities": {},
                        "clientInfo": {"name": "minidsh", "version": "0.1.0"},
                    }, timeout=self._timeout("startupTimeoutMs", 30000))
                    break
                except Exception:
                    if version == PROTOCOL_VERSIONS[-1] or self._closed:
                        raise
            self.notify("notifications/initialized", {})
            self.server_info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
            capabilities = result.get("capabilities", {}) if isinstance(result, dict) else {}
            self.capabilities = capabilities if isinstance(capabilities, dict) else {}
            instructions = result.get("instructions", "") if isinstance(result, dict) else ""
            self.instructions = str(instructions) if instructions is not None else ""
            if len((f"MCP server: {self.name}\n{self.instructions}").encode()) > int(self.config.get("maxInstructionBytes", 32768)):
                raise McpError(f"MCP server {self.name} instructions exceed maxInstructionBytes")
            self.status = "connected"
            self.error = None
            self.refresh_tools()
            if self.register:
                self.manager._publish_instructions(self)
        except Exception as exc:
            self.status = "disconnected"
            self.error = self._diagnostic(exc)
            if self.config.get("failOnStartupError"):
                self.close()
                raise
        self.manager._changed(self.name)

    def _spawn(self) -> None:
        command = self._expanded(self.config.get("command"))
        args = self.config.get("args", [])
        if not isinstance(command, str) or not command or not isinstance(args, list):
            raise McpError(f"MCP server {self.name} requires command and string args")
        arguments = [self._expanded(arg) for arg in args]
        if any(not isinstance(arg, str) for arg in arguments):
            raise McpError("MCP stdio args must be strings")
        configured_env = self.config.get("env", {})
        if not isinstance(configured_env, dict):
            raise McpError("MCP stdio env must be an object")
        extra = {str(key): self._expanded(value) for key, value in configured_env.items()}
        if any(not isinstance(value, str) for value in extra.values()):
            raise McpError("MCP stdio env values must be strings")
        cwd = self._expanded(self.config.get("cwd", str(self.manager.workspace)))
        if not isinstance(cwd, str) or not Path(cwd).is_dir():
            raise McpError(f"MCP cwd is not a directory: {cwd}")
        self._process = subprocess.Popen(
            [command] + arguments,
            cwd=cwd,
            env=scrub_environment(extra),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        threading.Thread(target=self._read_stdout, args=(self._process,), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(self._process,), daemon=True).start()

    def _expanded(self, value: object) -> object:
        if not isinstance(value, str):
            return value
        def resolve(match: re.Match) -> str:
            key = match.group(1)
            if key not in os.environ:
                raise McpError(f"waiting-credentials: {key}")
            resolved = os.environ[key]
            self._secrets.append(resolved)
            return resolved
        return CREDENTIAL.sub(resolve, value)

    def _diagnostic(self, exc: Exception) -> str:
        text = str(exc)
        for secret in sorted(set(self._secrets), key=len, reverse=True):
            if secret:
                text = text.replace(secret, "[redacted]").replace(urllib.parse.quote(secret, safe=""), "[redacted]")
        return text

    def _timeout(self, name: str, default: int) -> int:
        value = self.config.get(name, default)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise McpError(f"{name} must be a positive integer")
        return value

    def request(
        self,
        method: str,
        params: Optional[Dict[str, object]] = None,
        token: Optional[CancellationToken] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, object]:
        if self._closed:
            raise McpError(f"MCP server {self.name} is closed")
        timeout = timeout or self._timeout("toolCallTimeoutMs", 60000)
        with self._request_lock:
            self._next_id += 1
            request_id = self._next_id
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        if self.config.get("transport") == "streamable-http":
            return self._http_request(message, token, timeout)
        waiter: queue.Queue = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = waiter
        try:
            self._write(message)
            deadline = time.monotonic() + timeout / 1000
            while True:
                if token is not None:
                    token.throw_if_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise McpError(f"MCP request timed out: {method}")
                try:
                    response = waiter.get(timeout=min(remaining, 0.05))
                    break
                except queue.Empty:
                    if self._process is not None and self._process.poll() is not None:
                        raise McpError(f"MCP stdio process exited: {self._process.returncode}")
            if "error" in response:
                raise McpError(f"MCP {method} failed: {response['error']}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise McpError(f"MCP {method} returned a non-object result")
            return result
        except Exception as exc:
            if token is not None and token.cancelled:
                try:
                    self.notify("notifications/cancelled", {"requestId": request_id, "reason": "caller cancelled"})
                except Exception:
                    pass
            if isinstance(exc, RunCancelled):
                raise
            raise McpError(self._diagnostic(exc)) from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: Dict[str, object]) -> None:
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if self.config.get("transport") == "streamable-http":
            self._http_request(message, None, self._timeout("toolCallTimeoutMs", 60000))
        else:
            self._write(message)

    def _write(self, message: Dict[str, object]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise McpError(f"MCP server {self.name} stdio is unavailable")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode()
        with self._write_lock:
            process.stdin.write(data)
            process.stdin.flush()

    def _http_request(self, message: Dict[str, object], token: Optional[CancellationToken], timeout: int) -> Dict[str, object]:
        if token is not None:
            token.throw_if_cancelled()
        url = self._expanded(self.config.get("url"))
        if not isinstance(url, str) or urllib.parse.urlsplit(url).scheme not in {"http", "https"}:
            raise McpError("MCP streamable-http requires an HTTP(S) url")
        configured_headers = self.config.get("headers", {})
        if not isinstance(configured_headers, dict):
            raise McpError("MCP headers must be an object")
        headers = {str(key): str(self._expanded(value)) for key, value in configured_headers.items()}
        headers.update({"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        if self._session_id:
            headers["MCP-Session-Id"] = self._session_id
        request = urllib.request.Request(url, data=json.dumps(message).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout / 1000) as response:
                self._session_id = response.headers.get("MCP-Session-Id", self._session_id)
                content_type = response.headers.get("Content-Type", "")
                raw = response.read(8 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {202, 204}:
                return {}
            raise
        if len(raw) > 8 * 1024 * 1024:
            raise McpError("MCP HTTP response exceeds 8MiB")
        if not raw:
            return {}
        if "text/event-stream" in content_type:
            payloads = []
            for block in raw.decode("utf-8", errors="replace").split("\n\n"):
                data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
                if data:
                    payloads.append(json.loads(data))
            response_value = next((item for item in payloads if item.get("id") == message.get("id")), payloads[-1] if payloads else {})
        else:
            response_value = json.loads(raw)
        if not isinstance(response_value, dict):
            raise McpError("MCP HTTP response must be a JSON-RPC object")
        if "error" in response_value:
            raise McpError(f"MCP {message.get('method')} failed: {response_value['error']}")
        result = response_value.get("result", {})
        if not isinstance(result, dict):
            raise McpError("MCP HTTP result must be an object")
        return result

    def _read_stdout(self, process: subprocess.Popen) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                request_id = message.get("id")
                if isinstance(request_id, int):
                    with self._pending_lock:
                        waiter = self._pending.get(request_id)
                    if waiter is not None:
                        waiter.put(message)
                elif message.get("method") == "notifications/tools/list_changed" and not self._closed:
                    threading.Thread(target=self.refresh_tools, daemon=True).start()
        finally:
            if not self._closed:
                self.status = "disconnected"
                self.error = "MCP stdio transport closed"
                self.manager._changed(self.name)

    def _read_stderr(self, process: subprocess.Popen) -> None:
        assert process.stderr is not None
        data = process.stderr.read(65537)
        if data:
            self._stderr.append(data[:65536].decode("utf-8", errors="replace"))

    def refresh_tools(self) -> None:
        if not self.register or self._closed or self.status != "connected":
            return
        with self._sync_lock:
            try:
                remote = self._list_all("tools/list", "tools")
                names = [item.get("name") for item in remote]
                if any(not isinstance(name, str) or not name for name in names) or len(names) != len(set(names)):
                    raise McpError(f"MCP server {self.name} returned duplicate or invalid tool names")
                tools = [self._make_tool(item) for item in remote]
                self.manager.registry.replace_namespace(f"mcp__{self.name}__", tools)
                self.tool_names = [str(name) for name in names]
                self.error = None
                self.manager._changed(self.name)
            except Exception as exc:
                self.error = self._diagnostic(exc)

    def _list_all(self, method: str, key: str) -> List[Dict[str, object]]:
        items: List[Dict[str, object]] = []
        cursor: Optional[str] = None
        for _ in range(16):
            params = {"cursor": cursor} if cursor is not None else {}
            result = self.request(method, params)
            page = result.get(key, [])
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise McpError(f"MCP {method} returned an invalid page")
            items.extend(page)
            cursor_value = result.get("nextCursor")
            if cursor_value is None:
                return items
            if not isinstance(cursor_value, str) or not cursor_value or cursor_value == cursor:
                raise McpError(f"MCP {method} returned an invalid cursor")
            cursor = cursor_value
        raise McpError(f"MCP {method} exceeded 16 pages")

    def _make_tool(self, definition: Dict[str, object]) -> Tool:
        raw = str(definition["name"])
        schema = definition.get("inputSchema", {"type": "object", "properties": {}})
        if not isinstance(schema, dict):
            raise McpError(f"MCP tool {raw} has an invalid inputSchema")
        def execute(arguments: Dict[str, object], execution: Execution) -> str:
            result = self.request("tools/call", {"name": raw, "arguments": arguments}, execution.cancel_token)
            rendered = render_content(result)
            if result.get("isError"):
                raise McpError(rendered or f"MCP tool {raw} failed")
            return rendered
        return Tool(public_tool_name(self.name, raw), str(definition.get("description", "")), schema, execute, kind="mcp")

    def resource(self, method: str, arguments: Dict[str, object], token: Optional[CancellationToken]) -> object:
        if self.status != "connected":
            raise McpError(self.error or f"MCP server {self.name} is unavailable")
        if "resources" not in self.capabilities:
            if method == "resources/list":
                return {"resources": []}
            if method == "resources/templates/list":
                return {"resourceTemplates": []}
        params = dict(arguments)
        if method in {"resources/list", "resources/templates/list"} and params.get("cursor") is None:
            key = "resources" if method == "resources/list" else "resourceTemplates"
            return {key: self._list_all(method, key)}
        return self.request(method, params, token)

    def close(self) -> None:
        self._closed = True
        process = self._process
        if process is not None and process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=int(self.config.get("closeTimeoutMs", 5000)) / 1000)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=1)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        self.close_confirmed = process is None or process.poll() is not None
        if self.register:
            self.manager.registry.replace_namespace(f"mcp__{self.name}__", [])
            self.manager.prompt.unregister(f"mcp:{self.name}")
        self.status = "closed"
        self.manager._changed(self.name)


class McpResourceRouter:
    def __init__(self) -> None:
        self.manager: Optional[McpManager] = None
        self.visible = False

    def attach(self, manager: "McpManager", registry: ToolRegistry, prompt: SystemPrompt) -> None:
        self.manager = manager
        self.registry = registry
        self.prompt = prompt
        self.sync()

    def sync(self) -> None:
        if self.manager is None:
            return
        visible = bool(self.manager.configured_names())
        if visible == self.visible:
            if visible:
                self._guidance()
            return
        self.visible = visible
        if visible:
            self._register()
        else:
            for name in ("list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource"):
                self.registry.unregister(name)
            self.prompt.unregister("mcp:resource-servers")

    def _register(self) -> None:
        def server(args: Dict[str, object]) -> str:
            value = args.get("server")
            if not isinstance(value, str) or not value:
                raise McpError("server must be a non-empty string")
            return value
        def dispatch(args: Dict[str, object], execution: Execution, method: str, fields: List[str]) -> str:
            assert self.manager is not None
            connection = self.manager.connection(server(args))
            params = {key: args[key] for key in fields if key in args}
            result = connection.resource(method, params, execution.cancel_token)
            return render_resource(connection.name, result)
        list_schema = {"type": "object", "properties": {"server": {"type": "string", "description": "Configured MCP server name."}, "cursor": {"type": "string", "description": "Continuation cursor returned by this server."}}, "required": ["server"], "additionalProperties": False}
        tools = [
            Tool("list_mcp_resources", "List resources available from an MCP server.", list_schema,
                 lambda args, execution: dispatch(args, execution, "resources/list", ["cursor"]), kind="mcp"),
            Tool("list_mcp_resource_templates", "List parameterized resource URI templates from an MCP server.", list_schema,
                 lambda args, execution: dispatch(args, execution, "resources/templates/list", ["cursor"]), kind="mcp"),
            Tool("read_mcp_resource", "Read an MCP resource by URI from the named server. Use a listed URI or an expanded resource template.",
                 {"type": "object", "properties": {"server": list_schema["properties"]["server"], "uri": {"type": "string", "description": "Resource URI to read."}}, "required": ["server", "uri"], "additionalProperties": False},
                 lambda args, execution: dispatch(args, execution, "resources/read", ["uri"]), kind="mcp"),
        ]
        for tool in tools:
            self.registry.register(tool)
        self._guidance()

    def _guidance(self) -> None:
        assert self.manager is not None
        names = json.dumps(self.manager.configured_names())
        self.prompt.unregister("mcp:resource-servers")
        self.prompt.register("mcp:resource-servers", 1760, f"Use list_mcp_resources, list_mcp_resource_templates, or read_mcp_resource with one of these names as the server argument: {names}.")


class McpManager:
    def __init__(self, ctx: ServiceContext, workspace: Path, servers: Dict[str, Dict[str, object]]) -> None:
        self.ctx = ctx
        self.workspace = workspace
        self.registry = ctx.require("tools")
        self.prompt = ctx.require("systemPrompt")
        self.router = ctx.optional("mcpResources")
        self._entries: Dict[str, Tuple[Dict[str, object], bool]] = {}
        self._connections: Dict[str, McpConnection] = {}
        for name, config in servers.items():
            self._validate(name, config)
            enabled = config.get("enabled", True)
            if not isinstance(enabled, bool):
                raise McpError(f"MCP server {name} enabled must be boolean")
            self._entries[name] = (dict(config), True)
        if self.router is not None:
            self.router.attach(self, self.registry, self.prompt)
        try:
            for name in self._entries:
                if self._entries[name][0].get("enabled", True):
                    self._connections[name] = McpConnection(self, name, self._entries[name][0])
                    self._connections[name].start()
        except Exception:
            for connection in self._connections.values():
                connection.close()
            self._connections.clear()
            raise
        self._changed(None)

    def _validate(self, name: str, config: Dict[str, object]) -> None:
        if not SERVER_NAME.fullmatch(name) or not isinstance(config, dict):
            raise McpError(f"invalid MCP server name or config: {name}")
        transport = config.get("transport")
        if transport not in {"stdio", "streamable-http"}:
            raise McpError(f"MCP server {name} transport must be stdio or streamable-http")
        if transport == "stdio" and not isinstance(config.get("command"), str):
            raise McpError(f"MCP server {name} requires command")
        if transport == "streamable-http" and not isinstance(config.get("url"), str):
            raise McpError(f"MCP server {name} requires url")

    def configured_names(self) -> List[str]:
        return sorted(self._connections)

    def connection(self, name: str) -> McpConnection:
        if name not in self._connections:
            raise McpError(f"MCP server is not configured: {name}")
        return self._connections[name]

    def list(self) -> List[Dict[str, object]]:
        rows = []
        for name, (config, configured) in self._entries.items():
            connection = self._connections.get(name)
            rows.append({"name": name, "configured": configured, "enabled": config.get("enabled", True),
                         "status": connection.status if connection is not None else "disabled",
                         "error": connection.error if connection is not None else None,
                         "tools": list(connection.tool_names) if connection is not None else [],
                         "serverInfo": dict(connection.server_info) if connection is not None else {}})
        return rows

    def upsert(self, name: str, config: Dict[str, object], configured: bool = True) -> None:
        self._validate(name, config)
        if name in self._entries and self._entries[name][1] != configured:
            raise McpError(f"MCP server name is already registered: {name}")
        old = self._connections.pop(name, None)
        if old is not None:
            old.close()
        self._entries[name] = (dict(config), configured)
        if config.get("enabled", True):
            connection = McpConnection(self, name, config)
            self._connections[name] = connection
            connection.start()
        self._changed(name)

    def remove(self, name: str) -> None:
        if name not in self._entries:
            raise McpError(f"unknown MCP server: {name}")
        connection = self._connections.pop(name, None)
        if connection is not None:
            connection.close()
        del self._entries[name]
        self._changed(name)

    def restart(self, name: str) -> None:
        if name not in self._entries:
            raise McpError(f"unknown MCP server: {name}")
        config, configured = self._entries[name]
        self.upsert(name, config, configured)

    def probe(self, name: str) -> Dict[str, object]:
        if name not in self._entries:
            raise McpError(f"unknown MCP server: {name}")
        config = self._entries[name][0]
        connection = McpConnection(self, name, config, register=False)
        connection.start()
        try:
            remote = connection._list_all("tools/list", "tools") if connection.status == "connected" else []
            return {"name": name, "status": connection.status, "error": connection.error,
                    "serverInfo": connection.server_info, "tools": [item.get("name") for item in remote]}
        finally:
            connection.close()

    def _publish_instructions(self, connection: McpConnection) -> None:
        self.prompt.unregister(f"mcp:{connection.name}")
        if connection.instructions.strip():
            text = f"MCP server: {connection.name}\n{connection.instructions}"
            self.prompt.register(f"mcp:{connection.name}", 1750, text)

    def _changed(self, name: Optional[str]) -> None:
        if self.router is not None:
            self.router.sync()
        self.ctx.events.emit("mcp-manager/status", {"server": name, "servers": self.list()})

    def close(self) -> None:
        for connection in list(self._connections.values()):
            connection.close()
        self._connections.clear()
        if self.router is not None:
            self.router.sync()


class McpPlugin(Plugin):
    name = "core.mcp"
    provides = ("mcpManager",)
    requires = ("tools", "systemPrompt")

    def __init__(self, workspace: Path, servers: Dict[str, Dict[str, object]]) -> None:
        self.workspace = workspace
        self.servers = servers
        self.manager: Optional[McpManager] = None

    def activate(self, ctx: ServiceContext) -> None:
        self.manager = McpManager(ctx, self.workspace, self.servers)
        ctx.provide("mcpManager", self.manager, self.name)

    def deactivate(self, ctx: ServiceContext) -> None:
        if self.manager is not None:
            self.manager.close()


class McpResourcesPlugin(Plugin):
    name = "tools.mcp-resources"
    provides = ("mcpResources",)

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("mcpResources", McpResourceRouter(), self.name)
