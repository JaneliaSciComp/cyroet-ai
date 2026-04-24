"""Tests for validate.py."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from scripts.validate import validate_dir, main


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).lstrip())


def _minimal_sample(root: Path, *, project: str = "chromatin") -> Path:
    _write(
        root / "sample.toml",
        f"""
        [sample]
        data_source = "cryoet"
        project = "{project}"
        """,
    )
    return root


def _minimal_acquisition(root: Path, name: str = "acq1") -> Path:
    acq_dir = root / name
    _write(acq_dir / "acquisition.toml", "[acquisition]\n")
    return acq_dir


# ── validate_dir ──────────────────────────────────────────────────────


def test_missing_sample_toml(tmp_path):
    record, errors, warnings = validate_dir(tmp_path)
    assert record is None
    assert any("missing sample.toml" in e for e in errors)
    assert warnings == []


def test_sample_toml_parse_error(tmp_path):
    (tmp_path / "sample.toml").write_text("this is = = not valid toml\n")
    record, errors, warnings = validate_dir(tmp_path)
    assert record is None
    assert any("TOML parse error" in e for e in errors)


def test_acquisition_toml_parse_error(tmp_path):
    _minimal_sample(tmp_path)
    (tmp_path / "acq1").mkdir()
    (tmp_path / "acq1" / "acquisition.toml").write_text("not = = valid\n")
    record, errors, _ = validate_dir(tmp_path)
    assert record is None
    assert any("acq1/acquisition.toml" in e and "TOML parse error" in e for e in errors)


def test_minimal_valid_sample(tmp_path):
    _minimal_sample(tmp_path)
    record, errors, warnings = validate_dir(tmp_path)
    assert errors == []
    assert warnings == []
    assert record is not None
    assert record.sample.data_source.value == "cryoet"
    assert record.sample.project.value == "chromatin"
    assert record.sample.sample_id == tmp_path.name
    assert record.acquisitions == {}


def test_missing_required_field(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        """,
    )
    record, errors, _ = validate_dir(tmp_path)
    assert record is None
    assert any("project" in e for e in errors)


def test_invalid_enum_value(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "xray"
        project = "chromatin"
        """,
    )
    record, errors, _ = validate_dir(tmp_path)
    assert record is None
    assert any("data_source" in e for e in errors)


def test_project_block_mismatch_synapse_on_chromatin(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        project = "chromatin"

        [synapse]
        label_target = "AMPA"
        label_strategy = "single_label"
        """,
    )
    record, errors, _ = validate_dir(tmp_path)
    assert record is None
    assert any("chromatin" in e and "synapse" in e for e in errors)


def test_simulation_block_rejected_for_cryoet(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        project = "chromatin"

        [simulation]
        dataset_type = "bulk"
        """,
    )
    record, errors, _ = validate_dir(tmp_path)
    assert record is None
    assert any("cryoet" in e and "simulation" in e for e in errors)


def test_aunp_block_happy_path(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        project = "chromatin"

        [[aunp]]
        size_nm = 5.0
        type = "colloidal"
        fluorophore = "Alexa647"
        concentration_value = 2.5
        concentration_unit = "nM"
        conjugation = "Fab"
        conjugation_target = "GluA2"

        [[aunp]]
        size_nm = 10.0
        type = "cluster"
        """,
    )
    record, errors, warnings = validate_dir(tmp_path)
    assert errors == []
    assert warnings == []
    assert record is not None
    assert len(record.aunp) == 2
    assert record.aunp[0].size_nm == 5.0
    assert record.aunp[0].conjugation_target == "GluA2"
    assert record.aunp[1].type == "cluster"


def test_freezing_block_happy_path(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        project = "chromatin"

        [freezing]
        grid_type = "Quantifoil R2/2"
        cryoprotectant = "none"
        method = "HPF"
        planchette_size = "3 mm"
        spacer_thickness = "100 um"
        """,
    )
    record, errors, warnings = validate_dir(tmp_path)
    assert errors == []
    assert warnings == []
    assert record is not None
    assert record.freezing is not None
    assert record.freezing.method == "HPF"
    assert record.freezing.planchette_size == "3 mm"


def test_milling_block_happy_path(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        project = "chromatin"

        [milling]
        scheme = "cryo-FIB"
        date = 2025-06-15
        """,
    )
    record, errors, warnings = validate_dir(tmp_path)
    assert errors == []
    assert warnings == []
    assert record is not None
    assert record.milling is not None
    assert record.milling.scheme == "cryo-FIB"
    assert record.milling.date.isoformat() == "2025-06-15"


def test_simulation_sample_happy_path(tmp_path):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "simulation"
        project = "chromatin"

        [simulation]
        dataset_type = "single_molecule"
        """,
    )
    record, errors, warnings = validate_dir(tmp_path)
    assert errors == []
    assert warnings == []
    assert record is not None
    assert record.sample.data_source.value == "simulation"
    assert record.simulation is not None
    assert record.simulation.dataset_type == "single_molecule"


def test_acquisition_with_tomogram_and_annotation(tmp_path):
    _minimal_sample(tmp_path)
    _write(
        tmp_path / "acq1" / "acquisition.toml",
        """
        [acquisition]
        resolution = 3.5

        [[tomogram]]
        id = "tomo_001"
        pipeline = "AreTomo"

        [[tomogram]]
        id = "tomo_002"
        derived_from = ["tomo_001"]

        [[annotation]]
        id = "ann_001"
        target_tomogram = "tomo_001"
        """,
    )
    record, errors, warnings = validate_dir(tmp_path)
    assert errors == []
    assert record is not None
    acq = record.acquisitions["acq1"]
    assert [t.tomogram_id for t in acq.tomogram] == ["tomo_001", "tomo_002"]
    assert acq.annotation[0].target_tomogram == "tomo_001"


def test_annotation_target_tomogram_missing(tmp_path):
    _minimal_sample(tmp_path)
    _write(
        tmp_path / "acq1" / "acquisition.toml",
        """
        [acquisition]

        [[tomogram]]
        id = "tomo_001"

        [[annotation]]
        id = "ann_001"
        target_tomogram = "nonexistent_tomo"
        """,
    )
    record, errors, _ = validate_dir(tmp_path)
    assert record is None
    assert any("nonexistent_tomo" in e for e in errors)


def test_tomogram_derived_from_unknown(tmp_path):
    _minimal_sample(tmp_path)
    _write(
        tmp_path / "acq1" / "acquisition.toml",
        """
        [acquisition]

        [[tomogram]]
        id = "tomo_001"
        derived_from = ["ghost"]
        """,
    )
    record, errors, _ = validate_dir(tmp_path)
    assert record is None
    assert any("ghost" in e for e in errors)


def test_multiple_acquisitions(tmp_path):
    _minimal_sample(tmp_path)
    _minimal_acquisition(tmp_path, "acq_a")
    _minimal_acquisition(tmp_path, "acq_b")
    record, errors, _ = validate_dir(tmp_path)
    assert errors == []
    assert record is not None
    assert set(record.acquisitions) == {"acq_a", "acq_b"}
    for name, acq in record.acquisitions.items():
        assert acq.acquisition.acquisition_id == name


# ── main() ───────────────────────────────────────────────────────────────────


def test_main_wrong_argc(capsys):
    rc = main(["validate.py"])
    assert rc == 2
    assert "Usage" in capsys.readouterr().err


def test_main_not_a_directory(tmp_path, capsys):
    missing = tmp_path / "does_not_exist"
    rc = main(["validate.py", str(missing)])
    assert rc == 2
    assert "not a directory" in capsys.readouterr().err


def test_main_success(tmp_path, capsys):
    _minimal_sample(tmp_path)
    rc = main(["validate.py", str(tmp_path)])
    out = capsys.readouterr()
    assert rc == 0
    assert "OK" in out.out


def test_main_failure_returns_1(tmp_path, capsys):
    _write(
        tmp_path / "sample.toml",
        """
        [sample]
        data_source = "cryoet"
        """,
    )
    rc = main(["validate.py", str(tmp_path)])
    out = capsys.readouterr()
    assert rc == 1
    assert "FAIL" in out.err


