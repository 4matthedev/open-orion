"""Structured LLM output contract and parser.

The model replies with a single JSON object that the control loop interprets.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal["run", "read", "ls", "screenshot", "remember",
                    "ask", "done"] = "ask"
    command: str = Field(default="")
    path: str = Field(default="")
    message: str = Field(default="")
    reasoning: str = Field(default="")
    explanation: str = Field(default="")
    risk: Literal["safe", "medium", "high"] = "safe"
    requires_confirmation: bool = False


_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?", re.MULTILINE)
_FENCE_END_RE = re.compile(r"```\s*$", re.MULTILINE)


def parse_action(text: str) -> ActionRequest:
    """Parse a model reply into an ActionRequest. Raises ValueError on failure."""
    data = _extract_json(text)
    try:
        return ActionRequest(**data)
    except ValidationError as exc:
        raise ValueError(f"invalid action schema: {exc.errors()}") from exc


def _extract_json(text: str) -> dict:
    cleaned = _FENCE_RE.sub("", text.strip())
    cleaned = _FENCE_END_RE.sub("", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    candidate = cleaned[start : end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model output is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("model output JSON is not an object")
    return data
