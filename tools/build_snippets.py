#!/usr/bin/env python3
"""Extract annotated source snippets for the documentation site.

The site is static, so it cannot read the repository at runtime. This script
pulls exact line ranges out of the source and emits ``docs/snippets.js`` with
line numbers preserved, so a chapter can show the real code — and a drift check
can prove the page still matches the repository.

Usage:
    python3 tools/build_snippets.py            # write docs/snippets.js
    python3 tools/build_snippets.py --check    # fail if docs/snippets.js is stale
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys
from typing import Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (key, path, "Class.method") — the whole definition, docstring included.
SYMBOLS: List[Tuple[str, str, str]] = [
    # Chapter 1 — plugin kernel
    ("plugins.context.provide", "src/minidsh/plugins.py", "ServiceContext.provide"),
    ("plugins.register", "src/minidsh/plugins.py", "PluginManager.register"),
    ("plugins.activate", "src/minidsh/plugins.py", "PluginManager.activate_all"),
    ("plugins.deactivate", "src/minidsh/plugins.py", "PluginManager.deactivate_all"),
    # Chapter 2 — composition root
    ("app.agent-plugin", "src/minidsh/app.py", "AgentPlugin.activate"),
    ("events.bus", "src/minidsh/events.py", "EventBus"),
    # Chapter 4 — agent loop
    ("agent.run", "src/minidsh/agent.py", "Agent.run"),
    # Chapter 5 — system prompt
    ("prompt.render", "src/minidsh/system_prompt.py", "SystemPrompt.render"),
    ("prompt.runtime", "src/minidsh/system_prompt.py", "SystemPrompt.runtime_context"),
    # Chapter 6 — tool registry
    ("tools.execute", "src/minidsh/tools.py", "ToolRegistry.execute"),
    ("tools.authorize", "src/minidsh/tools.py", "ToolRegistry._authorize"),
    ("tools.definition", "src/minidsh/tools.py", "Tool.definition"),
    # Chapter 7 — file tools
    ("tools.read", "src/minidsh/tools.py", "make_read_tool"),
    ("tools.write", "src/minidsh/tools.py", "make_write_tool"),
    ("tools.edit", "src/minidsh/tools.py", "make_edit_tool"),
    ("tools.atomic", "src/minidsh/tools.py", "atomic_write"),
    # Chapter 8 — streaming
    ("deepseek.sse", "src/minidsh/deepseek.py", "iter_sse"),
    # Chapter 9 — sandbox policy
    ("sandbox.resolve", "src/minidsh/sandbox.py", "SandboxPolicy.resolve"),
    ("sandbox.context", "src/minidsh/sandbox.py", "SandboxPolicy.context"),
    # Chapter 10 — approval
    ("approval.request", "src/minidsh/approval.py", "ApprovalService.request"),
    ("approval.policy", "src/minidsh/approval.py", "ApprovalService.effective_policy"),
    # Chapter 11 — observations
    ("observations.record", "src/minidsh/observations.py", "ObservationStore.record"),
    ("observations.require", "src/minidsh/observations.py", "ObservationStore.require_current"),
    # Chapter 12 — kernel sandbox
    ("seatbelt.profile", "src/minidsh/seatbelt.py", "profile"),
    ("seatbelt.argv", "src/minidsh/seatbelt.py", "SeatbeltService.argv"),
    # Chapter 13 — processes and jobs
    ("jobs.read", "src/minidsh/jobs.py", "JobService.read"),
    ("jobs.kill", "src/minidsh/jobs.py", "JobService.kill"),
    # Chapter 14 — credentials
    ("shell.scrub", "src/minidsh/shell.py", "scrub_environment"),
    ("deepseek.env", "src/minidsh/deepseek.py", "DeepSeekConfig.from_env"),
    # Chapter 15 — sessions
    ("session.append", "src/minidsh/session.py", "JsonlSessionStore._append_current"),
    ("session.lease", "src/minidsh/session.py", "JsonlSessionStore._acquire"),
    ("generations.publish", "src/minidsh/generations.py", "publish"),
    # Chapter 16 — recovery
    ("session.repair", "src/minidsh/session.py", "JsonlSessionStore.repair"),
    ("recovery.closers", "src/minidsh/session_recovery.py", "open_turn_closers"),
    ("session.fork", "src/minidsh/session.py", "JsonlSessionStore.fork"),
    # Chapter 17 — surface
    ("surface.fold", "src/minidsh/surface.py", "fold_surface"),
    ("surface.validate", "src/minidsh/surface.py", "_validate_tool_rewrite"),
    # Chapter 18 — compaction and pruning
    ("compaction.needed", "src/minidsh/compaction.py", "CompactionService.compact_if_needed"),
    ("pruner.content", "src/minidsh/pruner.py", "ToolResultPruner.prune_content"),
    # Chapter 19 — stream recording
    ("codec.pack", "src/minidsh/stream_codec.py", "pack_stream"),
    # Chapter 20 — subagents
    ("subagents.child", "src/minidsh/subagents.py", "SubagentService._run_child"),
    ("subagents.inputs", "src/minidsh/subagents.py", "SubagentService.consume_inputs"),
    # Chapter 21 — workflow
    ("workflow.member", "src/minidsh/workflow.py", "WorkflowRun._start_member"),
    ("workflow.settle", "src/minidsh/workflow.py", "WorkflowRun._settle_members"),
    # Chapter 22 — MCP
    ("mcp.name", "src/minidsh/mcp.py", "public_tool_name"),
    ("mcp.request", "src/minidsh/mcp.py", "McpConnection.request"),
    ("mcp.tool", "src/minidsh/mcp.py", "McpConnection._make_tool"),
    # Chapter 23 — skills
    ("skills.parse", "src/minidsh/skills.py", "parse_skill_file"),
    ("skills.catalog", "src/minidsh/skills.py", "render_catalog"),
    # Chapter 24 — schedule
    ("schedule.deliver", "src/minidsh/schedule.py", "ScheduleService._deliver"),
    ("schedule.inbox", "src/minidsh/schedule.py", "ScheduleService._inbox"),
]

# (key, path, (start, end)) — slices inside a larger definition, 1-based inclusive.
RANGES: List[Tuple[str, str, Tuple[int, int]]] = [
    # Chapter 2 — the registration list, then the presets and activation tail
    ("app.registration", "src/minidsh/app.py", (121, 163)),
    ("app.activation", "src/minidsh/app.py", (164, 199)),
    # Chapter 3 — flags and the conditional write guidance
    ("config.features", "src/minidsh/config.py", (10, 40)),
    ("config.load", "src/minidsh/config.py", (88, 150)),
    ("prompt.write-guidance", "src/minidsh/app.py", (131, 138)),
    # Chapter 4 — context assembly and the step loop
    ("agent.assemble", "src/minidsh/agent.py", (95, 166)),
    ("agent.steps", "src/minidsh/agent.py", (168, 240)),
    # Chapter 8 — forwarding deltas
    ("agent.streaming", "src/minidsh/agent.py", (205, 262)),
    # Chapter 19 — expansion checks
    ("codec.expand", "src/minidsh/stream_codec.py", (75, 108)),
]


def find_symbol(tree: ast.AST, dotted: str) -> Optional[Tuple[int, int]]:
    parts = dotted.split(".")
    body = getattr(tree, "body", [])
    node = None
    for part in parts:
        node = next(
            (item for item in body if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == part),
            None,
        )
        if node is None:
            return None
        body = getattr(node, "body", [])
    return node.lineno, node.end_lineno


def slice_lines(path: str, start: int, end: int) -> str:
    text = (ROOT / path).read_text(encoding="utf-8").splitlines()
    if start < 1 or end > len(text):
        raise SystemExit(f"{path}:{start}-{end} is outside the file ({len(text)} lines)")
    # Strip leading/trailing blank lines but keep interior structure.
    chunk = text[start - 1 : end]
    while chunk and not chunk[0].strip():
        chunk.pop(0)
        start += 1
    while chunk and not chunk[-1].strip():
        chunk.pop()
    return "\n".join(chunk), start


def build() -> Dict[str, Dict[str, object]]:
    cache: Dict[str, ast.AST] = {}
    out: Dict[str, Dict[str, object]] = {}

    for key, path, dotted in SYMBOLS:
        if path not in cache:
            cache[path] = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        span = find_symbol(cache[path], dotted)
        if span is None:
            raise SystemExit(f"symbol not found: {dotted} in {path}")
        code, start = slice_lines(path, span[0], span[1])
        out[key] = {"file": path, "start": start, "code": code}

    for key, path, (start, end) in RANGES:
        code, first = slice_lines(path, start, end)
        out[key] = {"file": path, "start": first, "code": code}

    return out


HEADER = """/* Generated by tools/build_snippets.py — do not edit by hand.
   Each entry is a verbatim slice of the repository, with its first line number.
   Re-run the script after changing the source, or CI will report drift. */
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify that docs/snippets.js is current")
    args = parser.parse_args()

    snippets = build()
    payload = HEADER + "window.MINIDSH_SNIPPETS = " + json.dumps(snippets, ensure_ascii=False, indent=2) + ";\n"
    target = ROOT / "docs" / "snippets.js"

    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != payload:
            print("docs/snippets.js is stale — run python3 tools/build_snippets.py", file=sys.stderr)
            return 1
        print(f"ok: {len(snippets)} snippets match the source")
        return 0

    target.write_text(payload, encoding="utf-8")
    total = sum(len(item["code"].splitlines()) for item in snippets.values())
    print(f"wrote {target.relative_to(ROOT)} · {len(snippets)} snippets · {total} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
