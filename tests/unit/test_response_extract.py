"""Tests for API response field extraction."""
from __future__ import annotations

from pa_agent.ai.response_extract import content_from_response, reasoning_from_response


def test_reasoning_from_flat_cursor_shape() -> None:
    raw = {"content": "{}", "reasoning_content": "思考片段", "model": "auto"}
    assert reasoning_from_response(raw) == "思考片段"


def test_reasoning_from_openai_choices_shape() -> None:
    raw = {
        "choices": [{"message": {"content": "{}", "reasoning_content": "nested"}}],
    }
    assert reasoning_from_response(raw) == "nested"


def test_content_from_flat_shape() -> None:
    raw = {"content": '{"ok":true}', "reasoning_content": "x"}
    assert content_from_response(raw) == '{"ok":true}'


def test_reasoning_from_flat_record_response() -> None:
    text = "思考片段"
    assert reasoning_from_response(
        {"id": "x", "content": "{}", "reasoning_content": text}
    ) == text


def test_reasoning_from_persisted_openai_choices_shape() -> None:
    assert reasoning_from_response(
        {"choices": [{"message": {"reasoning_content": "abc", "content": "{}"}}]}
    ) == "abc"


def test_reasoning_prefers_top_level_when_both_present() -> None:
    assert reasoning_from_response(
        {
            "reasoning_content": "top",
            "choices": [{"message": {"reasoning_content": "nested"}}],
        }
    ) == "top"
