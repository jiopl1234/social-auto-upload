"""Resource boundaries shared by the supported legacy account operations."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal, TypeVar

from orchestrator.errors import ResourceCleanupError
from orchestrator.ownership import AccountLease


T = TypeVar("T")


async def run_with_lease(lease: AccountLease, operation: Callable[[], Awaitable[T]]) -> T:
    """Clear a lease's resource marker only after its operation confirms cleanup."""
    lease.resources_started()
    result = await operation()
    lease.resources_closed()
    return result


async def close_browser_resources(
    context: Any | None,
    browser: Any,
    *,
    stage: Literal["COOKIE_CHECK", "LOGIN"],
    primary_error: BaseException | None = None,
    prior_error: BaseException | None = None,
) -> None:
    """Try every close and preserve the failure already leaving the operation."""
    errors: list[BaseException] = []
    if prior_error is not None:
        errors.append(prior_error)
    for resource in (context, browser):
        if resource is None:
            continue
        try:
            await resource.close()
        except BaseException as exc:
            errors.append(exc)
    if primary_error is not None or not errors:
        return
    for error in errors:
        if not isinstance(error, Exception):
            raise error
    for error in errors:
        if isinstance(error, ResourceCleanupError):
            raise error
    raise ResourceCleanupError(stage) from None
