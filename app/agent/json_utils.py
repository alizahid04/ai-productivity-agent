"""Robust extraction of a JSON object from raw LLM text output.

Small/free-tier models frequently wrap JSON in markdown fences, prepend
commentary ("Sure, here's the JSON:"), or add trailing notes after the
object. This helper finds the first balanced `{...}` block in the text
(brace-counting, aware of quoted strings) rather than assuming the whole
response — or the whole response minus a fence — is valid JSON.
"""
from __future__ import annotations

import json


class JSONExtractionError(ValueError):
    pass


def extract_json_object(text: str) -> dict:
    """Find and parse the first balanced JSON object in `text`.

    Raises JSONExtractionError with a clear message if none is found or it
    doesn't parse, so callers can feed that back to the model for a retry.
    """
    if not text or not text.strip():
        raise JSONExtractionError("Model returned an empty response.")

    start = text.find("{")
    if start == -1:
        raise JSONExtractionError("No JSON object found in the model's response.")

    depth = 0
    in_string = False
    escape = False
    end = None

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end is None:
        raise JSONExtractionError("Model's JSON object was truncated or unbalanced.")

    candidate = text[start:end]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise JSONExtractionError(f"Extracted text was not valid JSON: {e}") from e
