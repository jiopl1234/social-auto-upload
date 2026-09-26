from __future__ import annotations


class TaskError(Exception):
    """A public task failure with a stable category and optional input field."""

    def __init__(self, code: str, message: str, *, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "field": self.field}
