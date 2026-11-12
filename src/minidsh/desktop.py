import importlib
import threading
from typing import Optional

from .app import Application
from .server import ApiServer


def run_desktop(app: Application, width: int = 1100, height: int = 760, webview_module: Optional[object] = None) -> None:
    if webview_module is None:
        try:
            webview_module = importlib.import_module("webview")
        except ImportError as exc:
            raise RuntimeError("Desktop 需要可选依赖，请安装 minidsh[desktop] 或 minidsh-desktop") from exc
    server = ApiServer(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, name="minidsh-desktop-http", daemon=True)
    thread.start()
    try:
        window = webview_module.create_window("MiniDSH", server.url, width=width, height=height)
        webview_module.start()
        if window is None:
            raise RuntimeError("Desktop 窗口创建失败")
    finally:
        server.close()
        thread.join(timeout=5)
