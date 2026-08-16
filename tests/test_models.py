"""Unit tests for the JSON action contract parser."""

import pydantic
import pytest

from orion.models import ActionRequest, parse_action


def test_parse_bare_json():
    action = parse_action('{"action": "run", "command": "echo hi"}')
    assert action.action == "run"
    assert action.command == "echo hi"


def test_parse_fenced_json():
    text = "```json\n{\"action\": \"ask\", \"message\": \"hi\"}\n```"
    assert parse_action(text).action == "ask"


def test_parse_json_embedded_in_prose():
    text = "Here you go: {\"action\": \"done\", \"message\": \"All set.\"} thanks!"
    assert parse_action(text).message == "All set."


def test_parse_ignores_unknown_keys():
    action = parse_action('{"action": "run", "command": "echo hi", "hacked": 1}')
    assert action.command == "echo hi"
    assert not hasattr(action, "hacked")


def test_parse_defaults():
    action = parse_action('{"action": "ask"}')
    assert action.action == "ask"
    assert action.command == ""
    assert action.risk == "safe"
    assert action.requires_confirmation is False


def test_parse_invalid_action_value():
    with pytest.raises(ValueError):
        parse_action('{"action": "fly"}')


def test_parse_not_json():
    with pytest.raises(ValueError):
        parse_action("just some plain prose")


def test_parse_raises_on_missing_object():
    with pytest.raises(ValueError):
        parse_action("no braces here")


def test_action_request_validates_bad_fields():
    with pytest.raises(pydantic.ValidationError):
        ActionRequest(command=1)
