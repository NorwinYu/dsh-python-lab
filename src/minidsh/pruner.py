import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from .plugins import Plugin, ServiceContext
from .session import SessionStore


PRUNE_MARKER = "\n\n[... tool result middle pruned ...]\n\n"


@dataclass(frozen=True)
class PruneConfig:
    threshold_chars: int = 8192
    head_chars: int = 4096
    tail_chars: int = 1024

    def __post_init__(self) -> None:
        if self.threshold_chars <= 0 or self.head_chars < 0 or self.tail_chars < 0:
            raise ValueError("pruner threshold must be a positive integer; head and tail lengths must not be negative")
        if self.head_chars + len(PRUNE_MARKER) + self.tail_chars > self.threshold_chars:
            raise ValueError("the combined pruner head, marker, and tail length must not exceed the threshold")

    @classmethod
    def from_env(cls) -> "PruneConfig":
        try:
            return cls(
                int(os.environ.get("MINIDSH_PRUNE_THRESHOLD_CHARS", "8192")),
                int(os.environ.get("MINIDSH_PRUNE_HEAD_CHARS", "4096")),
                int(os.environ.get("MINIDSH_PRUNE_TAIL_CHARS", "1024")),
            )
        except ValueError as exc:
            raise ValueError("invalid pruner numeric configuration") from exc


def measure_content(blocks: List[Dict[str, object]]) -> int:
    return sum(len(str(block.get("text", ""))) for block in blocks if block.get("type") == "text")


class ToolResultPruner:
    def __init__(self, session: SessionStore, config: PruneConfig) -> None:
        self.session = session
        self.config = config

    def prune_content(self, blocks: List[Dict[str, object]]) -> Optional[List[Dict[str, object]]]:
        total = measure_content(blocks)
        if total <= self.config.threshold_chars:
            return None
        removed_start = self.config.head_chars
        removed_end = total - self.config.tail_chars
        result: List[Dict[str, object]] = []
        consumed = 0
        marker_inserted = False
        for block in blocks:
            if block.get("type") != "text":
                result.append(block)
                continue
            text = str(block.get("text", ""))
            block_start = consumed
            block_end = block_start + len(text)
            head_end = min(len(text), max(0, removed_start - block_start))
            tail_start = min(len(text), max(0, removed_end - block_start))
            intersects = block_start < removed_end and block_end > removed_start
            marker = PRUNE_MARKER if intersects and not marker_inserted else ""
            marker_inserted = marker_inserted or bool(marker)
            replacement = text[:head_end] + marker + text[tail_start:]
            if replacement:
                result.append({**block, "text": replacement})
            consumed = block_end
        if not marker_inserted:
            raise RuntimeError("tool-result prune could not locate the trim range")
        if measure_content(result) >= total or measure_content(result) > self.config.threshold_chars:
            raise RuntimeError("tool-result prune result must be smaller and within the threshold")
        return result

    def prune(self, session_id: str) -> Dict[str, object]:
        with self.session.write_lease(session_id):
            events = self.session.load(session_id)
            surface = self.session.surface(session_id)
            candidates = [events[seq] for seq in surface["nodes"] if events[seq]["type"] == "tool/result"]
            pruned: List[Dict[str, object]] = []
            chars_removed = 0
            for event in candidates:
                original = event["payload"]
                content = original.get("content", [])
                if not isinstance(content, list):
                    raise ValueError("tool result content must be an array")
                replacement_content = self.prune_content(content)
                if replacement_content is None:
                    continue
                seq = int(event["seq"])
                message = {**original, "content": replacement_content}
                before = measure_content(content)
                after = measure_content(replacement_content)
                self.session.append(
                    session_id,
                    "compaction/prune",
                    {
                        "shadowedRange": {"start": seq, "end": seq},
                        "shadowedSeqs": [seq],
                        "shadowedTokenCount": max(1, len(json.dumps(original, ensure_ascii=False).encode("utf-8")) // 4),
                    },
                )
                self.session.append(
                    session_id,
                    "tool/result",
                    message,
                    surface_op={"op": "replace", "startSeq": seq, "endSeq": seq},
                    source_event_seqs=[seq],
                )
                replacement_seq = self.session.load(session_id)[-1]["seq"]
                pruned.append(
                    {
                        "originalSeq": seq,
                        "replacementSeq": replacement_seq,
                        "callId": original.get("toolCallId"),
                        "charsBefore": before,
                        "charsAfter": after,
                    }
                )
                chars_removed += before - after
            return {"pruned": pruned, "charsRemoved": chars_removed}


class ToolResultPrunerPlugin(Plugin):
    name = "core.tool-result-pruner"
    provides = ("toolResultPruner",)
    requires = ("session",)

    def activate(self, ctx: ServiceContext) -> None:
        session = ctx.require("session")
        assert isinstance(session, SessionStore)
        ctx.provide("toolResultPruner", ToolResultPruner(session, PruneConfig.from_env()), self.name)
