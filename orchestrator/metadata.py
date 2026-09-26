"""Compare metadata read back from a prepared work with its queued intent."""

from __future__ import annotations

from typing import Any, Mapping

from orchestrator.errors import TaskError


_FIELDS = frozenset({"title", "body", "tags", "media", "cover", "collection", "declarations"})
_OPTIONAL_EMPTY: dict[str, Any] = {"tags": [], "cover": None, "collection": None}


def _mismatch(field: str) -> TaskError:
    return TaskError("METADATA_MISMATCH", "Prepared work does not match the queued content", field=field)


def _declaration_mismatch(field: str) -> TaskError:
    return TaskError("DECLARATION_MISMATCH", "Prepared work does not match the requested declaration", field=field)


def _same_scalar(actual: Any, expected: Any) -> bool:
    return type(actual) is type(expected) and actual == expected


def _asset(actual: Any, expected: Mapping[str, Any], field: str) -> dict[str, str]:
    if not isinstance(actual, dict):
        raise _mismatch(field)
    unknown = sorted(set(actual) - {"kind", "sha256"}, key=str)
    if unknown:
        raise _mismatch(f"{field}.{unknown[0]}")
    for key in ("kind", "sha256"):
        if key not in actual or not _same_scalar(actual[key], expected[key]):
            raise _mismatch(f"{field}.{key}")
    return {"kind": actual["kind"], "sha256": actual["sha256"]}


def validate_prepared(manifest: Mapping[str, Any], actual: dict) -> tuple[dict, list[dict]]:
    """Require a complete, matching UI readback before the final action.

    Only a queued optional field explicitly allowed to be omitted may degrade
    to an empty value. The returned metadata records that actual empty value.
    """
    if not isinstance(actual, dict):
        raise _mismatch("actual_metadata")
    unknown = sorted(set(actual) - _FIELDS, key=str)
    if unknown:
        raise _mismatch(str(unknown[0]))

    expected: dict[str, Any] = {
        "title": manifest["title"], "body": manifest["body"],
        "tags": list(manifest.get("tags", ())),
        "media": [{"kind": item["kind"], "sha256": item["sha256"]} for item in manifest["media"]],
        "cover": ({"kind": manifest["cover"]["kind"], "sha256": manifest["cover"]["sha256"]}
                  if manifest.get("cover") is not None else None),
        "collection": manifest.get("collection"),
        "declarations": dict(manifest["declarations"]),
    }
    allow_omit = set(manifest["field_policy"]["allow_omit"])
    checked: dict[str, Any] = {}
    warnings: list[dict] = []
    for field in ("title", "body", "tags", "media", "cover", "collection", "declarations"):
        if field not in actual:
            if field not in allow_omit:
                raise _declaration_mismatch(field) if field == "declarations" else _mismatch(field)
            checked[field] = [] if field == "tags" else None
            warnings.append(TaskError("FIELD_OMITTED", "Optional field was omitted by the platform", field=field).as_dict())
            continue
        value = actual[field]
        if field in _OPTIONAL_EMPTY and field in allow_omit:
            empty = value is None or (field == "tags" and value == []) or (field == "collection" and value == "")
            if empty and (value != expected[field] or type(value) is not type(expected[field])):
                checked[field] = [] if field == "tags" else None
                warnings.append(TaskError("FIELD_OMITTED", "Optional field was omitted by the platform", field=field).as_dict())
                continue
        if field in {"title", "body", "collection"}:
            if not _same_scalar(value, expected[field]):
                raise _mismatch(field)
            checked[field] = value
        elif field == "tags":
            if not isinstance(value, list) or value != expected[field] or any(not isinstance(tag, str) for tag in value):
                raise _mismatch(field)
            checked[field] = list(value)
        elif field == "media":
            if not isinstance(value, list) or len(value) != len(expected[field]):
                raise _mismatch(field)
            checked[field] = [_asset(item, expected[field][index], f"media[{index}]")
                              for index, item in enumerate(value)]
        elif field == "cover":
            if expected[field] is None:
                if value is not None:
                    raise _mismatch(field)
                checked[field] = None
            else:
                checked[field] = _asset(value, expected[field], field)
        else:
            if not isinstance(value, dict):
                raise _declaration_mismatch(field)
            keys = set(value) | set(expected[field])
            for key in sorted(keys, key=str):
                if key not in value or key not in expected[field] or not _same_scalar(value[key], expected[field][key]):
                    raise _declaration_mismatch(f"declarations.{key}")
            checked[field] = dict(value)
    return checked, warnings
