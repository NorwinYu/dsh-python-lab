import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

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
    subparsers.add_parser("models", help="列出当前模型提供方的模型")
    subparsers.add_parser("files", help="列出 DeepSeek Files API 文件")
    subparsers.add_parser("prompt", help="输出当前系统提示词与 runtime context")
    subparsers.add_parser("sessions", help="列出本地会话")
    subparsers.add_parser("skills", help="列出当前工作区技能")
    skill_parser = subparsers.add_parser("skill", help="加载一份技能完整指令")
    skill_parser.add_argument("name")
    skill_parser.add_argument("--user", action="store_true", help="按用户显式调用策略加载")
    goal_parser = subparsers.add_parser("goal", help="查看或控制会话目标")
    goal_parser.add_argument("session_id")
    goal_parser.add_argument("action", nargs="?", default="get", choices=["get", "create", "edit", "pause", "resume", "complete", "blocked", "clear"])
    goal_parser.add_argument("--objective")
    goal_parser.add_argument("--rounds", type=int)
    goal_parser.add_argument("--reason")
    goal_parser.add_argument("--goal-id")
    goal_parser.add_argument("--revision", type=int)

    run_parser = subparsers.add_parser("run", help="新建会话并运行 Agent")
    run_parser.add_argument("prompt", nargs="+", help="用户提示词")
    run_parser.add_argument("--image", action="append", default=[], metavar="PATH", help="附加图片，可重复")
    run_parser.add_argument("--trace", action="store_true", help="输出工具事件轨迹")

    resume_parser = subparsers.add_parser("resume", help="恢复会话并继续运行")
    resume_parser.add_argument("session_id")
    resume_parser.add_argument("prompt", nargs="+", help="新的用户提示词")
    resume_parser.add_argument("--image", action="append", default=[], metavar="PATH", help="附加图片，可重复")
    resume_parser.add_argument("--trace", action="store_true", help="输出工具事件轨迹")

    session_parser = subparsers.add_parser("session", help="读取一份 JSONL 会话")
    session_parser.add_argument("session_id")

    transcript_parser = subparsers.add_parser("transcript", help="输出会话的可读消息投影")
    transcript_parser.add_argument("session_id")

    todos_parser = subparsers.add_parser("todos", help="查看会话当前的待办清单")
    todos_parser.add_argument("session_id")

    fork_parser = subparsers.add_parser("fork", help="复制会话到新分支")
    fork_parser.add_argument("session_id")
    fork_parser.add_argument("--at-seq", type=int, help="只保留到指定事件序号")

    compact_parser = subparsers.add_parser("compact", help="手动创建压缩检查点")
    compact_parser.add_argument("session_id")

    repair_parser = subparsers.add_parser("repair", help="备份并修复会话的不完整末尾或开放回合")
    repair_parser.add_argument("session_id")

    migrate_parser = subparsers.add_parser("migrate", help="备份并迁移旧会话；省略 id 时迁移全部")
    migrate_parser.add_argument("session_id", nargs="?")

    surface_parser = subparsers.add_parser("surface", help="查看当前模型消息 surface 和 replacement 记录")
    surface_parser.add_argument("session_id")

    stream_parser = subparsers.add_parser("stream", help="展开会话中压缩保存的原始模型分片")
    stream_parser.add_argument("session_id")

    prune_parser = subparsers.add_parser("prune", help="裁剪当前 surface 中过长的工具结果")
    prune_parser.add_argument("session_id")

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


def interactive_questions(request: Dict[str, object]) -> Dict[str, object]:
    if not sys.stdin.isatty():
        raise RuntimeError("无交互式提问者")
    answers = []
    for question in request["questions"]:
        if question.get("header"):
            print(f"\n[{question['header']}]")
        print(question["question"])
        options = question.get("options", [])
        for index, option in enumerate(options, 1):
            description = f" — {option['description']}" if option.get("description") else ""
            print(f"  {index}. {option['label']}{description}")
        prompt = "选择编号（多选用逗号），其他文本作为自定义回答；回车跳过: " if options else "回答（回车跳过）: "
        raw = input(prompt).strip()
        selected = []
        custom = None
        if raw and options:
            try:
                selected = [options[int(item.strip()) - 1]["label"] for item in raw.split(",")]
            except (ValueError, IndexError):
                custom = raw
        elif raw:
            custom = raw
        answer = {"id": question["id"], "selected": selected}
        if custom is not None:
            answer["custom"] = custom
        answers.append(answer)
    return {"answers": answers}


def _with_default_command(argv: List[str], command: str) -> List[str]:
    commands = {"config", "plugins", "tools", "models", "files", "prompt", "sessions", "skills", "skill", "goal", "run", "resume", "session", "transcript", "todos", "fork", "compact", "repair", "migrate", "surface", "stream", "prune", "serve", "desktop"}
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

        app = Application(
            config,
            interactive_answerer if args.command in {"run", "resume"} else None,
            interactive_questions if args.command in {"run", "resume"} else None,
        )
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

        if args.command == "models":
            if not config.features["llm.model-listing"]:
                raise RuntimeError("llm.model-listing 当前被禁用")
            print(json.dumps(app.model.list_models(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "files":
            if not config.features["llm.files"]:
                raise RuntimeError("llm.files 当前被禁用")
            print(json.dumps(app.model.list_files(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "prompt":
            print(json.dumps({"system": app.system_prompt.render(), "runtime_context": app.runtime_context}, ensure_ascii=False, indent=2))
            return 0

        if args.command == "sessions":
            if app.session is None:
                raise RuntimeError("session.jsonl 当前被禁用")
            print(json.dumps(app.session.list_sessions(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "skills":
            print(json.dumps(app.ctx.require("skills").snapshot(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "skill":
            print(app.ctx.require("skills").load(args.name, model=not args.user))
            return 0

        if args.command == "goal":
            goals = app.ctx.require("goals")
            if args.action == "get":
                result = goals.get(args.session_id)
            elif args.action == "create":
                result = goals.create(args.session_id, args.objective, args.rounds)
            else:
                current = goals.get(args.session_id)
                if current is None:
                    raise RuntimeError("当前会话没有 goal")
                reason = {"code": "user-reported", "message": args.reason or ""} if args.action == "blocked" else None
                result = goals.update(args.session_id, args.goal_id or current["id"], args.revision or current["revision"], args.action, args.objective, args.rounds, reason)
                if args.action in {"pause", "complete", "blocked", "clear"}:
                    goals.disarm(args.session_id)
            print(json.dumps({"goal": result}, ensure_ascii=False, indent=2))
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

        if args.command == "todos":
            if app.session is None:
                raise RuntimeError("session.jsonl 当前被禁用")
            print(json.dumps(app.session.todos(args.session_id), ensure_ascii=False, indent=2))
            return 0

        if args.command == "fork":
            if app.session is None:
                raise RuntimeError("session.jsonl 当前被禁用")
            print(app.session.fork(args.session_id, args.at_seq))
            return 0

        if args.command == "compact":
            print(app.agent.compact(args.session_id))
            return 0

        if args.command == "repair":
            if app.session is None:
                raise RuntimeError("session.jsonl 当前被禁用")
            print(json.dumps(app.session.repair(args.session_id), ensure_ascii=False, indent=2))
            return 0

        if args.command == "migrate":
            if app.session is None:
                raise RuntimeError("session.jsonl 当前被禁用")
            result = app.session.migrate(args.session_id) if args.session_id else app.session.migrate_all()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "surface":
            if app.session is None:
                raise RuntimeError("session.jsonl 当前被禁用")
            print(json.dumps(app.session.surface(args.session_id), ensure_ascii=False, indent=2))
            return 0

        if args.command == "stream":
            if app.session is None:
                raise RuntimeError("session.jsonl 当前被禁用")
            print(json.dumps(app.session.stream_chunks(args.session_id), ensure_ascii=False, indent=2))
            return 0

        if args.command == "prune":
            if not config.features["core.tool-result-pruner"]:
                raise RuntimeError("请使用 --enable core.tool-result-pruner")
            pruner = app.ctx.require("toolResultPruner")
            print(json.dumps(pruner.prune(args.session_id), ensure_ascii=False, indent=2))
            return 0

        if hasattr(args, "trace") and args.trace:
            for event_name in ("tools/pre-execute", "tools/post-execute", "turn/start", "turn/end", "llm/retry", "pty/created", "pty/reset"):
                app.events.on(
                    event_name,
                    lambda payload, name=event_name: print(
                        f"[trace] {name} {json.dumps(payload, ensure_ascii=False)}",
                        file=sys.stderr,
                    ),
                )
        if args.command == "resume":
            print(app.agent.run(" ".join(args.prompt), args.session_id, image_paths=args.image))
        else:
            print(app.agent.run(" ".join(args.prompt), image_paths=args.image))
        return 0
    except (ConfigError, KeyError, RuntimeError, OSError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    finally:
        if app is not None:
            app.close()


if __name__ == "__main__":
    raise SystemExit(main())
