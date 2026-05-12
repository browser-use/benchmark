"""No-op Laminar compatibility layer for public local verification."""

from typing import Any


class LaminarService:
    @classmethod
    def initialize(cls) -> bool:
        return False

    @classmethod
    def is_enabled(cls) -> bool:
        return False

    @classmethod
    def create_evaluation(cls, *args: Any, **kwargs: Any) -> None:
        return None

    @classmethod
    def attach_evaluation(cls, eval_id: str) -> None:
        return None

    @classmethod
    def get_eval_id(cls) -> None:
        return None

    @classmethod
    def get_eval_url(cls) -> None:
        return None

    @classmethod
    def create_datapoint(cls, task: dict[str, Any]) -> None:
        return None

    @classmethod
    def set_datapoint_score(
        cls,
        datapoint_id: str | None,
        score: int,
        final_result: str,
        agent_steps: list[str],
        metrics: dict[str, Any],
        judgement: dict[str, Any],
    ) -> None:
        return None

    @classmethod
    def set_datapoint_error(cls, datapoint_id: str | None, error_msg: str) -> None:
        return None
