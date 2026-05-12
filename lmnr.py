"""Local no-op subset of lmnr used by the public benchmark runners.

The remote benchmark runner can attach traces to Laminar. Public
verification writes local JSON artifacts instead, so these hooks are inert.
"""

from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def observe(*args: Any, **kwargs: Any):
    if args and callable(args[0]) and len(args) == 1 and not kwargs:
        return args[0]

    def decorator(fn: F) -> F:
        return fn

    return decorator


class Laminar:
    @staticmethod
    def initialize(*args: Any, **kwargs: Any) -> None:
        return None

    @staticmethod
    def serialize_span_context() -> None:
        return None

    @staticmethod
    def get_trace_id() -> None:
        return None


class LaminarClient:
    pass
