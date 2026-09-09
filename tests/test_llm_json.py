"""Weaker models answer correctly inside an inconvenient wrapper."""

import json

from catalogbot.llm import extract_json


def test_plain_json_passes_through():
    assert json.loads(extract_json('{"verdict": "actionable"}'))["verdict"] == "actionable"


def test_json_after_reasoning_prose_is_recovered():
    reply = (
        "Here's a thinking process for this message. The sender asks for a fix, "
        "so it is actionable. Action:\n"
        '{"verdict": "actionable", "confidence": 0.9}'
    )
    assert json.loads(extract_json(reply))["confidence"] == 0.9


def test_fenced_json_is_recovered():
    assert json.loads(extract_json('```json\n{"verdict": "not_a_task"}\n```'))


def test_trailing_commentary_is_dropped():
    reply = '{"verdict": "ambiguous"}\n\nLet me know if you want me to expand.'
    assert json.loads(extract_json(reply))["verdict"] == "ambiguous"


def test_braces_inside_strings_do_not_end_the_object():
    reply = 'Thinking...\n{"task": "fix the {weird} title", "verdict": "actionable"}'
    assert json.loads(extract_json(reply))["task"] == "fix the {weird} title"


def test_nested_objects_are_kept_whole():
    reply = 'Reasoning first.\n{"verdict": "actionable", "draft": {"task": "x"}}'
    assert json.loads(extract_json(reply))["draft"]["task"] == "x"
