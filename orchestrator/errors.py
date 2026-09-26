from __future__ import annotations

from typing import Literal

from orchestrator.diagnostics import diagnostic_error


class TaskError(Exception):
    """A public task failure with a stable category and optional input field."""

    def __init__(self, code: str, message: str, *, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def as_dict(self) -> dict:
        return diagnostic_error({"code": self.code, "message": self.message, "field": self.field})


class ResourceCleanupError(TaskError):
    """Browser shutdown was not confirmed; keep the account lease fail closed."""

    def __init__(self, stage: Literal["COOKIE_CHECK", "LOGIN"]):
        super().__init__("RESOURCES_UNCONFIRMED", "Browser resource shutdown could not be confirmed")
        self.stage = stage
