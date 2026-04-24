"""Validate a cryoET sample directory against the Pydantic schema.

Usage:
    pixi run validate <sample_dir>

Loads <sample_dir>/sample.toml plus every acquisition.toml one level below,
merges them into a SampleRecord keyed by acquisition directory name, and
validates. Errors are printed with JSON-path-like locators; unknown fields
are reported as warnings but do not fail validation.
"""

from __future__ import annotations

import sys
import tomllib
import warnings
from pathlib import Path

from pydantic import BaseModel, ValidationError

from cryoet_schema import SampleRecord


def _load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def _walk_extras(
    instance: BaseModel, path: str, out: list[tuple[str, str]]
) -> None:
    """Collect (location, field_name) for every extra key on `instance` and its children."""
    extras = instance.model_extra or {}
    for name in extras:
        out.append((path, name))

    for field_name in instance.__class__.model_fields:
        value = getattr(instance, field_name)
        child_path = f"{path}.{field_name}" if path else field_name
        if isinstance(value, BaseModel):
            _walk_extras(value, child_path, out)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, BaseModel):
                    _walk_extras(item, f"{child_path}[{i}]", out)
        elif isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, BaseModel):
                    _walk_extras(v, f"{child_path}[{k!r}]", out)


def _format_error_loc(loc: tuple) -> str:
    return ".".join(str(x) for x in loc)


def validate_dir(sample_dir: Path) -> tuple[SampleRecord | None, list[str], list[str]]:
    """Return (record, errors, warning_msgs). record is None iff errors is non-empty."""
    errors: list[str] = []
    warning_msgs: list[str] = []

    sample_toml = sample_dir / "sample.toml"
    if not sample_toml.is_file():
        errors.append(f"missing sample.toml at {sample_toml}")
        return None, errors, warning_msgs

    try:
        sample_data = _load_toml(sample_toml)
    except tomllib.TOMLDecodeError as e:
        errors.append(f"sample.toml: TOML parse error: {e}")
        return None, errors, warning_msgs

    acquisitions: dict[str, dict] = {}
    for acq_toml in sorted(sample_dir.glob("*/acquisition.toml")):
        acq_name = acq_toml.parent.name
        try:
            acq_data = _load_toml(acq_toml)
        except tomllib.TOMLDecodeError as e:
            errors.append(f"{acq_name}/acquisition.toml: TOML parse error: {e}")
            continue
        acq_data.setdefault("acquisition", {})["acquisition_id"] = acq_name
        acquisitions[acq_name] = acq_data

    if errors:
        return None, errors, warning_msgs

    sample_data.setdefault("sample", {})["sample_id"] = sample_dir.name
    merged = {**sample_data, "acquisitions": acquisitions}

    record: SampleRecord | None = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", UserWarning)
        try:
            record = SampleRecord.model_validate(merged)
        except ValidationError as e:
            for err in e.errors():
                errors.append(f"{_format_error_loc(err['loc'])}: {err['msg']}")

    for w in caught:
        if issubclass(w.category, UserWarning):
            warning_msgs.append(str(w.message))

    if errors:
        return None, errors, warning_msgs

    assert record is not None
    extras: list[tuple[str, str]] = []
    _walk_extras(record, "", extras)
    for loc, name in extras:
        warning_msgs.append(f"extra field '{name}' at '{loc or '<root>'}' (not in schema)")

    return record, errors, warning_msgs


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: validate.py <sample_dir>", file=sys.stderr)
        return 2

    sample_dir = Path(argv[1]).resolve()
    if not sample_dir.is_dir():
        print(f"error: {sample_dir} is not a directory", file=sys.stderr)
        return 2

    record, errors, warning_msgs = validate_dir(sample_dir)

    for w in warning_msgs:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}", file=sys.stderr)

    if errors:
        print(f"\nFAIL: {len(errors)} error(s), {len(warning_msgs)} warning(s)", file=sys.stderr)
        return 1

    n_acq = len(record.acquisitions) if record else 0
    print(f"\nOK: sample '{sample_dir.name}' validated "
          f"({n_acq} acquisition(s), {len(warning_msgs)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
