"""Zero-dependency strict JSON Schema validator for OSRS_ee normative contracts."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "schemas"


class SchemaValidationError(ValueError):
    """Raised when a payload violates a normative JSON schema."""


@lru_cache(maxsize=16)
def load_schema(schema_filename: str) -> dict[str, Any]:
    path = SCHEMAS_DIR / schema_filename
    if not path.is_file():
        raise FileNotFoundError(f"Schema file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return False


def validate_against_schema(data: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Recursively validate `data` against `schema` with strict type and property checks."""
    expected_type = schema.get("type")
    if expected_type is not None:
        allowed_types = [expected_type] if isinstance(expected_type, str) else list(expected_type)
        if not any(_matches_type(data, t) for t in allowed_types):
            raise SchemaValidationError(
                f"{path}: expected type {allowed_types}, got {type(data).__name__} ({data!r})"
            )

    if data is None:
        return

    if "const" in schema and data != schema["const"]:
        raise SchemaValidationError(f"{path}: expected const {schema['const']!r}, got {data!r}")

    if "enum" in schema and data not in schema["enum"]:
        raise SchemaValidationError(f"{path}: value {data!r} not in allowed enum {schema['enum']}")

    if isinstance(data, str):
        if "minLength" in schema and len(data) < schema["minLength"]:
            raise SchemaValidationError(f"{path}: string shorter than minLength={schema['minLength']}")
        if "pattern" in schema and not re.match(schema["pattern"], data):
            raise SchemaValidationError(f"{path}: string {data!r} does not match {schema['pattern']!r}")

    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if "minimum" in schema and data < schema["minimum"]:
            raise SchemaValidationError(f"{path}: {data} < minimum {schema['minimum']}")
        if "maximum" in schema and data > schema["maximum"]:
            raise SchemaValidationError(f"{path}: {data} > maximum {schema['maximum']}")

    if isinstance(data, list):
        if "minItems" in schema and len(data) < schema["minItems"]:
            raise SchemaValidationError(f"{path}: list length < minItems {schema['minItems']}")
        item_schema = schema.get("items")
        if item_schema:
            for idx, item in enumerate(data):
                validate_against_schema(item, item_schema, f"{path}[{idx}]")

    if isinstance(data, dict):
        required = schema.get("required", [])
        for req_key in required:
            if req_key not in data:
                raise SchemaValidationError(f"{path}: missing required field '{req_key}'")

        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(data.keys()) - set(props.keys())
            if extra:
                raise SchemaValidationError(f"{path}: unexpected additional properties {sorted(extra)}")

        for key, val in data.items():
            if key in props:
                validate_against_schema(val, props[key], f"{path}.{key}")
