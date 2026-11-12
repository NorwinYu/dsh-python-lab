import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from .app import Application
from .config import AppConfig, ConfigError
from .sandbox import SANDBOX_MODES
from .server import ApiServer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minidsh", description="分层学习版 Agent Harness")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="工作区，默认是当前目录")
    parser.add_argument("--config", type=Path, help="JSON 配置文件")
    parser.add_argument("--enable", action="append", default=[], metavar="FEATURE", help="启用功能，可重复")
    parser.add_argument("--disable", action="append", default=[], metavar="FEATURE", help="禁用功能，可重复")
    parser.add_argument("--sandbox-mode", choices=sorted(SANDBOX_MODES), help="文件策略")
    parser.add_argument("--approval-policy", choices=["ask", "never"], help="审批策略")
    parser.add_argument("--yes", action="store_true", help="允许本次进程中的所有写/Shell 操作审批")
    parser.add_argument("--context-window", type=int, help="压缩使用的上下文容量")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("config", help="输出合并后的配置")
    subparsers.add_parser("plugins", help="查看插件及依赖状态")
    subparsers.add_parser("tools", help="列出当前启用的工具")
    subparsers.add_parser("prompt", help="输出当前系统提示词与 runtime context")
    subparsers.add_parser("sessions", help="列出本地会话")

    run_parser = subparsers.add_parser("run", help="新建会话并运行 Agent")
    run_parser.add_argument("prompt", nargs="+", help="用户提示词")
    run_parser.add_argument("--trace", action="store_true", help="输出工具事件轨迹")

    resume_parser = subparsers.add_parser("resume", help="恢复会话并继续运行")
    resume_parser.add_argument("session_id")
    resume_parser.add_argument("prompt", nargs="+", help="新的用户提示词")
    resume_parser.add_argument("--trace", action="store_true", help="输出工具事件轨迹")

    session_parser = subparsers.add_parser("session", help="读取一份 JSONL 会话")
    session_parser.add_argument("session_id")

    transcript_parser = subparsers.add_parser("transcript", help="输出会话的可读消息投影")
    transcript_parser.add_argument("session_id")

    fork_parser = subparsers.add_parser("fork", help="复制会话到新分支")
    fork_parser.add_argument("session_id")
    fork_parser.add_argument("--at-seq", type=int, help="只保留到指定事件序号")

    compact_parser = subparsers.add_parser("compact", help="手动创建压缩检查点")
    compact_parser.add_argument("session_id")

    serve_parser = subparsers.add_parser("serve", help="启动本地 HTTP/SSE 宿主")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=3080)

    desktop_parser = subparsers.add_parser("desktop", help="启动可选的 pywebview Desktop 宿主")
    desktop_parser.add_argument("--width", type=int, default=1100)
    desktop_parser.add_argument("--height", type=int, default=760)
    return parser


def interactive_answerer(reason: str) -> str:
    if not sys.stdin.isatty():
        raise RuntimeError("无交互式审批者")
    answer = input(f"{reason}\n允许这一次吗？[y/N] ").strip().lower()
    return "allowed-once" if answer in {"y", "yes"} else "rejected"


def _with_default_command(argv: List[str], command: str) -> List[str]:
    commands = {"config", "plugins", "tools", "prompt", "sessions", "run", "resume", "session", "transcript", "fork", "compact", "serve", "desktop"}
    options_with_values = {"--workspace", "--config", "--enable", "--disable", "--sandbox-mode", "--approval-policy", "--context-window"}
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument in commands:
            return argv
        if argument in options_with_values:
            index += 2
        elif any(argument.startswith(option + "=") for option in options_with_values) or argument in {"--yes", "--help", "-h"}:
            index += 1
        else:
            break
    return argv[:index] + [command] + argv[index:]


def main(argv: Optional[List[str]] = None, default_command: Optional[str] = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    if default_command is not None:
        actual_argv = _with_default_command(actual_argv, default_command)
    parser = build_parser()
    args = parser.parse_args(actual_argv)
    if args.command is None:
        parser.print_help()
        return 0

    app: Optional[Application] = None
    try:
        config = AppConfig.load(
            workspace=args.workspace,
            config_path=args.config,
            enable=args.enable,
            disable=args.disable,
            sandbox_mode=args.sandbox_mode,
            approval_policy=args.approval_policy,
            approve_all=args.yes,
            context_window=args.context_window,
        )
        if args.command == "config":
            print(json.dumps(config.public_dict(), ensure_ascii=False, indent=2))
            return 0

        app = Application(config, interactive_answerer if args.command in {"run", "resume"} else None)
        if args.command == "desktop":
            from .desktop import run_desktop

            run_desktop(app, args.width, args.height)
            return 0
        if args.command == "serve":
            if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.environ.get("MINIDSH_TOKEN"):
                raise RuntimeError("绑定非回环地址必须设置 MINIDSH_TOKEN")
            server = ApiServer(app, args.host, args.port, os.environ.get("MINIDSH_TOKEN"))
            print(f"MiniDSH: {server.url}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.close()
            return 0

        if args.command == "plugins":
            for row in app.manager.status():
                missing = f" missing={','.join(row['missing'])}" if row["missing"] else ""
                print(f"{row['state']:<8} {row['name']:<22}{missing}")
            return 0

        if args.command == "tools":
            print(json.dumps(app.tools.definitions(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "prompt":
            print(json.dumps({"system": app.system_prompt.render(), "runtime_context": app.runtime_context}, ensure_ascii=False, indent=2))
            return 0

        if args.command == "sessions":
            if app.session is None:
                raise RuntimeError("session.jsonl 当前被禁用")
            print(json.dumps(app.session.list_sessions(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "session":
            if app.session is None:
                raise RuntimeError("session.jsonl 当前被禁用")
            print(json.dumps(app.session.load(args.session_id), ensure_ascii=False, indent=2))
            return 0

        if args.command == "transcript":
            if app.session is None:
                raise RuntimeError("session.jsonl 当前被禁用")
            print(app.session.transcript(args.session_id))
            return 0

        if args.command == "fork":
            if app.session is None:
                raise RuntimeError("session.jsonl 当前被禁用")
            print(app.session.fork(args.session_id, args.at_seq))
            return 0

        if args.command == "compact":
            print(app.agent.compact(args.session_id))
            return 0

        if hasattr(args, "trace") and args.trace:
            for event_name in ("tools/pre-execute", "tools/post-execute", "turn/start", "turn/end"):
                app.events.on(
                    event_name,
                    lambda payload, name=event_name: print(
                        f"[trace] {name} {json.dumps(payload, ensure_ascii=False)}",
                        file=sys.stderr,
                    ),
                )
        if args.command == "resume":
            print(app.agent.run(" ".join(args.prompt), args.session_id))
        else:
            print(app.agent.run(" ".join(args.prompt)))
        return 0
    except (ConfigError, KeyError, RuntimeError, OSError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    finally:
        if app is not None:
            app.close()


if __name__ == "__main__":
    raise SystemExit(main())
