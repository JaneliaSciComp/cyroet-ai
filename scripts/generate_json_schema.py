"""Regenerate schema.json from the Pydantic models.

Writes the JSON Schema for SampleRecord. Run whenever the Pydantic models
change so downstream tools (non-Python validators, UIs) stay in sync.

Usage:
    pixi run json-schema [output_path]

Defaults to <repo>/cryoet_schema/schema.json when no path is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cryoet_schema import SampleRecord


_DEFAULT_OUT = Path(__file__).resolve().parent.parent / "cryoet_schema" / "schema.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=_DEFAULT_OUT,
        help=f"path to write schema.json (default: {_DEFAULT_OUT})",
    )
    args = parser.parse_args(argv)

    schema = SampleRecord.model_json_schema()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
