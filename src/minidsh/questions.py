import json
import threading
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .cancellation import RunCancelled
from .plugins import Plugin, ServiceContext
from .tools import Execution, Tool, ToolError, ToolRegistry


SKIPPED_ANSWER_GUIDANCE = "An answer item with empty selected and no custom means the user skipped that question: continue without it, do not ask it again, and do not treat the skip as permission."
ASK_DESCRIPTION = "Ask the user a concise question when you need confirmation, a choice, or missing information before proceeding. " + SKIPPED_ANSWER_GUIDANCE


@dataclass
class PendingQuestion:
    id: str
    call_id: Optional[str]
    run_id: Optional[str]
    session_id: Optional[str]
    questions: List[Dict[str, object]]
    condition: threading.Condition
    answers: Optional[Dict[str, object]] = None
    cancelled: bool = False


def validate_questions(value: object) -> List[Dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise ToolError("questions 必须是非空数组")
    questions: List[Dict[str, object]] = []
    ids = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ToolError("question 必须是对象")
        question_id = raw.get("id")
        text = raw.get("question")
        if not isinstance(question_id, str) or not question_id or question_id in ids:
            raise ToolError("question id 必须非空且在本次调用中唯一")
        if not isinstance(text, str) or not text.strip():
            raise ToolError("question 必须是非空字符串")
        ids.add(question_id)
        question: Dict[str, object] = {"id": question_id, "question": text}
        if raw.get("header") is not None:
            if not isinstance(raw["header"], str):
                raise ToolError("header 必须是字符串")
            question["header"] = raw["header"]
        options = raw.get("options", [])
        if not isinstance(options, list):
            raise ToolError("options 必须是数组")
        normalized_options = []
        labels = set()
        for option in options:
            if not isinstance(option, dict) or not isinstance(option.get("label"), str) or not option["label"]:
                raise ToolError("option label 必须是非空字符串")
            if option["label"] in labels:
                raise ToolError("option label 不能重复")
            labels.add(option["label"])
            normalized = {"label": option["label"]}
            if option.get("description") is not None:
                if not isinstance(option["description"], str):
                    raise ToolError("option description 必须是字符串")
                normalized["description"] = option["description"]
            normalized_options.append(normalized)
        if normalized_options:
            question["options"] = normalized_options
        multi_select = raw.get("multi_select", False)
        if not isinstance(multi_select, bool):
            raise ToolError("multi_select 必须是布尔值")
        question["multi_select"] = multi_select
        questions.append(question)
    return questions


def validate_answers(questions: List[Dict[str, object]], value: object) -> Dict[str, object]:
    if not isinstance(value, dict) or not isinstance(value.get("answers"), list):
        raise ToolError("answers 必须是数组")
    by_id = {str(question["id"]): question for question in questions}
    answers: List[Dict[str, object]] = []
    seen = set()
    for raw in value["answers"]:
        if not isinstance(raw, dict) or raw.get("id") not in by_id or raw["id"] in seen:
            raise ToolError("answer id 必须匹配且不能重复")
        question = by_id[raw["id"]]
        seen.add(raw["id"])
        selected = raw.get("selected", [])
        if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
            raise ToolError("selected 必须是字符串数组")
        labels = {option["label"] for option in question.get("options", [])}
        if any(item not in labels for item in selected):
            raise ToolError("selected 包含未知选项")
        if not question.get("multi_select") and len(selected) > 1:
            raise ToolError("单选问题不能选择多个选项")
        answer: Dict[str, object] = {"id": raw["id"], "selected": selected}
        if raw.get("custom") is not None:
            if not isinstance(raw["custom"], str):
                raise ToolError("custom 必须是字符串")
            answer["custom"] = raw["custom"]
        answers.append(answer)
    if seen != set(by_id):
        raise ToolError("每个问题都必须有 answer；跳过时使用空 selected")
    return {"answers": answers}


class UserQuestionService:
    def __init__(self, responder: Optional[Callable[[Dict[str, object]], Dict[str, object]]] = None) -> None:
        self.responder = responder
        self._pending: Dict[str, PendingQuestion] = {}
        self._lock = threading.Lock()

    def ask(self, questions: List[Dict[str, object]], execution: Execution) -> Dict[str, object]:
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
                raise ToolError(f"用户提问不可用: {exc}") from exc
        else:
            result = self._wait(payload, execution)
        if session is not None and execution.session_id is not None:
            session.append(execution.session_id, "question/answered", {"id": request_id, "callId": execution.call_id, **result})
        return result

    def _wait(self, payload: Dict[str, object], execution: Execution) -> Dict[str, object]:
        pending = PendingQuestion(
            str(payload["id"]),
            execution.call_id,
            execution.run_id,
            execution.session_id,
            payload["questions"],
            threading.Condition(),
        )
        with self._lock:
            self._pending[pending.id] = pending
        execution.ctx.events.emit("question/asked", payload)
        token = execution.cancel_token

        def wake() -> None:
            with pending.condition:
                pending.condition.notify_all()

        unregister = token.on_cancel(wake) if token is not None else lambda: None
        try:
            with pending.condition:
                while pending.answers is None and not pending.cancelled:
                    if token is not None:
                        token.throw_if_cancelled()
                    pending.condition.wait()
                if pending.cancelled:
                    raise RunCancelled("用户提问服务已关闭")
                if token is not None:
                    token.throw_if_cancelled()
                return pending.answers
        finally:
            unregister()
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
                raise ValueError("问题已经结束")
            pending.answers = result
            pending.condition.notify_all()
        return result

    def close(self) -> None:
        with self._lock:
            pending_items = list(self._pending.values())
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

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        questions = ctx.require("userQuestions")
        assert isinstance(registry, ToolRegistry)
        assert isinstance(questions, UserQuestionService)

        def ask(arguments: Dict[str, object], execution: Execution) -> str:
            normalized = validate_questions(arguments.get("questions"))
            return json.dumps(questions.ask(normalized, execution), ensure_ascii=False)

        registry.register(
            Tool(
                name="ask_user_question",
                description=ASK_DESCRIPTION,
                parameters={
                    "type": "object",
                    "properties": {
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
                    },
                    "required": ["questions"],
                },
                handler=ask,
            )
        )

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        assert isinstance(registry, ToolRegistry)
        registry.unregister("ask_user_question")
