"""Regenerate schema.json from the Pydantic models.

Writes the JSON Schema for SampleRecord to ./schema.json. Run whenever the
Pydantic models change so downstream tools (non-Python validators, UIs)
stay in sync.
"""

from __future__ import annotations

import json
from pathlib import Path

from schema import SampleRecord


def main() -> None:
    schema = SampleRecord.model_json_schema()
    out = Path(__file__).parent / "schema.json"
    out.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
