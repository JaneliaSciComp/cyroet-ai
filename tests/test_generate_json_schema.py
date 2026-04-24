"""Tests for scripts/generate_json_schema.py and the committed schema.json."""

from __future__ import annotations

import json
from pathlib import Path

from cryoet_schema import SampleRecord
from scripts.generate_json_schema import _DEFAULT_OUT, main


def test_writes_valid_json_schema_to_given_path(tmp_path, capsys):
    out = tmp_path / "schema.json"
    rc = main([str(out)])
    assert rc == 0
    assert out.is_file()
    loaded = json.loads(out.read_text())
    assert loaded == SampleRecord.model_json_schema()
    assert "wrote" in capsys.readouterr().out


def test_creates_parent_directories(tmp_path):
    out = tmp_path / "nested" / "dir" / "schema.json"
    rc = main([str(out)])
    assert rc == 0
    assert out.is_file()
    json.loads(out.read_text())


def test_committed_schema_matches_pydantic_models():
    """Guard against drift between cryoet_schema/schema.json and SampleRecord.

    Regenerate with: `pixi run json-schema`.
    """
    committed = json.loads(Path(_DEFAULT_OUT).read_text())
    expected = SampleRecord.model_json_schema()
    assert committed == expected, (
        "cryoet_schema/schema.json is out of sync with SampleRecord. "
        "Run `pixi run json-schema` to regenerate."
    )
