from datetime import datetime
from typing import Dict

from minidsh.tools import Execution, SingleToolPlugin, Tool


def current_time(arguments: Dict[str, object], execution: Execution) -> str:
    if arguments:
        raise ValueError("current_time 不接受参数")
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ClockPlugin(SingleToolPlugin):
    def __init__(self) -> None:
        super().__init__(
            "example.clock",
            Tool(
                name="current_time",
                description="Get the current local date, time, and UTC offset.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                handler=current_time,
            ),
            (
                "tool:example-clock",
                9000,
                "Use the current_time tool when the user asks for the current local date or time.",
            ),
        )
