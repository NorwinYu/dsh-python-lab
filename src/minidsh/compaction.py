import json
from typing import Dict, List, Optional

from .llm import Model, text_block
from .plugins import Plugin, ServiceContext
from .session import SessionStore


COMPACTION_PROMPT = """You are now acting as a compaction engine for this AI coding assistant. Condense the conversation ABOVE into a structured checkpoint that lets another model resume the work with no loss of essential context.

Output EXACTLY the Markdown structure below: keep every section, in order. Use terse bullets, not prose paragraphs. Write "(none)" for an empty section — never drop a section.

## Primary Request and Intent
- [the user's original and evolving goals; quote verbatim where the exact wording matters]

## Key Technical Concepts
- [technologies, frameworks, patterns, and conventions in play]

## Files and Code
- [exact path: why it matters, key changes or snippets]

## Errors and Fixes
- [error: how it was resolved, plus any related user feedback]

## Pending Jobs
- [explicitly requested work not yet completed]

## Current Work
- [precisely what was in progress at this checkpoint]

## Next Step
- [the single next action, directly in line with the most recent request, or "(none)"]

## Critical Context
- [decisions and their rationale, constraints, user preferences, open questions, data needed to continue]

Rules:
- Write concise English engineering prose. Preserve exact file paths, commands, error strings, identifiers, numeric values, function signatures, and syntax fragments.
- Capture user feedback and explicit instructions faithfully, especially corrections.
- Do NOT mention this summarization request or that the context was compacted.
- Output only the checkpoint text: do not call any tool or take any other action.
- If the conversation already contains a <compacted-summary> block, it is a PRIOR checkpoint. Do not copy it forward verbatim: preserve still-true facts, drop stale ones, and merge newer information into a single consolidated summary under the same structure."""

SUMMARY_INTRO = (
    "This is an automatically generated checkpoint condensing an earlier span of the conversation to free up context. "
    "Treat the captured context as established background and build on it without restating it. "
    "Continue the task directly from the messages that follow, without acknowledging this checkpoint."
)


def estimate_tokens(system: str, messages: List[Dict[str, object]], tools: List[Dict[str, object]]) -> int:
    serialized = json.dumps({"system": system, "messages": messages, "tools": tools}, ensure_ascii=False)
    return max(1, len(serialized.encode("utf-8")) // 4)


class CompactionService:
    def __init__(self, model: Model, session: Optional[SessionStore], context_window: int) -> None:
        self.model = model
        self.session = session
        self.context_window = context_window
        self.threshold = int(context_window * 0.8)

    def compact_if_needed(
        self,
        system: str,
        messages: List[Dict[str, object]],
        tools: List[Dict[str, object]],
        session_id: str,
        force: bool = False,
    ) -> List[Dict[str, object]]:
        if self.session is None:
            return messages
        if not force and estimate_tokens(system, messages, tools) < self.threshold:
            return messages
        if len(messages) < 2:
            if force:
                raise RuntimeError("没有足够消息可压缩")
            return messages

        retain_count = max(1, int(len(messages) * 0.16))
        if retain_count >= len(messages):
            if force:
                raise RuntimeError("没有安全且有效的压缩区间")
            return messages
        prefix = messages[:-retain_count]
        retained = messages[-retain_count:]
        source_seqs = self._source_seqs(session_id, len(prefix))
        self.session.append(
            session_id,
            "compaction/start",
            {"trigger": "manual" if force else "pressure", "sourceEventSeqs": source_seqs},
        )
        response = self.model.complete(
            COMPACTION_PROMPT,
            [
                {
                    "role": "user",
                    "content": [text_block(json.dumps(prefix, ensure_ascii=False))],
                }
            ],
            [],
            session_id,
        )
        summary = response.content.strip()
        if not summary:
            raise RuntimeError("压缩模型返回空摘要")
        summary_message = {
            "role": "user",
            "content": [text_block(f"{SUMMARY_INTRO}\n\n<compacted-summary>\n{summary}\n</compacted-summary>")],
            "source": {"kind": "compaction"},
        }
        self.session.append(
            session_id,
            "compaction/end",
            {
                "trigger": "manual" if force else "pressure",
                "sourceEventSeqs": source_seqs,
                "message": summary_message,
                "usage": response.usage,
            },
        )
        return [summary_message] + retained

    def _source_seqs(self, session_id: str, count: int) -> List[int]:
        seqs: List[int] = []
        for event in self.session.load(session_id):
            if event["type"] in {"user/message", "assistant/message", "tool/result"}:
                seqs.append(int(event["seq"]))
        return seqs[:count]


class CompactionPlugin(Plugin):
    name = "core.compaction"
    provides = ("compaction",)
    requires = ("llm", "session")

    def __init__(self, context_window: int) -> None:
        self.context_window = context_window

    def activate(self, ctx: ServiceContext) -> None:
        model = ctx.require("llm")
        session = ctx.require("session")
        assert isinstance(model, Model)
        assert isinstance(session, SessionStore)
        ctx.provide("compaction", CompactionService(model, session, self.context_window), self.name)
