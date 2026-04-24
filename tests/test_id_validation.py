"""Tests for IdStr / _validate_id (folder-name & id restrictions)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from pydantic import ValidationError

from cryoet_schema import Sample, Tomogram
from cryoet_schema.schema import _validate_id
from scripts.validate import validate_dir


# ── direct unit tests of _validate_id ────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "Position_86",
        "bp_3dctf_bin4",
        "membrain_seg_v10",
        "gouauxlab_20241211_HippWaffle",
        "a",
        "A1",
        "x.y",
        "foo-bar_baz.v2",
        "a" * 128,
    ],
)
def test_valid_ids(value):
    assert _validate_id(value) == value


@pytest.mark.parametrize(
    "value,reason",
    [
        ("", "empty"),
        ("a" * 129, "too long"),
        # leading characters
        (".hidden", "leading dot"),
        ("-flag", "leading hyphen"),
        ("_underscore", "leading underscore"),
        # trailing characters
        ("trailing.", "trailing dot"),
        ("trailing-", "trailing hyphen"),
        # path / traversal
        ("foo/bar", "forward slash"),
        ("foo\\bar", "backslash"),
        ("..", "double dot"),
        ("foo..bar", "embedded double dot"),
        # whitespace
        ("has space", "space"),
        ("tab\there", "tab"),
        ("new\nline", "newline"),
        # shell metacharacters
        ("star*", "asterisk"),
        ("question?", "question mark"),
        ("bracket[", "bracket"),
        ("brace{", "brace"),
        ("tilde~", "tilde"),
        ("dollar$", "dollar"),
        ("back`tick", "backtick"),
        ("bang!", "exclamation"),
        ("pipe|", "pipe"),
        ("amp&", "ampersand"),
        ("semi;", "semicolon"),
        ("lt<", "less-than"),
        ("gt>", "greater-than"),
        ("paren(", "paren"),
        ("double\"quote", "double quote"),
        ("single'quote", "single quote"),
        # URL-reserved
        ("percent%", "percent"),
        ("hash#", "hash"),
        ("query?x", "question mark"),
        ("plus+1", "plus"),
        ("at@sym", "at sign"),
        ("colon:x", "colon"),
        ("eq=val", "equals"),
        # control characters
        ("ctrl\x00char", "null byte"),
        ("ctrl\x1fchar", "control char"),
        ("del\x7fchar", "DEL"),
        # non-ASCII
        ("café", "non-ASCII letter"),
        ("naïve", "diaeresis"),
        ("zero​width", "zero-width space"),
        # comma, not in allowlist
        ("a,b", "comma"),
    ],
)
def test_invalid_ids(value, reason):
    with pytest.raises(ValueError):
        _validate_id(value)


@pytest.mark.parametrize(
    "value",
    ["CON", "con", "PRN", "aux", "NUL", "COM1", "com9", "LPT1", "lpt9"],
)
def test_windows_reserved_names_rejected(value):
    with pytest.raises(ValueError, match="reserved"):
        _validate_id(value)


def test_non_string_rejected():
    with pytest.raises(ValueError):
        _validate_id(123)  # type: ignore[arg-type]


# ── integration through Pydantic models ──────────────────────────────────────


def test_sample_accepts_valid_sample_id():
    s = Sample(sample_id="good_sample_01", data_source="cryoet", project="chromatin")
    assert s.sample_id == "good_sample_01"


def test_sample_rejects_bad_sample_id():
    with pytest.raises(ValidationError):
        Sample(sample_id="bad name", data_source="cryoet", project="chromatin")


def test_sample_allows_none_sample_id():
    s = Sample(data_source="cryoet", project="chromatin")
    assert s.sample_id is None


def test_tomogram_rejects_bad_id_alias():
    with pytest.raises(ValidationError):
        Tomogram.model_validate({"id": "has space"})


def test_tomogram_rejects_bad_derived_from():
    with pytest.raises(ValidationError):
        Tomogram.model_validate({"id": "tomo_001", "derived_from": ["also/bad"]})


# ── integration through validate_dir ──────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).lstrip())


def test_bad_sample_folder_name(tmp_path):
    bad = tmp_path / "bad name"
    _write(
        bad / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        project = "chromatin"
        """,
    )
    record, errors, _ = validate_dir(bad)
    assert record is None
    assert any("sample_id" in e for e in errors)


def test_bad_acquisition_folder_name(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        project = "chromatin"
        """,
    )
    _write(tmp_path / "Position*86" / "acquisition.toml", "[acquisition]\n")
    record, errors, _ = validate_dir(tmp_path)
    assert record is None
    assert any("acquisition_id" in e for e in errors)


def test_acquisition_case_insensitive_collision(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        project = "chromatin"
        """,
    )
    _write(tmp_path / "Position_86" / "acquisition.toml", "[acquisition]\n")
    _write(tmp_path / "position_86" / "acquisition.toml", "[acquisition]\n")
    record, errors, _ = validate_dir(tmp_path)
    assert record is None
    assert any("collides case-insensitively" in e and "acquisition" in e for e in errors)


def test_tomogram_case_insensitive_collision(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        project = "chromatin"
        """,
    )
    _write(
        tmp_path / "acq1" / "acquisition.toml",
        """
        [acquisition]

        [[tomogram]]
        id = "tomo_001"

        [[tomogram]]
        id = "Tomo_001"
        """,
    )
    record, errors, _ = validate_dir(tmp_path)
    assert record is None
    assert any("collides case-insensitively" in e and "tomogram" in e for e in errors)


def test_annotation_case_insensitive_collision(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        project = "chromatin"
        """,
    )
    _write(
        tmp_path / "acq1" / "acquisition.toml",
        """
        [acquisition]

        [[tomogram]]
        id = "tomo_001"

        [[annotation]]
        id = "ann_001"

        [[annotation]]
        id = "ANN_001"
        """,
    )
    record, errors, _ = validate_dir(tmp_path)
    assert record is None
    assert any("collides case-insensitively" in e and "annotation" in e for e in errors)


def test_tomogram_and_annotation_sharing_id_is_allowed(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        project = "chromatin"
        """,
    )
    _write(
        tmp_path / "acq1" / "acquisition.toml",
        """
        [acquisition]

        [[tomogram]]
        id = "shared_id"

        [[annotation]]
        id = "shared_id"
        """,
    )
    record, errors, _ = validate_dir(tmp_path)
    assert errors == []
    assert record is not None


def test_bad_tomogram_id_in_toml(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        project = "chromatin"
        """,
    )
    _write(
        tmp_path / "acq1" / "acquisition.toml",
        """
        [acquisition]

        [[tomogram]]
        id = "has space"
        """,
    )
    record, errors, _ = validate_dir(tmp_path)
    assert record is None
    assert any("tomogram" in e.lower() and "id" in e.lower() for e in errors)
