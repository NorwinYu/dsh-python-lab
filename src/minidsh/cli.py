import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .app import Application
from .config import AppConfig, ConfigError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minidsh", description="分层学习版 Agent Harness")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="工作区，默认是当前目录")
    parser.add_argument("--config", type=Path, help="JSON 配置文件")
    parser.add_argument("--enable", action="append", default=[], metavar="FEATURE", help="启用功能，可重复")
    parser.add_argument("--disable", action="append", default=[], metavar="FEATURE", help="禁用功能，可重复")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("config", help="输出合并后的配置")
    subparsers.add_parser("plugins", help="查看插件及依赖状态")
    subparsers.add_parser("tools", help="列出当前启用的工具")

    run_parser = subparsers.add_parser("run", help="运行一次 Mock Agent")
    run_parser.add_argument("prompt", nargs="+", help="用户提示词")
    run_parser.add_argument("--trace", action="store_true", help="输出工具事件轨迹")

    session_parser = subparsers.add_parser("session", help="读取一份 JSONL 会话")
    session_parser.add_argument("session_id")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
        )
        if args.command == "config":
            print(json.dumps(config.public_dict(), ensure_ascii=False, indent=2))
            return 0

        app = Application(config)
        if args.command == "plugins":
            for row in app.manager.status():
                missing = f" missing={','.join(row['missing'])}" if row["missing"] else ""
                print(f"{row['state']:<8} {row['name']:<18}{missing}")
            return 0

        if args.command == "tools":
            print(json.dumps(app.tools.definitions(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "session":
            if app.session is None:
                raise RuntimeError("session.jsonl 当前被禁用")
            events = app.session.load(args.session_id)
            print(json.dumps(events, ensure_ascii=False, indent=2))
            return 0

        if args.trace:
            for event_name in ("tools/pre-execute", "tools/post-execute", "turn/start", "turn/end"):
                app.events.on(
                    event_name,
                    lambda payload, name=event_name: print(
                        f"[trace] {name} {json.dumps(payload, ensure_ascii=False)}",
                        file=sys.stderr,
                    ),
                )
        print(app.agent.run(" ".join(args.prompt)))
        return 0
    except (ConfigError, KeyError, RuntimeError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    finally:
        if app is not None:
            app.close()


if __name__ == "__main__":
    raise SystemExit(main())
