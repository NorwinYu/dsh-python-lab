import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .cancellation import RunCancelled
from .plugins import Plugin, ServiceContext
from .session import SessionStore
from .tools import Execution, Tool, ToolError, ToolRegistry


SKIPPED_ANSWER_GUIDANCE = "An answer item with empty selected and no custom means the user skipped that question: continue without it, do not ask it again, and do not treat the skip as permission."
ASK_DESCRIPTION = "Ask the user a concise question when you need confirmation, a choice, or missing information before proceeding. " + SKIPPED_ANSWER_GUIDANCE
PENDING_NOTICE = "No answer batch arrived before the timeout. This is pending, not a skipped answer. Continue useful independent work. The user can still answer; their reply will be a user message identified as answer_to_pending_question with this callId and the original questions. Do not treat this as permission."
TIMED_DESCRIPTION = "Ask brief, direct, self-contained questions about missing information, preferences, or decisions. Use user-facing terms; assume no knowledge of background work or internal names. Use distinct stable question IDs. " + SKIPPED_ANSWER_GUIDANCE + " A pending result instead means no answer batch arrived before the timeout and the user can still answer."


@dataclass
class PendingQuestion:
    id: str
    call_id: Optional[str]
    run_id: Optional[str]
    session_id: Optional[str]
    questions: List[Dict[str, object]]
    condition: threading.Condition
    session: Optional[SessionStore] = None
    answers: Optional[Dict[str, object]] = None
    cancelled: bool = False
    returned: bool = False


def validate_questions(value: object) -> List[Dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise ToolError("questions must be a non-empty array")
    questions: List[Dict[str, object]] = []
    ids = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ToolError("question must be an object")
        question_id = raw.get("id")
        text = raw.get("question")
        if not isinstance(question_id, str) or not question_id or question_id in ids:
            raise ToolError("question id must be non-empty and unique within this call")
        if not isinstance(text, str) or not text.strip():
            raise ToolError("question must be a non-empty string")
        ids.add(question_id)
        question: Dict[str, object] = {"id": question_id, "question": text}
        if raw.get("header") is not None:
            if not isinstance(raw["header"], str):
                raise ToolError("header must be a string")
            question["header"] = raw["header"]
        options = raw.get("options", [])
        if not isinstance(options, list):
            raise ToolError("options must be an array")
        normalized_options = []
        labels = set()
        for option in options:
            if not isinstance(option, dict) or not isinstance(option.get("label"), str) or not option["label"]:
                raise ToolError("option label must be a non-empty string")
            if option["label"] in labels:
                raise ToolError("option labels must not be duplicated")
            labels.add(option["label"])
            normalized = {"label": option["label"]}
            if option.get("description") is not None:
                if not isinstance(option["description"], str):
                    raise ToolError("option description must be a string")
                normalized["description"] = option["description"]
            normalized_options.append(normalized)
        if normalized_options:
            question["options"] = normalized_options
        multi_select = raw.get("multi_select", False)
        if not isinstance(multi_select, bool):
            raise ToolError("multi_select must be a boolean")
        question["multi_select"] = multi_select
        questions.append(question)
    return questions


def validate_answers(questions: List[Dict[str, object]], value: object) -> Dict[str, object]:
    if not isinstance(value, dict) or not isinstance(value.get("answers"), list):
        raise ToolError("answers must be an array")
    by_id = {str(question["id"]): question for question in questions}
    answers: List[Dict[str, object]] = []
    seen = set()
    for raw in value["answers"]:
        if not isinstance(raw, dict) or raw.get("id") not in by_id or raw["id"] in seen:
            raise ToolError("answer id must match a question and must not be repeated")
        question = by_id[raw["id"]]
        seen.add(raw["id"])
        selected = raw.get("selected", [])
        if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
            raise ToolError("selected must be an array of strings")
        labels = {option["label"] for option in question.get("options", [])}
        if any(item not in labels for item in selected):
            raise ToolError("selected contains an unknown option")
        if not question.get("multi_select") and len(selected) > 1:
            raise ToolError("a single-select question cannot have multiple selected options")
        answer: Dict[str, object] = {"id": raw["id"], "selected": selected}
        if raw.get("custom") is not None:
            if not isinstance(raw["custom"], str):
                raise ToolError("custom must be a string")
            answer["custom"] = raw["custom"]
        answers.append(answer)
    if seen != set(by_id):
        raise ToolError("every question must have an answer; use an empty selected array to skip")
    return {"answers": answers}


class UserQuestionService:
    def __init__(self, responder: Optional[Callable[[Dict[str, object]], Dict[str, object]]] = None) -> None:
        self.responder = responder
        self._pending: Dict[str, PendingQuestion] = {}
        self._lock = threading.Lock()

    def ask(self, questions: List[Dict[str, object]], execution: Execution, timeout: Optional[float] = None) -> Dict[str, object]:
        session = execution.session()
        request_id = uuid.uuid4().hex
        payload = {
            "id": request_id,
            "callId": execution.call_id,
            "run_id": execution.run_id,
            "session_id": execution.session_id,
            "questions": questions,
        }
        if session is not None and execution.session_id is not None:
            session.append(execution.session_id, "question/asked", payload)
        if self.responder is not None:
            try:
                result = validate_answers(questions, self.responder({"questions": questions}))
            except RunCancelled:
                raise
            except Exception as exc:
                raise ToolError(f"user questions are unavailable: {exc}") from exc
        else:
            result = self._wait(payload, execution, timeout)
        if "answers" in result and session is not None and execution.session_id is not None:
            session.append(execution.session_id, "question/answered", {"id": request_id, "callId": execution.call_id, **result})
        execution.ctx.events.emit("question/answered", {"id": request_id, "session_id": execution.session_id, **result})
        return result

    def _wait(self, payload: Dict[str, object], execution: Execution, timeout: Optional[float]) -> Dict[str, object]:
        pending = PendingQuestion(
            str(payload["id"]),
            execution.call_id,
            execution.run_id,
            execution.session_id,
            payload["questions"],
            threading.Condition(),
            execution.session(),
        )
        with self._lock:
            self._pending[pending.id] = pending
        execution.ctx.events.emit("question/asked", payload)
        token = execution.cancel_token

        def wake() -> None:
            with pending.condition:
                pending.condition.notify_all()

        unregister = token.on_cancel(wake) if token is not None else lambda: None
        deadline = time.monotonic() + timeout if timeout is not None and timeout >= 0 else None
        try:
            with pending.condition:
                while pending.answers is None and not pending.cancelled:
                    if token is not None:
                        token.throw_if_cancelled()
                    if deadline is None:
                        pending.condition.wait()
                    else:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            pending.returned = True
                            return {"pending": True, "callId": execution.call_id, "message": PENDING_NOTICE}
                        pending.condition.wait(remaining)
                if pending.cancelled:
                    raise RunCancelled("user question service is closed")
                if token is not None:
                    token.throw_if_cancelled()
                return pending.answers
        finally:
            unregister()
            if not pending.returned:
                with self._lock:
                    self._pending.pop(pending.id, None)

    def list(self) -> List[Dict[str, object]]:
        with self._lock:
            return [
                {
                    "id": item.id,
                    "callId": item.call_id,
                    "run_id": item.run_id,
                    "session_id": item.session_id,
                    "questions": item.questions,
                    "timedOut": item.returned,
                }
                for item in self._pending.values()
            ]

    def answer(self, request_id: str, value: object) -> Dict[str, object]:
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is None:
            raise ValueError(f"unknown or settled question {request_id}")
        result = validate_answers(pending.questions, value)
        with pending.condition:
            if pending.answers is not None or pending.cancelled:
                raise ValueError("question is already closed")
            pending.answers = result
            pending.condition.notify_all()
        if pending.returned:
            if pending.session is not None and pending.session_id is not None:
                pending.session.append(
                    pending.session_id,
                    "user/message",
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                        "source": {
                            "kind": "answer_to_pending_question",
                            "callId": pending.call_id,
                            "questionId": pending.id,
                            "questions": pending.questions,
                        },
                    },
                )
            with self._lock:
                self._pending.pop(request_id, None)
        return result

    def close(self) -> None:
        with self._lock:
            pending_items = list(self._pending.values())
            self._pending.clear()
        for pending in pending_items:
            with pending.condition:
                pending.cancelled = True
                pending.condition.notify_all()


class UserQuestionsPlugin(Plugin):
    name = "core.user-questions"

    def __init__(self, responder: Optional[Callable[[Dict[str, object]], Dict[str, object]]] = None) -> None:
        self.responder = responder

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("userQuestions", UserQuestionService(self.responder), self.name)

    def deactivate(self, ctx: ServiceContext) -> None:
        service = ctx.require("userQuestions")
        assert isinstance(service, UserQuestionService)
        service.close()


class AskUserPlugin(Plugin):
    name = "tools.ask-user"
    requires = ("tools", "userQuestions")

    def __init__(self, timed: bool = False) -> None:
        self.timed = timed
        self.default_timeout = float(os.environ.get("MINIDSH_ASK_TIMEOUT_SECONDS", "120")) if timed else None
        if self.default_timeout is not None and self.default_timeout != -1 and self.default_timeout <= 0:
            raise ValueError("MINIDSH_ASK_TIMEOUT_SECONDS must be -1 or positive")

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        questions = ctx.require("userQuestions")
        assert isinstance(registry, ToolRegistry)
        assert isinstance(questions, UserQuestionService)
        properties: Dict[str, object] = {
            "questions": {
                "type": "array",
                "description": "Questions to ask the user before continuing.",
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "id": {"type": "string", "description": "Stable id for this question; echoed in the answer."},
                        "question": {"type": "string", "description": "The specific question to ask the user."},
                        "header": {"type": "string", "description": "Optional short heading for the question."},
                        "options": {
                            "type": "array",
                            "description": "Optional choices. Put the recommended choice first and append (Recommended).",
                            "items": {
                                "type": "object",
                                "additionalProperties": True,
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["label"],
                            },
                        },
                        "multi_select": {"type": "boolean"},
                    },
                    "required": ["id", "question"],
                },
            }
        }
        if self.timed:
            properties["timeout"] = {
                "type": "number",
                "description": f"Wait seconds for the whole answer batch (default {self.default_timeout}); -1 waits indefinitely.",
            }

        def ask(arguments: Dict[str, object], execution: Execution) -> str:
            normalized = validate_questions(arguments.get("questions"))
            timeout = self.default_timeout
            if self.timed and "timeout" in arguments:
                timeout = arguments["timeout"]
                if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or (timeout != -1 and timeout <= 0):
                    raise ToolError("timeout must be -1 or positive")
            return json.dumps(questions.ask(normalized, execution, timeout), ensure_ascii=False)

        registry.register(
            Tool(
                name="ask_user_question",
                description=TIMED_DESCRIPTION if self.timed else ASK_DESCRIPTION,
                parameters={"type": "object", "properties": properties, "required": ["questions"]},
                handler=ask,
            )
        )

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        assert isinstance(registry, ToolRegistry)
        registry.unregister("ask_user_question")
