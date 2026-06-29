"""Tiny stdlib arg validator for strict enablement tool schemas.

Used by registry.dispatch_tool to validate dispatched args against a
tool's declared MCP ``inputSchema`` BEFORE the handler runs. Only the
enablement tools carry a strict schema (additionalProperties:false +
explicit per-property types + a required list); every other tool — and
the whole Gemini text-loop — passes through untouched.

Pure stdlib (no jsonschema). Supports the small subset the enablement
schemas actually use: object schemas with typed properties, a required
list, and additionalProperties:false. Unknown/absent schemas are not
enforced here — see ``is_strict``.
"""

from __future__ import annotations

from typing import Any

# JSON-schema type name -> Python types accepted for it. 'string' requires a
# real str (an int/list/dict for a string field IS rejected); integer/number
# are handled specially in _type_ok so a quoted number ("5") also passes —
# small models routinely emit numbers as strings and the handlers coerce them,
# so rejecting that at dispatch would hurt reliability without catching a real
# bug. Non-numeric strings ("five") for a numeric field still fail.
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


def _is_int_str(s: str) -> bool:
    s = s.strip()
    body = s[1:] if s[:1] in "+-" else s
    return bool(body) and body.isdigit()


def _is_float_str(s: str) -> bool:
    try:
        float(s.strip())
        return True
    except ValueError:
        return False


def is_strict(schema: dict | None) -> bool:
    """A schema is 'strict' (enforceable) only when it opts in via
    ``additionalProperties: false``. Tools without a schema, or with an
    open schema, are intentionally NOT enforced so legacy/text-loop call
    shapes keep working unchanged."""
    if not isinstance(schema, dict):
        return False
    return schema.get("additionalProperties") is False


def _type_ok(value: Any, declared: Any) -> bool:
    """True when ``value`` matches the declared JSON-schema type.

    Strings/arrays/objects/booleans must match exactly. For ``integer`` /
    ``number`` we also accept a numeric *string* (e.g. ``"5"``) — a common
    small-model quirk the handlers already coerce — but never a boolean
    (``True`` is not an integer here) nor a non-numeric string.
    """
    if declared is None:
        return True
    # bool is a subclass of int — only valid where 'boolean' is declared.
    if isinstance(value, bool):
        return declared == "boolean"
    if declared == "integer":
        return isinstance(value, int) or (isinstance(value, str) and _is_int_str(value))
    if declared == "number":
        return isinstance(value, (int, float)) or (isinstance(value, str) and _is_float_str(value))
    types = _TYPE_MAP.get(declared)
    if types is None:
        return True  # unknown declared type → don't block
    return isinstance(value, types)


def validate_args(schema: dict | None, args: dict) -> tuple[bool, str | None]:
    """Validate ``args`` against a strict object ``schema``.

    Returns ``(ok, error)``. ``ok`` is True (and error None) when the
    schema is absent/non-strict — non-strict schemas are not enforced.
    For a strict schema it checks: every required key present, no unknown
    keys (additionalProperties:false), and each provided value matches its
    declared type. Returns the first violation as a short message.
    """
    if not is_strict(schema):
        return True, None
    if not isinstance(args, dict):
        return False, "args must be an object"

    properties = schema.get("properties") or {}
    required = schema.get("required") or []

    for key in required:
        if key not in args:
            return False, f"missing required argument: {key}"

    for key, value in args.items():
        if key not in properties:
            return False, f"unknown argument: {key}"
        if value is None:
            continue  # an explicit null for an optional arg is benign
        declared = (properties.get(key) or {}).get("type")
        if not _type_ok(value, declared):
            return False, f"argument {key} must be of type {declared}"

    return True, None
