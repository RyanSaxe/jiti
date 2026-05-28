"""Per-generation transcript: ordered JSONL events on disk."""

import json
from pathlib import Path
from types import SimpleNamespace

from jiti.transcript import Recorder, transcript_path


def test_records_turn_tool_call_and_tool_result_in_order(tmp_path):
    recorder = Recorder()
    usage = SimpleNamespace(input_tokens=120, output_tokens=45)
    text_block = SimpleNamespace(type="text", text="thinking out loud")

    recorder.turn(1, 1.234, usage, 0.000123, [text_block])
    recorder.tool_call("grep", {"pattern": "needle"})
    recorder.tool_result("grep", "sample.py:1:def needle():")

    path = tmp_path / "transcripts" / "pkg" / "mod" / "fn.jsonl"
    recorder.write(path)
    events = [json.loads(line) for line in path.read_text().splitlines()]

    assert [event["type"] for event in events] == ["turn", "tool_call", "tool_result"]
    assert events[0] == {
        "type": "turn",
        "n": 1,
        "seconds": 1.234,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 45,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        "cost": 0.000123,
        "text": "thinking out loud",
    }
    assert events[1] == {"type": "tool_call", "name": "grep", "input": {"pattern": "needle"}}
    assert events[2]["output"] == "sample.py:1:def needle():"


def test_write_creates_parent_directories(tmp_path):
    recorder = Recorder()
    recorder.tool_call("grep", {"pattern": "x"})

    nested = tmp_path / "a" / "b" / "c" / "out.jsonl"
    recorder.write(nested)

    assert nested.exists()


def test_transcript_path_mirrors_module_as_directories(tmp_path):
    path = transcript_path(tmp_path, "examples.semver.core", "parse")

    assert path == tmp_path / "transcripts" / "examples" / "semver" / "core" / "parse.jsonl"


def test_turn_extracts_only_text_blocks_skipping_tool_use(tmp_path):
    recorder = Recorder()
    text = SimpleNamespace(type="text", text="reasoning")
    tool_use = SimpleNamespace(type="tool_use", name="grep", input={"pattern": "x"})

    recorder.turn(2, 0.5, None, 0.0, [text, tool_use])
    path = tmp_path / "t.jsonl"
    recorder.write(path)

    event = json.loads(Path(path).read_text().splitlines()[0])
    assert event["text"] == "reasoning"
    assert event["usage"] == {}
