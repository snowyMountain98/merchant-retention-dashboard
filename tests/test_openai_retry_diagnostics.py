import json
from types import SimpleNamespace

import pytest

from src.analysis.generate_openai_insights import call_structured

SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"headline": {"type": "string"}}, "required": ["headline"]}

class FakeResponses:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

class FakeClient:
    def __init__(self, replies):
        self.responses = FakeResponses(replies)


def response(text=None, status="completed", incomplete_details=None, output=None):
    return SimpleNamespace(output_text=text, status=status, incomplete_details=incomplete_details, output=output or [], usage=SimpleNamespace(output_tokens=0))


def test_retries_empty_response_with_low_reasoning_and_larger_output_limit():
    client = FakeClient([
        response(text=None, status="incomplete", incomplete_details={"reason": "max_output_tokens"}),
        response(text=json.dumps({"headline": "매출 추이 확인"})),
    ])
    result = call_structured(client, "test-model", "instructions", {"test": 1}, "test_schema", SCHEMA, 100)
    assert result == {"headline": "매출 추이 확인"}
    assert len(client.responses.calls) == 2
    assert client.responses.calls[0]["reasoning"] == {"effort": "low"}
    assert client.responses.calls[0]["max_output_tokens"] == 100
    assert client.responses.calls[1]["max_output_tokens"] == 200
    assert client.responses.calls[0]["text"]["verbosity"] == "low"


def test_empty_response_error_has_safe_diagnostics_only():
    client = FakeClient([
        response(text=None, status="incomplete", incomplete_details={"reason": "max_output_tokens"}),
        response(text=None, status="incomplete", incomplete_details={"reason": "max_output_tokens"}),
    ])
    with pytest.raises(RuntimeError) as exc:
        call_structured(client, "test-model", "instructions", {"private": "do-not-log"}, "test_schema", SCHEMA, 100)
    message = str(exc.value)
    assert "status=incomplete" in message and "max_output_tokens" in message
    assert "do-not-log" not in message


def test_refusal_is_not_retried():
    refusal_content = SimpleNamespace(type="refusal")
    refusal_item = SimpleNamespace(type="message", content=[refusal_content])
    client = FakeClient([response(text=None, output=[refusal_item])])
    with pytest.raises(RuntimeError, match="refusal"):
        call_structured(client, "test-model", "instructions", {}, "test_schema", SCHEMA, 100)
    assert len(client.responses.calls) == 1
