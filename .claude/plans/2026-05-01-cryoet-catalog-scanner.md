# CryoET Catalog Scanner — Implementation Plan

**Date:** 2026-05-01
**Scope:** A filesystem scanner that walks sample directories laid out per `README.md` and `cryoet_schema/schema_info.md`, parses every authoritative source (TOML, MDOC, MRC header, OME-Zarr `.zattrs`, frame extension, directory names), and persists the result to a SQL database. SQLite for development/testing; PostgreSQL for production. The scanner code itself is database-agnostic — only the connection string changes.

---

## 1. Context

### What already exists in this repo

- `cryoet_schema/schema.py` — authoritative Pydantic v2 schema. Defines `Sample`, `Chromatin`, `Synapse`, `Aunp`, `Freezing`, `Milling`, `Simulation`, `Acquisition`, `Tomogram`, `Annotation`, `AcquisitionFile`, and the top-level `SampleRecord` that bundles a sample with all its acquisitions. Uses `extra="allow"` so unknown TOML keys survive as `model_extra`.
- `cryoet_schema/schema_info.md` — human-readable enumeration of every DB-bound field, its type, and authoritative source. The DB schema in this plan is a 1:1 mirror of this document.
- `scripts/validate.py` — already implements the **TOML half** of assembly: loads `sample.toml` plus every `acquisition.toml` under a sample dir, injects path-derived ids (`sample_id`, `acquisition_id`), and validates into a `SampleRecord`. Reports extras as warnings via `_walk_extras`. **This plan moves it into `cryoet_schema/` and splits the merge logic out as `cryoet_schema.loader` so both the CLI and the catalog scanner can import it.**
- `scripts/generate_json_schema.py` — regenerates `schema.json` from `schema.py`. **Also moved into `cryoet_schema/` by this plan.**

### Goals

1. One command (or one Python entry point) ingests a data root into the catalog DB.
2. Re-running is idempotent and fast: a sample is re-assembled only when one of its parse-target files changed mtime or when a parse-target file was added or removed.
3. Backend-portable: same scanner code runs against SQLite (test) and PostgreSQL (prod). No raw SQL strings in the scanner.
4. ORM is **hand-written in `cryoet_catalog/orm.py` and pinned to the Pydantic models in `schema.py` by a drift test** that asserts every field and column agrees on name, type, and nullability. Two definitions, one test — no introspection.
5. Validation errors and source conflicts (e.g., MDOC-implied `pixel_size × voxel_bin` vs MRC header `voxel_size.x`) are surfaced clearly per-sample without aborting the whole scan; per-acquisition isolation means a single bad `acquisition.toml` does not black-hole the rest of the sample.

### Non-goals

- No portal UI in v1. The React dashboard will call the FastAPI API (§4.12) but is not implemented here.
- No migrations from a previous catalog DB.
- No simulation-side parsers beyond what the schema already covers (Trajectories/, Snapshots/ are walked for path discovery only — no per-format parsing in v1).
- No Alignments/ parsing in v1 (the schema doesn't describe alignment fields yet).

---

## 2. Architecture

Five layers, with strict directional dependencies — each layer only imports from the layers above it.

```
                ┌──────────────────────────────┐
                │ orchestrator (scan_root)     │
                └──────────────────────────────┘
                              │
            ┌─────────────────┼─────────────────┐
            ▼                                   ▼
   ┌────────────────┐                ┌────────────────────┐
   │ discovery      │                │ parsers/           │
   │ - find sample, │                │   toml_files       │
   │   acquisition, │                │   mdoc             │
   │   tomogram,    │                │   mrc_header       │
   │   annotation   │                │   ome_zarr         │
   │   directories  │                │   frame_ext        │
   │ - parse targets│                │ (each: path → dict)│
   └────────────────┘                └────────────────────┘
                              │
                              ▼
                ┌──────────────────────────────┐
                │ assembler                    │
                │ - merge parser outputs into  │
                │   SampleRecord (Pydantic)    │
                │ - apply conflict policy      │
                │ - compute derived fields     │
                └──────────────────────────────┘
                              │
                              ▼
                ┌──────────────────────────────┐
                │ persistence                  │
                │ - hand-written ORM           │
                │ - SampleRecord → rows        │
                │ - upsert by PK               │
                └──────────────────────────────┘
                              │
                              ▼
                ┌──────────────────────────────┐
                │ db engine (SQLAlchemy)       │
                │ sqlite:///… or postgresql://…│
                └──────────────────────────────┘
```

### Module layout

`cryoet_catalog/` sits as a peer of `cryoet_schema/` at the repo root. `cryoet_schema` is the *contract* (pydantic + rapidfuzz only); `cryoet_catalog` is one *consumer* of it (adds SQLAlchemy, mrcfile, zarr). The dependency arrow points one way: catalog → schema, never the reverse.

No `src/` layout — match the existing flat convention used by `cryoet_schema/` and `scripts/`.

```
/workspace/
  pyproject.toml             # NEW — root package definition; optional extras: catalog, api, dev

  cryoet_schema/             # existing, expanded
    __init__.py              # existing — exports SampleRecord, etc.
    schema.py                # existing — Pydantic models
    schema.json              # existing — generated artifact
    schema_info.md           # existing — docs
    loader.py                # NEW — library: load_sample_record(dir) -> (record, errors, warnings, extras)
    validate.py              # MOVED from scripts/ — CLI wrapper around loader
    generate_json_schema.py  # MOVED from scripts/ — regenerates schema.json

  cryoet_catalog/            # NEW
    __init__.py
    db.py                    # engine factory + session helper
    orm.py                   # hand-written SQLAlchemy declarative classes (pinned to Pydantic by drift test)
    state.py                 # mtime/scan-state tables and gating helpers
    discovery.py             # walks the data root, yields locations
    parsers/
      __init__.py
      toml_files.py          # thin wrapper that imports cryoet_schema.loader.load_sample_record
      mdoc.py                # MDOC parser
      mrc_header.py          # mrcfile header → dict
      ome_zarr.py            # .zattrs → dict
      frame_ext.py           # .eer/.tiff → camera string
    assembler.py             # parser outputs → SampleRecord
    persistence.py           # SampleRecord → ORM rows (upsert)
    scanner.py               # public entry point: scan_root(engine, root, *, force=False)
    cli.py                   # `python -m cryoet_catalog scan <root> --db <url>`
    api/                     # NEW — FastAPI read layer (see §4.12)
      __init__.py
      main.py                # app factory, lifespan (engine init), CORS
      deps.py                # get_session() FastAPI dependency
      schemas.py             # Pydantic response models (API output shapes, flat/renamed for JSON consumers)
      routes/
        __init__.py
        samples.py           # GET /samples, GET /samples/{sample_id}
        scans.py             # GET /scans, GET /scans/latest
        extras.py            # GET /extras/summary
        warnings.py          # GET /samples/{sample_id}/warnings

  frontend/                  # NEW — React app (Vite + TypeScript); separate package.json
    package.json
    src/

  templates/                 # existing
  tests/
    cryoet_schema/           # new — loader and walker unit tests
      test_loader_isolation.py
      test_walker.py
    cryoet_catalog/          # new
      fixtures/              # tiny synthetic sample dirs (toml + minimal mdoc/mrc)
      test_orm_drift.py      # asserts every Pydantic field has a corresponding column
      test_parsers.py
      test_assembler.py
      test_persistence.py
      test_scanner.py        # end-to-end against fixtures
      test_api.py            # FastAPI TestClient against in-memory SQLite
```

The `scripts/` directory is removed. `cryoet_schema` becomes self-contained: definition, loader, and CLI tools all live together. `cryoet_catalog` depends on `cryoet_schema`; the reverse is forbidden.

---

## 2.5 Packaging (`pyproject.toml` + `pixi.toml`)

A **root `pyproject.toml`** makes `cryoet_schema` and `cryoet_catalog` pip-installable as one package with optional extras. The split between base and extras keeps the lightweight validation-only use case free of heavy deps:

```toml
[project]
name = "cryoet-schema"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["pydantic>=2.6", "rapidfuzz>=3.0"]   # schema + loader only

[project.optional-dependencies]
catalog = ["sqlalchemy>=2.0", "mrcfile>=1.5"]          # scanner + ORM
api     = ["cryoet-schema[catalog]", "fastapi>=0.110", "uvicorn[standard]"]
dev     = ["pytest>=7.0"]
```

`pixi.toml` gains matching features for local dev and CI, plus tasks for the API server and frontend:

```toml
[feature.catalog.dependencies]
sqlalchemy = ">=2.0"
mrcfile    = ">=1.5"

[feature.api.dependencies]
fastapi  = ">=0.110"
uvicorn  = "*"

[feature.api.tasks]
api      = "uvicorn cryoet_catalog.api.main:app --reload --port 8000"
frontend = { cmd = "npm run dev", cwd = "frontend" }   # convenience; requires node

[environments]
default = { solve-group = "default" }
test    = { features = ["test"],                    solve-group = "default" }
catalog = { features = ["catalog", "test"],         solve-group = "default" }
api     = { features = ["catalog", "api", "test"],  solve-group = "default" }
```

The React frontend (`frontend/`) has its own `package.json` and is managed by npm/node independently of pixi. It is not pip-installable; the API server is its only Python coupling point.

---

## 3. Database schema (mirrors `schema_info.md`)

Tables, with primary keys and parent foreign keys called out explicitly. Every column maps to one Pydantic field; list-valued fields become JSON columns (PostgreSQL `JSONB` via dialect, SQLite stores the same as TEXT — SQLAlchemy's `JSON` type handles both).

| Table | PK | FK → | Source Pydantic class |
|---|---|---|---|
| `samples` | `sample_id` | — | `Sample` |
| `chromatin` | `sample_id` | `samples.sample_id` | `Chromatin` |
| `synapse` | `sample_id` | `samples.sample_id` | `Synapse` |
| `simulation` | `sample_id` | `samples.sample_id` | `Simulation` |
| `freezing` | `sample_id` | `samples.sample_id` | `Freezing` |
| `milling` | `sample_id` | `samples.sample_id` | `Milling` |
| `aunp` | `(sample_id, ordinal)` | `samples.sample_id` | `Aunp` (0..N per sample) |
| `acquisitions` | `(sample_id, acquisition_id)` | `samples.sample_id` | `Acquisition` (reached via `record.acquisitions[acq_id].acquisition` — `AcquisitionFile` is a wrapper, not a table) |
| `tomograms` | `(sample_id, acquisition_id, tomogram_id)` | `acquisitions(sample_id, acquisition_id)` | `Tomogram` (reached via `record.acquisitions[acq_id].tomogram` — singular field name) |
| `annotations` | `(sample_id, acquisition_id, annotation_id)` | `acquisitions(sample_id, acquisition_id)` | `Annotation` (reached via `record.acquisitions[acq_id].annotation` — singular field name) |
| `extras` | `(entity_type, entity_pk_json, key)` | `samples.sample_id` (via denormalized `sample_id` column) | `model_extra` from any class |
| `scan_warnings` | `id` (autoinc) | `samples.sample_id`, `scans.scan_run_id` | per-sample validation/conflict warnings (see Q7 resolution) |
| `scans` | `scan_run_id` | — | one row per `scan_root` invocation; the "most recent scan" anchor for the dashboard |
| `scan_state` | `path` | — | (housekeeping; see §4.5) |
| `catalog_meta` | (single-row) | — | records `data_root` from most recent scan (see Q4 resolution) |

Notes:
- The chromatin/synapse/simulation/freezing/milling tables share `sample_id` as both PK and FK. They're 1:1 with samples but only present when applicable, so they sit in their own tables rather than as nullable columns on `samples` (resolved per Q1).
- `aunp` uses a synthetic `ordinal` integer (the index of the aunp entry in the TOML list) to give it a stable composite PK.
- `extras` is **queryable, not round-trippable**: it answers "which informal keys are researchers writing, on which entity types, how often?" but does not promise byte-for-byte reconstruction of the original TOML. `value_json` stores the raw JSON-encoded value of each top-level unknown key; if that value is itself a nested dict, the inner keys remain inside the JSON blob (queryable via `json_extract` / `->>`, not as flat rows). Promote any inner key that turns out to be common into a first-class field on the Pydantic model — that's the feedback loop the table exists to drive. `entity_pk_json` is a JSON-encoded list of the parent row's PK value(s) (e.g. `["my_sample", 2]` for an Aunp-list element). The table also carries a denormalized `sample_id TEXT NOT NULL` column (always equal to `entity_pk_json[0]`) so that per-sample refresh / cleanup is a clean indexed `WHERE sample_id = ?` instead of a JSON-prefix `LIKE` (which would mis-match samples whose ids share a prefix, e.g. `my_sample` vs `my_sample_v2`); the column is indexed, not part of the PK, and is populated at insert time from `entry.entity_pk[0]` — see §4.7 step 5.
- All path-typed fields (`mrc_path`, `zarr_path`, `annotations.files[*]`) are stored as **absolute** text paths anchored at the canonical mount, normalized via `Path.absolute()` (preserves symlinks). The single-row `catalog_meta` table records the `data_root` used at scan time as a documentation/migration anchor (resolved per Q4).
- List fields (e.g., `dose_per_tilt`, `linker_pattern`, `derived_from`, `zarr_scale`) use SQLAlchemy's `JSON` type.
- The `tomograms` table carries two DB-only columns not present on the Pydantic model: `voxel_spacing_angstrom` (read from the MRC header by the catalog; removed from `Tomogram` per step 0 in §7) and `voxel_spacing_angstrom_implied` (`pixel_size × voxel_bin` when both are available, NULL otherwise; exists for queryability of cross-source disagreements per Q6). Both are listed in `db_only_columns` in the drift test (§4.1) and populated via the assembler's `tomogram_aux` sidechannel (§4.6 / §4.7).
- The `samples` table carries one DB-only column: `deleted_at FLOAT NULL` (Unix timestamp). NULL means live; non-NULL means the sample directory was missing as of that scan. Soft-deletion is the v1 strategy for the "researcher removed a sample directory" case (see §4.10). Listed in the drift-test `db_only_columns` carve-out for `SampleORM`. Every dashboard or catalog query that wants live samples must filter `WHERE samples.deleted_at IS NULL`.
- The `scan_warnings` table is hand-coded (no Pydantic counterpart). Columns: `id INTEGER PK AUTOINCREMENT`, `sample_id TEXT NOT NULL FK→samples`, `category TEXT NOT NULL` (e.g. `'extra_field'`, `'possible_typo'`, `'voxel_spacing_implied_mismatch'`, `'missing_acquisition_toml'`, `'unparseable_acquisition_toml'`, `'unfilled_placeholder'`), `location TEXT NOT NULL` (e.g. `'chromatin'`, `'acquisitions.Position_86.tomogram[my_tomo].voxel_spacing_angstrom'`), `message TEXT NOT NULL`, `detected_at FLOAT NOT NULL`, `scan_run_id TEXT NOT NULL FK→scans`. The persistence layer clears all rows for a given `sample_id` before inserting fresh ones for that sample, mirroring the stale-row cleanup pattern used for `acquisitions`/`tomograms`/`annotations`.
- The `scans` table is hand-coded. Columns: `scan_run_id TEXT PRIMARY KEY` (UUID generated at the start of each `scan_root` invocation), `started_at FLOAT NOT NULL`, `ended_at FLOAT NULL` (NULL while a scan is in progress, set when the orchestrator finishes), `root TEXT NOT NULL` (the `data_root` argument), `status TEXT NOT NULL` (`'running' | 'completed' | 'failed'`), `samples_upserted INTEGER`, `samples_skipped INTEGER`, `samples_failed INTEGER`. The dashboard's "warnings from the most recent scan" query is `SELECT scan_run_id FROM scans WHERE status='completed' ORDER BY ended_at DESC LIMIT 1` joined against `scan_warnings`. Also gives scan history for free — the table grows by one row per invocation and is never pruned by the scanner itself.

---

## 4. Implementation steps

### 4.1 ORM (`orm.py`)

**Goal:** Hand-written SQLAlchemy declarative models — one class per table from §3 — pinned to the Pydantic models in `cryoet_schema/schema.py` via a drift test. Two definitions, one drift test; no introspection.

**Approach:**

1. Hand-write declarative SQLAlchemy classes in `cryoet_catalog/orm.py` using `Mapped[...]` annotations. One class per table from §3 (`SampleORM`, `ChromatinORM`, `SynapseORM`, `SimulationORM`, `FreezingORM`, `MillingORM`, `AunpORM`, `AcquisitionORM`, `TomogramORM`, `AnnotationORM`, plus the housekeeping tables `ExtrasORM`, `ScanWarningsORM`, `ScanStateORM`, `CatalogMetaORM`, `ScansORM`). Example shape:

   ```python
   class SampleORM(Base):
       __tablename__ = "samples"
       sample_id: Mapped[str] = mapped_column(String(_ID_MAX_LEN), primary_key=True)
       name: Mapped[str | None] = mapped_column(String, nullable=True)
       data_source: Mapped[DataSource] = mapped_column(SAEnum(DataSource), nullable=False)
       project: Mapped[Project] = mapped_column(SAEnum(Project), nullable=False)
       # ...
   ```

2. Type mapping conventions (applied consistently when writing each class):
   - `str` → `String`; `IdStr` → `String(_ID_MAX_LEN)` (the cross-platform-safety check stays on the Pydantic side).
   - `int` → `Integer`; `float` → `Float`; `bool` → `Boolean`.
   - `datetime.date` → `Date`.
   - Enum subclasses → `SAEnum(EnumClass)`.
   - List-valued fields (`list[IdStr]`, `list[float]`, `Annotation.files`, etc.) → `JSON` (works on both SQLite and Postgres).
   - Nullability: a Pydantic annotation that includes `None` → `nullable=True`; otherwise `nullable=False`. Note that `default_factory=list` fields (e.g. `Annotation.files`, `Tomogram.derived_from`) are *required-with-default* in Pydantic but their annotation doesn't include `None` — model them as `nullable=False` with a SQLAlchemy `default=list`.

3. DB-only columns are declared inline on the ORM class — they do not exist on the Pydantic side and never leak into TOML validation or the JSON Schema export:
   - `aunp.ordinal` (composite PK component).
   - `tomograms.voxel_spacing_angstrom` (MRC-header-derived; removed from the `Tomogram` Pydantic model per step 0).
   - `tomograms.voxel_spacing_angstrom_implied` (`pixel_size × voxel_bin` when computable, NULL otherwise).
   - Sub-entity `sample_id` columns on `ChromatinORM`/`SynapseORM`/`SimulationORM`/`FreezingORM`/`MillingORM`/`AunpORM` (FK back to `samples`; the Pydantic sub-entity classes don't carry a `sample_id` field).

4. Composite PKs and foreign keys are declared inline using `mapped_column(ForeignKey(...))` and `__table_args__ = (PrimaryKeyConstraint(...), ForeignKeyConstraint(...), ...)` where needed (e.g. `TomogramORM` needs a composite FK back to `(acquisitions.sample_id, acquisitions.acquisition_id)`).

**Drift test (`test_orm_drift.py`):** the test owns a small `MAPPING` table at the top — one entry per `(pydantic_cls, orm_cls, db_only_columns)` pair — and asserts:

- **Every Pydantic field has an ORM column.** Resolves aliases (e.g. `Tomogram.tomogram_id` is aliased to `id`; the column is named `tomogram_id`). Fails if a field is added to `schema.py` and forgotten in `orm.py`.
- **Every ORM column is either a Pydantic field or in `db_only_columns`.** Fails if a column is added to `orm.py` without either a matching field or an explicit DB-only carve-out. Forces the carve-out list to stay honest.
- **Column type matches the Pydantic annotation**, checked against a hand-written canonical mapping in the test file (`str -> String`, `int -> Integer`, `float -> Float`, `bool -> Boolean`, `datetime.date -> Date`, `Enum subclass -> SAEnum(<that enum>)`, `list[...] -> JSON`, `IdStr -> String(_ID_MAX_LEN)`). The mapping lives in the test, not in production code, so a regression in the mapping itself fails the test instead of silently agreeing with itself.
- **Nullability matches.** A Pydantic annotation containing `None` ↔ `column.nullable is True`. `default_factory=list` fields are explicitly listed as exceptions in the test (annotation has no `None`, but column is `nullable=False default=list`).

The test file is ~80 lines: ~15 lines of `MAPPING`, ~10 lines of canonical type table, ~50 lines of assertion loops. When someone adds a field to either side, the test names exactly which pair drifted.

### 4.2 DB engine / session (`db.py`)

```python
def make_engine(url: str) -> Engine: ...
def init_schema(engine: Engine) -> None:        # Base.metadata.create_all
def session_scope(engine) -> ContextManager[Session]: ...
```

Configuration:
- Default URL `sqlite:///cryoet_catalog.db` for tests.
- `cryoet_catalog.db.make_engine` takes any SQLAlchemy URL — no Postgres-specific code in the scanner. Postgres-only tuning (e.g., `JSONB` vs `JSON`) can be done via `TypeDecorator` if/when needed; for v1 use `JSON` so the same code runs on both.

### 4.3 Discovery (`discovery.py`)

Pure path-walking, no I/O of file *contents*. Yields a small dataclass per layer:

```python
@dataclass(frozen=True)
class SampleLocation:
    path: Path
    sample_id: str          # path.name
    sample_toml: Path       # path / "sample.toml"

@dataclass(frozen=True)
class AcquisitionLocation:
    path: Path
    sample_id: str
    acquisition_id: str
    acquisition_toml: Path | None     # None if the directory has Frames/ but no acquisition.toml
    frames_dir: Path | None
    tilt_series_dir: Path | None
    tomograms_dir: Path | None
    annotations_dir: Path | None

@dataclass(frozen=True)
class TomogramLocation:
    path: Path              # the per-pipeline subfolder
    tomogram_id: str        # path.name
    mrc_files: list[Path]
    zarr_dirs: list[Path]

@dataclass(frozen=True)
class AnnotationLocation:
    path: Path
    annotation_id: str
    files: list[Path]       # discovered artifact files (see rule below)
```

**`AnnotationLocation.files` discovery rule.** List the *direct* children of the annotation folder, then keep:

- Any file with an extension in `{".star", ".mrc", ".png", ".tiff", ".tif", ".csv", ".json"}`.
- Any *directory* whose name ends in `.zarr` or `.ome.zarr` — treated as a single entry, **not recursed into**. (A `.zarr` store contains hundreds of opaque chunk files; storing one row per chunk in `annotations.files` would explode the JSON column for no useful query.)

Skip everything else: hidden files (`.DS_Store`, `.gitkeep`), subdirectories that aren't `.zarr` stores, anything not in the extension list. If a researcher adds a new artifact kind we don't recognize, it's silently dropped — that's fine for v1; widen the extension list when a real case appears. Discovery does not recurse: a researcher who buries artifacts inside an arbitrary subdirectory is outside the supported layout.

`iter_samples(root)` yields `SampleLocation` for any direct child of `root` containing `sample.toml`. `iter_acquisitions(sample)` yields `AcquisitionLocation` for any direct child of the sample dir that has **either an `acquisition.toml` or a `Frames/` subdirectory** — the catalog reflects what exists on disk, with curated metadata where present. A directory with `acquisition.toml` populates `acquisition_toml = <path>`; a Frames-only directory populates `acquisition_toml = None` and is treated as an acquisition with no curated metadata yet (the assembler synthesizes an empty `Acquisition` and emits a `'missing_acquisition_toml'` warning — see §4.6 step 1.5). Similar shapes for tomograms and annotations under `Reconstructions/`.

`parse_targets_for_sample(sample_loc) -> list[Path]` returns every file the parsers will read for this sample: `sample.toml`, each `acquisition.toml` *when present*, each MDOC under each `frames_dir`, each `.mrc` under each `Reconstructions/Tomograms/{processing_id}/`, each `.ome.zarr/.zattrs` likewise, and a representative frame file per acquisition (for the camera-extension parser). The orchestrator (§4.8) consumes this list to drive file-level mtime gating in §4.5. Frame files themselves are not parse targets — only the chosen representative is — because the camera string is constant across all frames in an acquisition.

For Frames-only acquisitions (no `acquisition.toml`), discovery does **not** add a directory-level probe — the gate stays purely file-level. The MDOC files inside `frames_dir` and the representative frame file are real files and become parse targets in their own right. When the researcher later authors `acquisition.toml`, the next scan's discovery surfaces it as a new entry in `parse_targets`, and the orchestrator's `parse_target_set_changed` check (§4.5) sees a path that isn't in `scan_state` and forces a re-assemble. No directory mtime is consulted; the set-difference is the load-bearing signal.

### 4.4 Parsers (`parsers/`)

Each parser is a pure function. Pure: same path → same output, no DB, no global state. Each returns either a `dict[str, Any]` of fields or a small typed result.

| Module | Function | Output |
|---|---|---|
| `toml_files.py` | `load_sample_record(sample_loc)` | `SampleRecord` (delegates to `cryoet_schema.loader.load_sample_record`; returns the validated record + warnings + errors) |
| `mdoc.py` | `parse_acquisition_mdocs(frames_dir)` | dict with `pixel_size`, `dose_per_tilt`, `total_dose`, `tilt_min`, `tilt_max`, `tilt_axis`, `defocus_per_image`, `date_collected`, `voltage`, `energy_filter_slit_width`, `frame_count` |
| `mrc_header.py` | `read_mrc_header(mrc_path)` | dict with `image_size_x/y/z`, `voxel_spacing_angstrom` (canonical, per Q6) |
| `ome_zarr.py` | `read_zarr_attrs(zarr_path)` | dict with `zarr_axes`, `zarr_scale` |
| `frame_ext.py` | `infer_camera(frames_dir)` | `"Falcon"` / `"K3"` / None |

Implementation: direct `mrcfile.open(..., header_only=True)` for MRC headers, json-loading the `.zattrs` file directly (avoids pulling in a heavy zarr dep), a tiny extension-to-camera lookup, and a from-scratch MDOC parser (MDOC is plain text key/value with `[ZValue = N]` section markers — small enough to write directly). The TOML parser is a thin wrapper that imports `cryoet_schema.loader.load_sample_record` — see step 4.4.1 below for the loader extraction.

#### Parser failure modes

Each non-TOML parser (MDOC, MRC header, OME-Zarr, frame-ext) declares one of three outcomes for every call. The assembler reads the outcome and decides what to put on the `SampleRecord` and what to surface as a warning. Extending the parsers' return shape from "dict | None" to a small typed result keeps the failure path uniform:

```python
@dataclass
class ParseResult:
    fields: dict[str, Any]   # parsed key/value pairs; empty dict on source-missing or source-unreadable
    status: Literal["ok", "missing", "unreadable"]
    error: str | None        # human-readable detail when status == "unreadable"
```

The three categories:

1. **Source-missing (`status="missing"`).** The parser was invoked but the underlying file/dir doesn't exist — e.g. an acquisition has a `frames_dir` but no `.mdoc` files yet, a tomogram folder has no `.mrc`, an annotation has no `.ome.zarr`. Returns `ParseResult(fields={}, status="missing", error=None)`. The assembler treats this as "no information"; affected fields stay `None` on the Pydantic model. **No warning is emitted** — most missing sources are just "researcher hasn't run that pipeline step yet" and would create dashboard noise. Discovery-side absence (e.g. no `Frames/` subdirectory at all) is upstream of this and never reaches the parser.

2. **Source-unreadable (`status="unreadable"`).** The file exists but parsing failed: malformed MDOC (unparseable section header, non-numeric value where a float was required), corrupt MRC header (mrcfile raises), `.zattrs` that isn't valid JSON, frame extension recognized but conflicting (e.g. both `.eer` and `.tiff` present in the same `Frames/`). Returns `ParseResult(fields={}, status="unreadable", error=<detail>)`. The assembler emits a categorized warning (`'unparseable_mdoc'`, `'unparseable_mrc_header'`, `'unparseable_zarr_attrs'`, `'ambiguous_frame_extension'`) into `AssemblyResult.warnings` with the parser's `error` string in the message and a `location` keyed to the offending acquisition / tomogram / annotation, then continues with the remaining parse targets for that sample. Affected fields stay `None`. The catalog still ingests the rest of the sample.

3. **Source-conflict.** Parsing succeeded on every individual source, but two sources disagree on the same logical field — currently only the `voxel_spacing_implied_mismatch` case (§4.6 step 4) lives here. This is a `FieldConflict` in `AssemblyResult.conflicts`, not a `ParseResult` failure. Listed here for completeness so all three "field is missing or wrong" paths share one mental model.

Discovery-side absence (no `frames_dir`, no `Reconstructions/Tomograms/`, no annotation folders) is *not* a parser failure — those parsers are simply not invoked. Only the field-level "I tried, nothing was there" case is `status="missing"`.

The TOML loader uses its own shape (`LoadResult` from §4.4.1) because it has richer per-acquisition isolation requirements; the three categories above apply to the four supplementary parsers only.

#### 4.4.1 Loader extraction (precondition for the catalog)

Before the catalog can use TOML parsing, the existing merge-and-validate logic in `scripts/validate.py` needs to be split into a library function and a CLI wrapper:

- **`cryoet_schema/loader.py`** (new) — contains `load_sample_record(sample_dir: Path) -> LoadResult` plus the `LoadResult` and `ExtrasEntry` dataclasses and the walker that produces extras. Pure library code, no argparse, no printing.
  - `LoadResult` packages everything the caller needs:
    ```python
    @dataclass
    class LoadResult:
        record: SampleRecord | None              # None if sample.toml itself is unparseable
        sample_errors: list[str]                  # errors not tied to a specific acquisition
        acquisition_errors: dict[str, str]        # acquisition_id -> error message; populated when an acquisition.toml fails to parse or validate (see "Per-acquisition isolation" below)
        warnings: list[str]                       # extra-field warnings, possible-typo warnings, unfilled-placeholder warnings
        extras: list[ExtrasEntry]
    ```
    The 5-field dataclass replaces what would have been a 5-tuple; the validate CLI and the catalog both consume `LoadResult` directly.
  - `ExtrasEntry` is `(entity_type: str, entity_pk: tuple, key: str, value: Any)` — `entity_type` is the lowercase table name (e.g. `"chromatin"`, `"aunp"`), `entity_pk` is the parent row's PK as a tuple of native Python values (e.g. `("my_sample",)` for `chromatin`, `("my_sample", 2)` for the third Aunp entry), `key` is the top-level unknown TOML key, `value` is the raw Python value Pydantic stored on `model_extra`. Persistence (§4.7) converts `entity_pk` to JSON via `json.dumps(list(entity_pk))` and `value` likewise.
  - **The walker is a rewrite of `_walk_extras`, not a light refactor.** The existing walker (validate.py:29) emits string paths like `aunp[2]`, but the catalog needs structured PK *tuples* whose shape varies by container — some children key by *position*, some by *child id*. The walker carries a small per-container mapping table (constants in `cryoet_schema/loader.py`) that says, for every model-valued attribute on a parent, which container shape it is and how to derive the child's PK:

    | Parent attribute (on `SampleRecord`) | Container | PK rule |
    |---|---|---|
    | `sample` | single | `entity_type="sample"`, `entity_pk = (sample_id,)` |
    | `chromatin`, `synapse`, `simulation`, `freezing`, `milling` | `Optional[BaseModel]`, may be `None` | `entity_type=<attr>`, `entity_pk = (sample_id,)` |
    | `aunp` | `list[Aunp]` | `entity_type="aunp"`, `entity_pk = (sample_id, i)` (positional — `Aunp` carries no id) |
    | `acquisitions` | `dict[str, AcquisitionFile]` | recurse into each value (see below) |

    For each `(acq_id, acq_file)` in `record.acquisitions.items()`:

    | Attribute on `AcquisitionFile` | Container | PK rule |
    |---|---|---|
    | `acquisition` | single `Acquisition` | `entity_type="acquisition"`, `entity_pk = (sample_id, acq_id)` |
    | `tomogram` | `list[Tomogram]` | `entity_type="tomogram"`, `entity_pk = (sample_id, acq_id, t.tomogram_id)` — id-keyed; reach into the model via the alias-resolved attribute, **not** the list index |
    | `annotation` | `list[Annotation]` | `entity_type="annotation"`, `entity_pk = (sample_id, acq_id, a.annotation_id)` — id-keyed |

    `AcquisitionFile` itself is *not* an extras-bearing entity (it's a wrapper, no table per §3) and its own `model_extra` is intentionally not walked — any unknown top-level keys in `acquisition.toml` belong to one of `acquisition` / `tomogram` / `annotation` blocks, which the walker reaches directly.

    The walker emits one `ExtrasEntry` per top-level unknown key per visited entity. Nested unknown values stay inside `value` as a dict (per Q3 — promote them to first-class fields if they recur, don't flatten in the catalog). The validate CLI flattens `entity_type` + `entity_pk` back to a human path string (`"aunp[2]"`, `"acquisitions.Position_86.tomogram[my_tomo]"`) for warning printing; the catalog passes the structured tuple straight through to persistence.

    A unit test in `tests/cryoet_schema/test_walker.py` covers each container shape with a fixture that places one unknown key in each — failure to reach inside `Tomogram` for the id (e.g. emitting a positional `(sample_id, acq_id, 0)` instead of `(sample_id, acq_id, "my_tomo")`) fails the test.
  - **Per-acquisition isolation.** Each `acquisition.toml` is parsed and validated *independently*, not merged into a single sample-wide dict ahead of validation. Concrete flow: (1) load and validate `sample.toml` into a `Sample`; if that fails, return `LoadResult(record=None, sample_errors=[…], …)` because the sample is unrecoverable. (2) For each `acquisition.toml` found by glob, try to load and validate as an `Acquisition`; on failure, add `acquisition_id -> error_string` to `acquisition_errors` and skip that acquisition. (3) Build `SampleRecord` from the `Sample` plus the *successfully* validated acquisitions. The result: a single bad `acquisition.toml` no longer black-holes the rest of the sample — the validate CLI prints per-acquisition errors, and the catalog ingests the rest of the sample (the failed acquisition is synthesized as a TOML-less placeholder with an `'unparseable_acquisition_toml'` warning; see §4.6 step 1.5).
  - **Placeholder stripping (`<FILL IN>` handling).** Before passing each loaded TOML dict to Pydantic, `load_sample_record` recursively walks the dict and replaces any string value equal to `"<FILL IN>"` with `None`, collecting the dotted field path of each replacement into `warnings` as a `'unfilled_placeholder'` entry (e.g. `"acquisitions.Position_86.acquisition.description: unfilled <FILL IN> placeholder"`). Two reasons for converting *before* Pydantic validation: (a) `<FILL IN>` in numeric fields like `pixel_size` would otherwise fail type coercion and abort that acquisition's load, and (b) a clean DB downstream is more useful than literal `<FILL IN>` strings polluting `WHERE name = ?` queries. The replace-then-warn shape preserves the researcher feedback loop (the dashboard surfaces "you have N unfilled placeholders") while keeping query semantics simple. Skipped inside `model_extra` blobs — unknown fields are already noisy enough; if a researcher embeds `<FILL IN>` inside an unknown nested dict, it stays as-is in the extras blob.
- **`cryoet_schema/validate.py`** (moved + slimmed) — the CLI: argparse, calls `load_sample_record`, pretty-prints errors, warnings, and the `extras` list, returns an exit code. Invokable as `python -m cryoet_schema.validate <sample_dir>`.
- **`cryoet_schema/generate_json_schema.py`** (moved as-is from `scripts/`) — invokable as `python -m cryoet_schema.generate_json_schema`.
- **`scripts/`** — directory deleted.
- **`pixi.toml`** — update the `validate` and `json-schema` task definitions to use the new `python -m cryoet_schema.…` invocations. The user-facing commands (`pixi run validate <dir>`, `pixi run json-schema`) stay the same.

### 4.5 State / mtime gating (`state.py`)

**Approach: file-level gating throughout.** Directory mtimes are unreliable as a fast path: they only advance when *direct* children are added, removed, or renamed — they do not bubble up when a file is modified inside a deeper subtree, and they do not even fire when a file is overwritten in place at the same level (some editors save in-place; some save via atomic rename). Both failure modes hit realistic researcher workflows (e.g., a new MRC dropped into an existing `Reconstructions/Tomograms/{processing_id}/` directory, or a `sample.toml` edited in vim). Stat'ing every parse-target file every scan is correct by construction and, at the expected scale (~hundreds of samples, ~30k files), costs seconds, not minutes — within the budget for a CLI tool that's run on demand.

A `scan_state` table:

```
scan_state(path TEXT PRIMARY KEY,
           sample_id TEXT NOT NULL,        -- denorm; indexed; FK→samples.sample_id (no ON DELETE — soft-delete keeps samples row alive)
           mtime FLOAT NOT NULL,
           last_scanned FLOAT NOT NULL,
           content_hash TEXT NULL)
```

`sample_id` is denormalized onto every row (always equal to the owning sample directory's id) and indexed. This anchors per-sample queries — pruning, gating, and any future GC — to a clean indexed `WHERE sample_id = ?` instead of path-prefix string matching, which would be brittle if the canonical mount ever moves (the §4.11 path-migration recipe rewrites `path` but a `sample_id` column survives untouched). It also enables the batched lookup in the gating loop: one indexed SELECT per sample instead of N per-path SELECTs.

Helpers:

```python
def load_sample_state(session, sample_id: str) -> dict[Path, float]:
    """Return {path: mtime} for every scan_state row for this sample. One indexed SELECT."""

def is_file_changed(state: dict[Path, float], path: Path) -> bool:
    """Pure: stat `path`, compare to `state.get(path)`. True if missing from state (first-seen) or mtime differs.
    Caller passes the dict from load_sample_state; no DB hit per call."""

def record_file_scan(session, path: Path, sample_id: str, mtime: float) -> None:
    """Upsert scan_state(path, sample_id, mtime, last_scanned=now)."""

def parse_target_set_changed(state: dict[Path, float], parse_targets: list[Path]) -> bool:
    """True iff `set(parse_targets) != set(state.keys())` — files added or removed since last scan."""

def prune_missing(session, sample_id: str, kept_paths: set[Path]) -> int:
    """DELETE FROM scan_state WHERE sample_id = ? AND path NOT IN kept_paths. Returns row count."""

def load_soft_deleted_ids(session) -> set[str]:
    """Return the set of sample_ids currently soft-deleted (deleted_at IS NOT NULL).
    Called once at the top of scan_root so the per-sample gating loop can force
    re-assembly for any soft-deleted sample that has reappeared on disk — even when
    its files are mtime-unchanged. Without this, the mtime gate would skip the sample
    and never clear deleted_at, leaving the sample dead in the DB despite being back on disk."""

def start_scan(session, scan_run_id: str, root: Path) -> None:
    """INSERT INTO scans(scan_run_id, started_at=now, root=str(root), status='running').
       Also upserts the single-row catalog_meta(data_root) to str(root) so the
       most-recent-scan anchor described in Q4 is kept current."""

def finish_scan(session, scan_run_id: str, *, status: str, report: ScanReport) -> None:
    """UPDATE scans SET ended_at=now, status=?, samples_upserted=?, samples_skipped=?, samples_failed=?
       WHERE scan_run_id = ?."""
```

The `catalog_meta` upsert lives inside `start_scan` (rather than `finish_scan`) so the row is correct even if the scan crashes before completing — the table documents *what root was being scanned*, not *what root last completed*. Implementation: a one-row table with a fixed sentinel PK (e.g. `id INTEGER PRIMARY KEY CHECK (id = 1)`) so `INSERT … ON CONFLICT DO UPDATE` is a clean upsert on both SQLite and Postgres without needing dialect-specific MERGE.

(`content_hash` is reserved for a future case where mtime alone is too coarse — e.g. a touched-but-unchanged file. It's wired into the table now to avoid a later migration; the v1 scanner ignores it.)

Each parser declares its parse-target files (see §4.4); the orchestrator calls `load_sample_state(session, sample_id)` once per sample, then walks the parse-target list in Python via `is_file_changed`. A sample is treated as "unchanged" iff every parse-target file is unchanged AND `parse_target_set_changed` returns False. If any check fails, the whole sample is re-assembled (the assembler operates on a `SampleRecord` granularity — partial re-parsing within a sample isn't worth the complexity).

`force=True` bypasses the gate and re-parses every sample.

**Pruning.** When a sample's parse-target file disappears (e.g. a researcher deletes a tomogram folder), `prune_missing(session, sample_id, kept_paths)` deletes any `scan_state` row scoped to this sample whose path isn't in `kept_paths`. Called by the orchestrator (§4.8) after persistence completes for that sample, with `kept_paths = set(parse_targets)` for the just-completed assembly.

### 4.6 Assembler (`assembler.py`)

The interesting layer. Takes the outputs of all parsers for one sample and produces a single validated `SampleRecord`:

```python
def assemble_sample(
    sample_loc: SampleLocation,
    *,
    on_voxel_mismatch: Literal["warn", "error"] = "warn",
) -> AssemblyResult: ...

@dataclass
class ScanWarning:
    category: str    # 'extra_field' | 'possible_typo' | 'unfilled_placeholder' |
                     # 'missing_acquisition_toml' | 'unparseable_acquisition_toml' |
                     # 'unparseable_mdoc' | 'unparseable_mrc_header' |
                     # 'unparseable_zarr_attrs' | 'ambiguous_frame_extension' |
                     # 'voxel_spacing_implied_mismatch'
    location: str    # dotted path, e.g. "chromatin" or "acquisitions.Pos_86.tomogram[my_tomo]"
    message: str     # human-readable detail

@dataclass
class AssemblyResult:
    record: SampleRecord | None      # None if hard validation failed
    warnings: list[ScanWarning]
    errors: list[str]
    conflicts: list[FieldConflict]   # cross-source disagreements
    extras: list[ExtrasEntry]        # propagated from cryoet_schema.loader (top-level unknown keys per entity)
    tomogram_aux: dict[tuple[str, str, str], dict[str, Any]]
                                     # {(sample_id, acquisition_id, tomogram_id): {
                                     #     "voxel_spacing_angstrom": float | None,        # from MRC header
                                     #     "voxel_spacing_angstrom_implied": float | None  # pixel_size × voxel_bin
                                     # }}
                                     # carries DB-only values that aren't on the Pydantic Tomogram model

@dataclass
class FieldConflict:
    location: str        # e.g., "acquisitions.Position_86.tomogram[my_tomo].voxel_spacing_angstrom"
    category: str        # "voxel_spacing_implied_mismatch", ...
    values: dict[str, Any]   # source name → value, e.g., {"mrc_header": 10.1, "implied (pixel_size*voxel_bin)": 10.0}
    severity: str        # "warning" | "error"
```

**`ScanWarning` construction.** `ScanWarning` is defined in `assembler.py` and imported by `persistence.py`. The assembler is the sole creator of `ScanWarning` objects; persistence is a dumb writer. Three sources:

1. **`LoadResult.warnings: list[str]`** (extra-field, possible-typo, and unfilled-placeholder warnings from `cryoet_schema.loader`) — converted by a small `_categorize_loader_warning(s: str) -> ScanWarning` helper in `assembler.py` that assigns `category` by checking known string prefixes emitted by the loader (`"extra field"` → `'extra_field'`, `"possible typo"` → `'possible_typo'`, `"unfilled"` → `'unfilled_placeholder'`). These prefixes are stable because `cryoet_schema.loader` is their sole source. The `location` field is parsed from the same string (the loader already encodes location in the message). `ScanWarning` lives in `cryoet_catalog`, so the conversion happens here rather than in the schema layer, preserving the one-way dependency.

2. **`ParseResult(status="unreadable")` outcomes** — constructed directly: `ScanWarning(category='unparseable_mdoc'|'unparseable_mrc_header'|'unparseable_zarr_attrs'|'ambiguous_frame_extension', location=<acquisition or tomogram path>, message=result.error)`.

3. **Assembler-generated signals (step 1.5)** — constructed directly: `ScanWarning(category='missing_acquisition_toml'|'unparseable_acquisition_toml', location=f"acquisitions.{acquisition_id}", message=...)`.

`detected_at` and `scan_run_id` are **not** fields on `ScanWarning` — they are persistence-layer concerns. Persistence stamps each row with `time.time()` and the active `scan_run_id` at insert time.

**Merge rules:**

1. Start from the `LoadResult` returned by `toml_files.load_sample_record`: `record` is the validated `SampleRecord` with successfully-parsed acquisitions only, `acquisition_errors` is a dict of `acquisition_id -> error_string` for any `acquisition.toml` that failed to parse or validate, and `extras` is propagated through `AssemblyResult.extras` to persistence unchanged.
1.5. **Synthesize missing/unparseable acquisitions.** Compare the set of acquisition ids in `record.acquisitions` (a `dict[str, AcquisitionFile]`, schema.py:293) against the set yielded by `discovery.iter_acquisitions(sample_loc)`. For each filesystem id not in `record.acquisitions`, construct an empty `AcquisitionFile(acquisition=Acquisition(acquisition_id=acq_id))` (every other field defaults to `None`; `Acquisition` has no required TOML-authored fields, and `AcquisitionFile.tomogram` / `.annotation` default to empty lists) and assign it as `record.acquisitions[acq_id] = ...`. The warning category depends on *why* it's missing from `record`:
   - `acquisition_toml is None` on the `AcquisitionLocation` → `'missing_acquisition_toml'` warning, message names the directory path. (Frames-only directory; researcher hasn't authored a TOML yet.)
   - `acquisition_id` appears in `LoadResult.acquisition_errors` → `'unparseable_acquisition_toml'` warning, message includes the parse error from the loader so the dashboard surfaces *why* it failed, not just *that* it failed. (TOML present but broken.)
   In both cases `location = f"acquisitions.{acquisition_id}"`. Per-acquisition isolation means a single bad TOML no longer prevents the rest of the sample from being cataloged — the bad acquisition gets a placeholder row, MDOC parsing still runs against its `frames_dir`, and tomograms/annotations beneath it are still indexed. Downstream merge steps treat synthesized and TOML-backed acquisitions identically; step 7 (re-validate) confirms the synthesized acquisition still satisfies the model.
2. For each acquisition, run the MDOC + frame-ext parsers and fill in fields that are `None` on the `Acquisition` model (`pixel_size`, `dose_per_tilt`, `total_dose`, `tilt_min`, `tilt_max`, `tilt_axis`, `defocus_per_image`, `date_collected`, `voltage`, `energy_filter_slit_width`, `frame_count`, `camera`). These fields are MDOC/extension-authoritative — the schema says researchers don't enter them — so the only question is "is the parsed value present?". No conflict possible. (Synthesized Frames-only acquisitions go through this step too — MDOC parsing fills in what it can; the rest stay `None`.)
3. For each tomogram folder under `Reconstructions/Tomograms/` (or `SyntheticCryoET/` for simulations), run `mrc_header` + `ome_zarr` and fill in `image_size_x/y/z`, `mrc_path`, `zarr_path`, `zarr_axes`, `zarr_scale` on the Pydantic `Tomogram`. The MRC header's `voxel_size.x` is stored separately in `tomogram_aux[(sample_id, acquisition_id, tomogram_id)]["voxel_spacing_angstrom"]` because that field was removed from `Tomogram` in step 0 (§7) and is now DB-only. `voxel_spacing_angstrom` is MRC-header-authoritative — researchers do not author it (resolved per Q6) — so the only question is "is the MRC readable?".
4. **Cryoet-only consistency check**: when `pixel_size` (MDOC, on the parent acquisition) and `voxel_bin` (TOML, on this tomogram) are both available, compute `voxel_spacing_angstrom_implied = pixel_size × voxel_bin` and compare to the MRC header value using **relative tolerance**: `abs(implied - mrc) / max(abs(implied), abs(mrc), 1.0) < 1e-3`. The relative form is needed because MDOC `pixel_size` typically carries 3–4 sig figs (e.g. `2.93`) while the MRC header carries ~7 (e.g. `11.7197`), and an absolute tolerance like `1e-3 Å` would false-positive on every realistic precision mismatch. On disagreement, record a `FieldConflict` with category `"voxel_spacing_implied_mismatch"` and emit a warning. Store `voxel_spacing_angstrom_implied` in `tomogram_aux[(sample_id, acquisition_id, tomogram_id)]` alongside `voxel_spacing_angstrom` (set to `None` when either `pixel_size` or `voxel_bin` is unavailable). Persistence writes whatever is in `tomogram_aux` — no recomputation. Skipped for simulation samples (no MDOC) and tomograms with null `voxel_bin`. Configurable via `assemble_sample(..., on_voxel_mismatch="warn"|"error")`. **Test:** `test_assembler_merge.py` includes a fixture where MDOC reports `pixel_size = 2.93` (4 sig figs) and the MRC header reports `voxel_size.x = 46.8788` (7 sig figs) with `voxel_bin = 16`; implied is `2.93 × 16 = 46.88`, absolute difference is `0.0012 Å` (would *fail* a `1e-3 Å` absolute tolerance) but relative difference is `0.0012 / 46.88 ≈ 2.6e-5` (passes the `1e-3` relative check). The fixture must accept the pair as consistent — and the check fails to discriminate between the two policies if it ever drifts back toward an absolute form.
5. Compute derived fields: `is_raw = (derived_from == [])` for tomograms; `linker_length_fraction` for chromatin (per schema doc).
6. For each annotation folder, list discovered artifact files and set `Annotation.files`.
7. Re-validate the now-fully-populated `SampleRecord` via `SampleRecord.model_validate(record.model_dump(by_alias=True))` to catch any constraint we just violated. The explicit round-trip is necessary because `_Base` (schema.py:82) does not set `validate_assignment=True`, so direct attribute mutations earlier in the assembler do *not* re-run model validators; without this step `_check_project_blocks`, `_check_acquisition_name_collisions`, and `AcquisitionFile._check_cross_refs` (e.g. duplicate `tomogram_id`s, dangling `derived_from` references, dangling `target_tomogram` references) would silently pass on assembled state. `by_alias=True` preserves `Field(alias="id")` round-tripping for `Tomogram` / `Annotation`. The reassigned `record` is what flows on to persistence.

The output is a single `SampleRecord` plus a list of warnings/conflicts. The persistence layer never has to think about which field came from where.

### 4.7 Persistence (`persistence.py`)

```python
def upsert_sample_record(
    session: Session,
    record: SampleRecord,
    *,
    extras: list[ExtrasEntry],
    tomogram_aux: dict[tuple[str, str, str], dict[str, Any]],
    warnings: list[ScanWarning],
    scan_run_id: str,
) -> None: ...

def soft_delete_missing_samples(
    session: Session,
    fs_sample_ids: set[str],
    *,
    dry_run: bool,
    safety_floor: float,
    report: ScanReport,
) -> None:
    """
    Diff `fs_sample_ids` (sample dirs found on disk this scan) against live samples in the DB.
    Compute `to_delete = live_sample_ids_in_db - fs_sample_ids`. If `len(to_delete) /
    max(1, len(live_sample_ids_in_db)) > safety_floor`, raise `PruneSafetyFloorExceeded`
    (skipped when there are 0 live samples — first scan).

    On dry_run: append to `report.would_soft_delete: list[str]` and return without writing.
    Otherwise: `UPDATE samples SET deleted_at = ? WHERE sample_id IN (...)` and increment
    `report.soft_deleted` by the row count.

    Child entities (acquisitions/tomograms/annotations/aunp/sub-tables/extras/scan_warnings/
    scan_state) are deliberately not touched — soft delete preserves history for resurrection.
    See §4.10 for the full rationale.
    """
```

Steps:
1. Upsert the `samples` row from `record.sample`. Always set `deleted_at = NULL` as part of the upsert payload — if this sample was previously soft-deleted (§4.10) and has now reappeared on disk, the upsert resurrects it.
2. For each 1:1 sub-entity (`chromatin`, `synapse`, `simulation`, `freezing`, `milling`): if the corresponding field on `record.sample` is not `None`, upsert the row; if it *is* `None`, `DELETE FROM <table> WHERE sample_id = ?`. This delete-if-absent step is required — without it, a researcher who removes `[chromatin]` from their TOML leaves a stale `chromatin` row in the DB forever. The delete is a no-op when no row exists, so it's safe to issue unconditionally for absent sub-entities.
3. Upsert each `aunp` row indexed by ordinal. Delete `aunp` rows for this sample whose ordinal is `>= len(record.aunp)` (i.e., the user removed an entry). **Note: `(sample_id, ordinal)` is a positional reference, not a stable identity.** Reordering `[[aunp]]` entries in TOML silently shifts which ordinal points to which entry — external consumers (dashboard URLs, future FKs) must not assume `(sample_id, 0)` refers to the same physical aunp before and after a reorder. If stable aunp identity becomes a requirement in v2, the right fix is a researcher-authored `aunp_id` field on `Aunp` (e.g. `"5nm-protein-A"`) — explicit naming beats hashing because it survives field edits, not just reorders.
4. For each `(acq_id, acq_file)` in `record.acquisitions.items()`: upsert the `acquisitions` row from `acq_file.acquisition`, then upsert each `tomograms` row from `acq_file.tomogram` (singular field name on `AcquisitionFile`). The tomogram upsert merges two sources of values: (a) Pydantic `Tomogram` fields, and (b) `tomogram_aux[(sample_id, acq_id, tomogram_id)]` for DB-only columns computed by the assembler — `voxel_spacing_angstrom` (from the MRC header) and `voxel_spacing_angstrom_implied` (`pixel_size × voxel_bin`, or `None` if either is unavailable). Persistence writes whatever the assembler put in `tomogram_aux`; it does not recompute either value. Then upsert each `annotations` row from `acq_file.annotation`.
5. **Extras refresh:** `DELETE FROM extras WHERE sample_id = ?` for this sample, then insert fresh rows from the `extras` argument. Each `ExtrasEntry` becomes one row: `sample_id = entry.entity_pk[0]` (the denormalized column added in §3), `entity_type` is stored verbatim, `entity_pk_json = json.dumps(list(entry.entity_pk))`, `key = entry.key`, `value_json = json.dumps(entry.value)`. The walker in `cryoet_schema.loader` is the single source of `(entity_type, entity_pk, key)` tuples — persistence does not re-walk the record. Using the denormalized column rather than a JSON-prefix `LIKE` means refresh is a single indexed delete and is safe for sample ids that share a prefix.
6. **Warnings refresh:** delete all `scan_warnings` rows for this `sample_id`, then insert fresh rows from the `warnings` argument. Each `ScanWarning` is written as `(sample_id, category, location, message, detected_at=time.time(), scan_run_id=scan_run_id)` — `detected_at` and `scan_run_id` are added here, not by the assembler. This is the persistent half of the Q7 resolution.
7. Stale-row cleanup: delete `acquisitions`/`tomograms`/`annotations` whose composite PK doesn't appear in this `SampleRecord`. **The keep-list is derived from Python state, not from a fresh SELECT** — `record.acquisitions` is a `dict[str, AcquisitionFile]`, and `AcquisitionFile` carries `tomogram: list[Tomogram]` and `annotation: list[Annotation]` (singular field names, schema.py:245). Walk `record.acquisitions.values()` to collect acquisition PKs, then for each `acq_file` walk `acq_file.tomogram` and `acq_file.annotation` to collect child PKs, then issue `DELETE … WHERE sample_id = ? AND (pk_cols) NOT IN (keep_set)`. Doing it this way means the cleanup is correct regardless of whether the preceding `session.merge()` calls have been flushed: the new PKs are already known from Python and don't need to be read back from the DB. (A SELECT-based approach would miss unflushed merges and risk deleting newly-inserted rows.) Important for the case where a researcher renames or removes a folder; the orchestrator only invokes persistence when it has actually re-assembled the sample, so cleanup runs in lockstep with re-parsing.

Upsert technique: SQLAlchemy 2.0's `insert(...).on_conflict_do_update(...)` is dialect-specific; use `session.merge()` for portability. Slightly slower but works on both SQLite and Postgres.

All operations happen inside a single transaction per sample. On exception, the transaction rolls back and the orchestrator records the sample as failed.

### 4.8 Orchestrator (`scanner.py`)

```python
def scan_root(engine: Engine, root: Path, *, force: bool = False,
              prune: bool = False, prune_dry_run: bool = False,
              prune_safety_floor: float = 0.5,
              on_error: Literal["collect", "raise"] = "collect") -> ScanReport:
    ...
```

Loop:

```
scan_run_id = uuid4().hex          # tags every scan_warnings row from this invocation
state.start_scan(session, scan_run_id, root)              # INSERT scans(scan_run_id, started_at=now, root, status='running')
try:
    # Pre-load soft-deleted sample ids so resurrection is forced even when files are mtime-unchanged.
    # One SELECT before the loop; pure set membership check per sample inside.
    soft_deleted_ids = state.load_soft_deleted_ids(session)

    fs_sample_ids = set()
    for sample_loc in discovery.iter_samples(root):
        fs_sample_ids.add(sample_loc.sample_id)
        parse_targets = discovery.parse_targets_for_sample(sample_loc)   # all files the parsers will read
        sample_state = state.load_sample_state(session, sample_loc.sample_id)   # one indexed SELECT
        is_soft_deleted = sample_loc.sample_id in soft_deleted_ids
        if not force \
           and not is_soft_deleted \
           and not any(state.is_file_changed(sample_state, p) for p in parse_targets) \
           and not state.parse_target_set_changed(sample_state, parse_targets):
            report.skipped += 1
            continue
        try:
            with session.begin():
                result = assembler.assemble_sample(sample_loc)
                report.warnings.extend(result.warnings)    # transient half of Q7
                if result.record is None:
                    report.errors.extend(result.errors)
                    continue
                persistence.upsert_sample_record(
                    session,
                    result.record,
                    extras=result.extras,
                    tomogram_aux=result.tomogram_aux,
                    warnings=result.warnings,              # persistent half of Q7
                    scan_run_id=scan_run_id,
                )
                for p in parse_targets:
                    state.record_file_scan(session, p, sample_loc.sample_id, p.stat().st_mtime)
                state.prune_missing(session, sample_loc.sample_id, kept_paths=set(parse_targets))
                report.upserted += 1
        except Exception as e:
            report.errors.append(f"{sample_loc.sample_id}: {e}")
            if on_error == "raise":
                raise
    if prune:
        persistence.soft_delete_missing_samples(
            session, fs_sample_ids,
            dry_run=prune_dry_run,
            safety_floor=prune_safety_floor,
            report=report,
        )
    state.finish_scan(session, scan_run_id, status='completed', report=report)
except Exception:
    state.finish_scan(session, scan_run_id, status='failed', report=report)
    raise
```

`parse_target_set_changed` compares the current set of parse-target paths to the keys of `sample_state` (the dict pre-loaded by `load_sample_state` for this sample); a change in either direction (file added, file removed) forces a re-assemble. Pure-Python comparison — no extra DB hit beyond the one indexed SELECT already done by `load_sample_state`.

`is_soft_deleted` is checked *before* the mtime gate so that a soft-deleted sample that reappears with unchanged files (e.g., a dir that was moved away and moved back) is always re-assembled. The upsert in §4.7 step 1 clears `deleted_at`; if the mtime gate were allowed to skip the sample, that upsert would never run and the sample would remain dead in the DB despite being visible on disk. `load_soft_deleted_ids` is a single indexed `SELECT sample_id FROM samples WHERE deleted_at IS NOT NULL` executed once before the loop — one DB hit for the whole scan, not one per sample.

`start_scan` inserts a row into `scans` with `status='running'`; `finish_scan` updates `ended_at`, `status`, and the per-status counters from the `ScanReport`. The `scan_run_id` is the dashboard's anchor for "warnings from the most recent scan" — joined against `scan_warnings` via `scan_warnings.scan_run_id`. Rows with `status='running'` indicate either a scan in progress or one that crashed before `finish_scan` could run; the dashboard query filters on `status='completed'` to avoid showing partial state.

`ScanReport` carries counters (`upserted`, `skipped`, `failed`, `soft_deleted`), the warnings list, the errors list, and the conflicts list — printed at the end of a CLI run.

### 4.9 CLI (`cli.py`)

Thin wrapper:

```
python -m cryoet_catalog scan <root>
    [--db sqlite:///path.db] [--force] [--init]
    [--prune] [--prune-dry-run] [--prune-safety-floor 0.5]
```

`--init` runs `init_schema(engine)` to create tables on a fresh DB.

`--prune` enables soft-deletion of samples that exist in the DB but no longer exist on disk; off by default (see §4.10 for rationale). `--prune-dry-run` lists what would be deleted without changing the DB. `--prune-safety-floor` sets the maximum fraction of live samples that may be soft-deleted in a single run; defaults to `0.5` — if more than that would be deleted, the prune step aborts with an error and recommends `--prune-safety-floor 1.0` for the rare case where a large deletion is intentional.

Wire up a corresponding `pixi run scan` task alongside the existing `validate` and `json-schema` tasks (which now point to `python -m cryoet_schema.validate` and `python -m cryoet_schema.generate_json_schema` after the loader extraction in §4.4.1). Standalone `validate` stays in `cryoet_schema` — the catalog CLI doesn't re-export it.

### 4.10 Sample deletion (soft delete + opt-in prune)

A sample directory deleted from disk leaves `samples`, `acquisitions`, `tomograms`, `annotations`, `aunp`, `chromatin`/`synapse`/`simulation`/`freezing`/`milling`, `extras`, `scan_warnings`, and `scan_state` rows behind. Without explicit deletion logic, the catalog accumulates ghosts. v1 strategy: **soft delete, opt-in via `--prune`, with a safety floor**.

**Soft delete, not hard delete.** `samples` carries a DB-only `deleted_at FLOAT NULL` column (Unix timestamp). `soft_delete_missing_samples` sets `deleted_at` on samples that are in the DB but not in `fs_sample_ids` from the just-completed scan. Rows for child entities (`acquisitions` etc.) are not touched — they stay around, joinable by `sample_id`, so a researcher who accidentally moved a directory and re-adds it can recover their warnings, extras, and history. Resurrection is `UPDATE samples SET deleted_at = NULL WHERE sample_id = ?`, done by the orchestrator the next time the directory reappears (the upsert path in §4.7 step 1 explicitly clears `deleted_at`). All catalog/dashboard queries that want live samples must filter `WHERE samples.deleted_at IS NULL` — this is the cost of the soft-delete approach.

**Opt-in via `--prune`.** Default `scan_root` invocations don't soft-delete. The orchestrator collects `fs_sample_ids` from `iter_samples` regardless (used for the diff), but only calls `soft_delete_missing_samples` when `prune=True`. Reasons to be conservative by default: (a) NFS mount drops would otherwise mark every sample deleted in one scan; (b) a rename (`sample_42` → `sample_042`) looks identical to a delete from the orchestrator's perspective; (c) interrupted scans don't reflect the full filesystem, but `iter_samples` always returns whatever it sees. The `--prune` flag is the researcher's affirmative "yes, the missing samples really are gone."

**Safety floor.** `soft_delete_missing_samples` computes `to_delete = (live_samples_in_db) - fs_sample_ids` and `floor = len(to_delete) / max(1, len(live_samples_in_db))`. If `floor > prune_safety_floor` (default `0.5`), the function raises `PruneSafetyFloorExceeded(missing=…, threshold=…)` instead of doing the update — the orchestrator catches it, marks the scan failed, and the CLI prints a message recommending the user either investigate (mount issue?) or rerun with `--prune-safety-floor 1.0` if the large deletion is genuinely intentional. The check is skipped when there are zero live samples in the DB (first scan or fully empty catalog).

**Dry run.** `prune_dry_run=True` runs the diff and the safety-floor check but skips the `UPDATE`. The to-delete sample ids are appended to `report.would_soft_delete: list[str]` and printed by the CLI. Useful for "what would `--prune` actually do?" before committing.

**Out of scope for v1.** Hard delete (CLI flag like `--purge` to actually remove rows for a soft-deleted sample after some grace period), GC of `scan_state` rows for deleted samples (currently they remain — harmless but unbounded), `--prune` over a subset of the data root. Add when a real need surfaces.

### 4.11 Known limitations (v1)

Documented here so they don't become surprise discoveries later.

**Scans are not concurrent.** `scan_root` is single-writer. Running two `scan_root` calls against the same DB simultaneously produces undefined behavior — `catalog_meta` (single-row), `scan_state`, `scans`, and `extras` all see writes from both invocations interleaved. SQLite serializes transactions at the file level so the worst-case is "the second scan blocks until the first commits"; Postgres doesn't, and you can get split state across the two scans (e.g. half the `extras` rows from each, `scans.status='running'` left dangling for the loser). v1 contract: **don't run concurrent scans**. The CLI does not guard against it (no advisory lock, no `pg_advisory_lock`); if cron or a dashboard ever triggers scans, the operator is responsible for serializing them. v2 fix when this becomes a real problem: take an advisory lock keyed on `data_root` at the start of `scan_root` and release it in `finish_scan`.

**Path migration is a manual SQL operation.** The Q4 resolution stores absolute paths anchored at the canonical mount (`/groups/cryoet/cryoet/data`), and `catalog_meta(data_root)` records that anchor. If the canonical mount **moves** to a new prefix (e.g. `/groups/cryoet/cryoet/data` → `/nrs/cryoet/data`) without a compatibility symlink at the old location, every stored path in `mrc_path`, `zarr_path`, `annotations.files`, and `scan_state.path` becomes invalid. `catalog_meta` is *documentation* of the anchor, not a *migration tool* — it does not rewrite paths automatically. The migration recipe is a one-line SQL `UPDATE` per path-bearing column, e.g. `UPDATE tomograms SET mrc_path = REPLACE(mrc_path, '/groups/cryoet/cryoet/data', '/nrs/cryoet/data')`, run inside a transaction along with `UPDATE catalog_meta SET data_root = '/nrs/cryoet/data'`. The recipe works because the absolute paths are prefix-uniform under one canonical root. Symlink migrations *at or above* the stored prefix work transparently (the kernel resolves the symlink at file-open time); only a moved-and-no-symlink case requires the SQL `UPDATE`. v1 contract: **if the mount moves, an admin runs the migration; the catalog does not auto-detect or auto-rewrite**.

### 4.12 API (`cryoet_catalog/api/`)

A read-only FastAPI layer over the same ORM and DB engine used by the scanner. The scanner writes; the API reads. No write endpoints in v1.

**Module responsibilities:**

- **`main.py`** — `create_app() -> FastAPI`: registers routers, adds CORS middleware (origins configurable via `CORS_ORIGINS` env var, defaults to `["http://localhost:5173"]` for the Vite dev server), and wires up a lifespan that reads `CATALOG_DB_URL` (defaulting to `sqlite:///cryoet_catalog.db`) to create and store the engine. Also calls `init_schema(engine)` on startup so a fresh DB gets its tables created automatically — safe to call on an existing DB (`create_all` is idempotent).
- **`deps.py`** — `get_session()`: FastAPI dependency that yields a `Session` from `session_scope(engine)`. The engine is retrieved from `app.state` set during lifespan.
- **`schemas.py`** — Pydantic response models. These are **separate from `cryoet_schema` models** — they are flat, JSON-consumer-shaped output types. For example, `SampleSummary` (list view: id, name, project, data_source, lab, warning count) and `SampleDetail` (full record with nested acquisitions). Keeping them separate means the API can evolve its output shape without touching the validation schema.
- **`routes/`** — one file per resource group.

**Routes:**

| Method | Path | Description |
|---|---|---|
| `GET` | `/samples` | Paginated list of live samples (`deleted_at IS NULL`). Filter params: `project`, `data_source`, `has_warnings` (bool). Returns `list[SampleSummary]`. |
| `GET` | `/samples/{sample_id}` | Full sample record with all sub-entities and acquisitions. Returns `SampleDetail`. 404 if not found or soft-deleted. |
| `GET` | `/samples/{sample_id}/warnings` | All `scan_warnings` rows for this sample from the most recent completed scan. Returns `list[WarningOut]`. |
| `GET` | `/scans` | List of all scan runs, most recent first. Returns `list[ScanOut]`. |
| `GET` | `/scans/latest` | Most recent completed scan row. 404 if no completed scan exists. Used by the dashboard to show last-scan metadata. |
| `GET` | `/extras/summary` | `GROUP BY entity_type, key ORDER BY count DESC`. Returns `list[ExtrasSummaryRow]`. Answers "what informal keys are researchers writing most often?" |

**Key design constraints:**

- All queries filter `samples.deleted_at IS NULL` unless the endpoint is explicitly about scan history.
- No authentication in v1 — the API runs inside Janelia's network where the data is accessible.
- Pagination on `/samples` uses `limit` + `offset` query params (defaults: limit=100, offset=0). No cursor-based pagination in v1.
- The API does not expose `scan_state` rows — those are internal housekeeping.
- CORS is the only concurrency concern: the React dev server runs on a different port than uvicorn, so `CORSMiddleware` is required. Production deployment (same origin, nginx proxy) can restrict origins via env var.

**`test_api.py`:** uses FastAPI's `TestClient` with an in-memory SQLite engine injected via `app.dependency_overrides[get_session]`. Seeds the DB with a known `SampleRecord` via `upsert_sample_record` (reuses the same fixtures as the scanner tests), then asserts response shape, status codes, and filter behaviour. No subprocess, no network, no file I/O.

---

## 5. Testing strategy

Test what matters, don't overtest, no fake tests.

| Test | What it verifies |
|---|---|
| `test_orm_drift.py` | every Pydantic field has a matching ORM column (and vice versa, modulo a DB-only carve-out list) — the contract that pins the two hand-written schemas together |
| `test_parsers.py` | each parser returns the right keys for tiny synthetic fixtures (one MDOC, one MRC, one `.zattrs`) |
| `test_assembler_merge.py` | TOML + MDOC + MRC merge, including the voxel-spacing conflict path |
| `test_loader_isolation.py` | one bad `acquisition.toml` produces an `acquisition_errors` entry but does not prevent the rest of the sample from validating; bad sample.toml returns `record=None` |
| `tests/cryoet_schema/test_walker.py` | the structured extras walker emits the right `(entity_type, entity_pk, key)` tuples for each container shape — single (`sample`, optional sub-entities), positional (`aunp`), and id-keyed (`tomogram`/`annotation` reached via `AcquisitionFile`); regression test for "use `Tomogram.tomogram_id`, not the list index" |
| `test_persistence_roundtrip.py` | upsert a `SampleRecord` + extras list, query rows back, assert equality on every scalar field and extras row; covers `extras` (top-level keys only — nested values stay as JSON blobs by design) and `aunp` ordinal cleanup |
| `test_persistence_idempotent.py` | upserting twice produces the same final state; deleted folders disappear from the DB |
| `test_prune.py` | `--prune` soft-deletes missing samples (sets `deleted_at`); resurrection clears `deleted_at` when the directory reappears; the safety floor aborts when too many samples would be deleted; dry-run reports without modifying |
| `test_scanner_e2e.py` | run `scan_root` against a temp dir laid out per the README, against an in-memory SQLite |
| `test_api.py` | FastAPI `TestClient` + in-memory SQLite; seeds via `upsert_sample_record`, asserts response shape, status codes, filter params, and 404 behaviour for soft-deleted samples |

Fixtures: tiny sample directory tree under `tests/cryoet_catalog/fixtures/`, with synthetic `sample.toml`, `acquisition.toml`, a single-tilt `.mdoc` (text file, easy to author), a 16-byte MRC stub (mrcfile can write these), and a minimal `.ome.zarr/.zattrs`. The same fixtures are reused by `test_api.py` — seed the DB with `upsert_sample_record`, then hit the API rather than standing up a real scan.

Do **not** stand up a real Postgres in CI for v1. The drift-test + SQLAlchemy abstraction is the contract; a manual smoke run against a Postgres instance gates the production switch. The React frontend has no automated tests in v1; manual browser testing against a local `uvicorn` + `npm run dev` stack is sufficient until the UI stabilises.

---

## 6. Open questions

1. ~~**Wide nullable columns vs sub-tables.**~~ **Resolved: sub-tables.** `chromatin`/`synapse`/`simulation`/`freezing`/`milling` each get their own table keyed by `sample_id`. Reasons: (a) the README's coverage-comparison query (cryoet vs simulation across matching conditions) needs the same self-join on chromatin condition columns either way, so the wide form gains nothing on the central use case; (b) sub-tables document `chromatin XOR synapse` and `cryoet XOR simulation` mutual exclusivity in the schema itself, instead of letting `samples` grow to 30+ mostly-`NULL` columns; (c) adding a future project type (e.g. `membrane`) means adding one new table rather than ALTERing one big one. If the portal later wants a flat per-sample view for convenience, add a `samples_flat` SQL view on top of the sub-tables rather than reshaping the underlying schema.
2. ~~**Existing `scripts/validate.py`.**~~ **Resolved: extract a library + move both scripts into `cryoet_schema/`.** Split the merge logic into `cryoet_schema/loader.py` (library, imported by both the validate CLI and the catalog scanner). Move `validate.py` and `generate_json_schema.py` from `scripts/` into `cryoet_schema/` so the package is self-contained (definition + loader + CLI tools). Delete `scripts/`. Pixi tasks switch to `python -m cryoet_schema.…` invocations; user-facing commands unchanged.
3. ~~**`extras` representation.**~~ **Resolved: flat KV table, queryable but not round-trippable.** One global `extras(entity_type, entity_pk_json, key, value_json)` table. The dominant query is "which informal keys are researchers writing, on which entity types, how often?" — that's the whole point of `extra="allow"` (lets schema catch up to ad-hoc usage), and the KV shape makes it a one-line `GROUP BY` instead of a `UNION ALL` across every main table. **Scope:** rows store *top-level* unknown keys per entity. If a value is itself a nested dict (e.g. `unknown_block = { foo = 1 }`), the inner keys remain inside the JSON blob and are not flattened into separate rows; finding them requires `json_extract` / `->>` rather than a `GROUP BY`. This is acceptable because nested unknowns signal a missing first-class field — the right fix is to promote the inner keys onto the Pydantic model, not to flatten them in the catalog. **Source of truth:** the structured walker in `cryoet_schema.loader` produces a single `list[ExtrasEntry]` consumed by both the validate CLI (warning printing) and the catalog (persistence) — see §4.4.1. If Postgres `JSONB` per-key indexing becomes attractive later, an `extras_json` column can be added alongside the KV table without removing it — same data, two access paths.
4. ~~**Path-derived fields.**~~ **Resolved: absolute paths + a `catalog_meta(data_root)` row.** The data lives at exactly one canonical mount (`/groups/cryoet/cryoet/data`), and the only file-opening consumer (the portal backend) lives inside Janelia where it can see that mount — so the multi-mount argument for parameterized paths doesn't apply. Store absolute paths directly in `mrc_path`, `zarr_path`, and `annotations.files`. Use `Path.absolute()` (not `Path.resolve()`) so symlinks are preserved — the cluster path is the canonical reference, and if storage migrates and admins re-symlink at or above the stored prefix, stored paths follow without any DB change. Add a single-row `catalog_meta(data_root TEXT)` table updated on every scan; it documents the anchor used at scan time. **Migration caveat:** if the canonical mount moves *without* a compatibility symlink at the old location, stored paths break — `catalog_meta` is documentation, not an auto-migration mechanism, and the fix is a manual SQL `UPDATE` (recipe in §4.11). Acceptable cost given a single canonical mount and rare migrations; revisit if multi-mount becomes a real requirement.
5. ~~**Auto-gen approach.**~~ **Resolved: hand-write both schemas, pin with a drift test.** Pydantic stays the validation contract in `cryoet_schema/schema.py`; SQLAlchemy declarative classes are hand-written in `cryoet_catalog/orm.py`; a drift test (~80 lines) asserts they agree on every field and column. Two ~200-line definitions plus a test, no introspection. Predictable and debuggable, sidesteps the edge cases an auto-gen approach would have to handle (`Annotated[str, AfterValidator]` for `IdStr` is not detectable via `issubclass`; required-vs-optional Enum unions need `Annotated` unwrapping; `default_factory=list` fields are required-with-default but not "non-null in TOML"; SQLAlchemy 2.0 declarative doesn't accept dynamically-built `Table` objects without losing `Mapped[...]` typing). Rejected alternatives: (a) Pydantic-introspection — fragile for the items just listed; (b) SQLModel — forces SQLAlchemy as a transitive dep for every consumer of `cryoet_schema` (conflicts with Q2's contract-package framing) and DB-only fields like `ordinal` would leak into TOML validation and the JSON Schema export. Cost of the chosen approach: when a field is added to one side, the drift test fails until it's added to the other — explicit work instead of magic, which is what we want for a schema that's still evolving.
6. ~~**Conflict policy default.**~~ **Resolved: schema corrected — `voxel_spacing_angstrom` is MRC-header-only, not TOML-authored.** Per a separate design decision, researchers author only `voxel_bin` (a processing choice they alone know); `voxel_spacing_angstrom` is read from the MRC header by the catalog and is no longer in any TOML. There is therefore no TOML-vs-MRC conflict to resolve. The only consistency check that remains is **cryoet-only**: when `pixel_size` (MDOC) and `voxel_bin` (TOML) are both available, compute `pixel_size × voxel_bin` and compare to the MRC header's `voxel_size.x` using **relative tolerance** (`abs(a-b) / max(|a|,|b|,1) < 1e-3`); on disagreement, emit a categorized warning (`"voxel_spacing_implied_mismatch"`) into `ScanReport.conflicts`. Relative tolerance is required because MDOC and MRC carry different precisions for the same physical quantity — see §4.6 step 4. Skipped for simulation samples (no MDOC, so no `pixel_size`) and for any tomogram with null `voxel_bin`. Persist `voxel_spacing_angstrom` (MRC header value, canonical) and `voxel_spacing_angstrom_implied` (`pixel_size × voxel_bin` when computable, NULL otherwise) so disagreements are queryable. Configurable via `on_voxel_mismatch="warn"|"error"`. Default `"warn"`. **Precondition:** `voxel_spacing_angstrom` must be removed from the `Tomogram` Pydantic model entirely (so a researcher who tries to write it in TOML lands in `model_extra` and gets the unknown-field warning) — see step 0 in §7.
7. ~~**Scope of `extras` warnings.**~~ **Resolved: propagate to `ScanReport.warnings`, persist in a `scan_warnings` DB table, anchor by a `scans` table.** The schema's `extra="allow"` design and the rapidfuzz typo detection in `cryoet_schema/schema.py` are only useful if the warnings reach researchers — surfacing them only in scanner stdout breaks that feedback loop. Persisting warnings to a queryable table lets the portal dashboard close the loop: a researcher edits a TOML, the next scan refreshes their sample's warnings, and the dashboard shows them. The same table holds `voxel_spacing_implied_mismatch` warnings from Q6 and any future cross-source categories — one unified shape, one queryable surface. **Most-recent-scan anchor:** a `scans(scan_run_id PK, started_at, ended_at, root, status, …)` table records one row per `scan_root` invocation; the dashboard query for "warnings from the most recent scan" is `SELECT scan_run_id FROM scans WHERE status='completed' ORDER BY ended_at DESC LIMIT 1` joined against `scan_warnings.scan_run_id`. Without this anchor, the dashboard would have no way to distinguish "warnings from the most recent successful scan" from leftover rows during a partial scan failure. The `scans` table also gives scan history for free. See §3 and §4.8 for the table definition and orchestrator wiring. **UX note:** a researcher's "edit TOML, see results immediately" flow requires a scan to run between the edit and the dashboard view. The v1 scanner is a CLI tool; auto-scan triggers (file watcher, on-demand scan via the dashboard, etc.) are out of scope but should be considered future work.

---

## 7. Suggested implementation order

0. **Schema correction (precondition for Q6).** Remove `voxel_spacing_angstrom` from the `Tomogram` Pydantic model entirely — not just unmark it as TOML-authored. With `extra="allow"` on the base model, a researcher who keeps writing `voxel_spacing_angstrom = X` in TOML now lands in `Tomogram.model_extra` and triggers the existing unknown-field warning, which closes the silent-overwrite hole that "field stays on the model, catalog overwrites it" would have left. The MRC-header value lives only on the DB side: `tomograms.voxel_spacing_angstrom` is a DB-only column on `TomogramORM` (added to the `db_only_columns` carve-out in the drift test, alongside `voxel_spacing_angstrom_implied`). The assembler (§4.6) transports MRC-header-derived values to persistence via a `tomogram_aux: dict[(sample_id, acquisition_id, tomogram_id), {"voxel_spacing_angstrom": float}]` field on `AssemblyResult`; persistence (§4.7 step 4) merges it into the tomograms upsert. Concrete file changes:
   - `cryoet_schema/schema.py`: delete the `voxel_spacing_angstrom` field from `Tomogram`.
   - `cryoet_schema/schema_info.md` Section 3: change the source row from `acquisition.toml ↔ MRC header` to `MRC header`; note that `voxel_spacing_angstrom` is a DB-only column populated by the catalog scanner.
   - `README.md` lines 65 and 139–153: remove `voxel_spacing_angstrom` from the prose and the example `[[tomogram]]` block.
   - `templates/acquisition.toml`: remove the `voxel_spacing_angstrom = "<FILL IN>"` line.
   - Regenerate `cryoet_schema/schema.json`.
   - Existing tests should pass (no fixture currently sets `voxel_spacing_angstrom`); `tests/test_generate_json_schema.py::test_committed_schema_matches_pydantic_models` re-passes after the regeneration.
1. **Loader extraction (§4.4.1).** Concrete substeps:
   - Create `cryoet_schema/loader.py` with `load_sample_record(sample_dir: Path) -> LoadResult` (the 5-field dataclass defined in §4.4.1: `record`, `sample_errors`, `acquisition_errors`, `warnings`, `extras`), the `LoadResult` and `ExtrasEntry` dataclasses, and the structured walker (refactor of `_walk_extras`). Pure library, no argparse or printing.
   - Create `cryoet_schema/validate.py` (CLI) calling `load_sample_record`, pretty-printing `sample_errors`, `acquisition_errors`, `warnings`, and `extras`, returning an exit code. Invokable as `python -m cryoet_schema.validate <sample_dir>`.
   - Move `scripts/generate_json_schema.py` to `cryoet_schema/generate_json_schema.py` unchanged. Invokable as `python -m cryoet_schema.generate_json_schema`.
   - Delete `scripts/` (including `scripts/__init__.py`).
   - **Migrate `tests/test_validate_sample.py` and `tests/test_id_validation.py`.** This is the largest substep. Both files import `validate_dir` from `scripts.validate` and unpack it as a 3-tuple `record, errors, warnings = validate_dir(...)`. Every call site must be updated (roughly 20+ in `test_validate_sample.py`, ~10 in `test_id_validation.py`):
     - Change the import to `from cryoet_schema.loader import load_sample_record` (and `from cryoet_schema.validate import main` where `main` is tested).
     - Replace `record, errors, warnings = validate_dir(tmp_path)` with `result = load_sample_record(tmp_path)`.
     - Replace `errors` references with `result.sample_errors`, `warnings` with `result.warnings`, `record` with `result.record`.
     - **Behavioral break — per-acquisition isolation.** The current `validate_dir` validates the entire merged dict in one shot: a bad `acquisition.toml` causes `record=None`. The new loader validates each acquisition independently, so a bad `acquisition.toml` produces a non-`None` record with that acquisition absent and its error in `result.acquisition_errors`. Any test that asserts `record is None` after a bad `acquisition.toml` must be rewritten: assert `result.record is not None`, assert the bad acquisition id is in `result.acquisition_errors`, and assert the rest of the sample validated successfully. Tests for a bad `sample.toml` (the whole record unrecoverable) keep the `record is None` pattern unchanged.
     - Add new tests for `result.acquisition_errors` and `result.extras` where there is no coverage today (the old 3-tuple had no `extras` return value).
   - Update `tests/test_generate_json_schema.py` import to `from cryoet_schema.generate_json_schema import ...`.
   - Update `pixi.toml` `validate` and `json-schema` task definitions to `python -m cryoet_schema.validate` / `python -m cryoet_schema.generate_json_schema`. User-facing `pixi run validate <dir>` and `pixi run json-schema` are unchanged.
   - Run the full test suite — every test that previously passed still passes (modulo the intentional behavioral rewrites above).
2. Add root `pyproject.toml` (§2.5) + update `pixi.toml` with `[feature.catalog]`, `[feature.api]`, and updated `[environments]`. Verify `pixi run -e catalog python -c "import cryoet_catalog"` works before writing any catalog code.
3. `cryoet_catalog/db.py` + skeleton `orm.py` with two hand-written tables (`samples`, `acquisitions`) + drift test → proves the hand-write-and-pin pattern (including the canonical type mapping and the DB-only carve-out shape).
4. Expand `orm.py` to all remaining tables; add their `(pydantic_cls, orm_cls, db_only_columns)` entries to the drift test until it passes.
5. `state.py` + `discovery.py` (no parsing, just walking) + a unit test that walks a fixture tree.
6. `parsers/toml_files.py` (delegates to `cryoet_schema.loader`); add MDOC, MRC, zarr, frame-ext parsers.
7. `assembler.py` with the merge + conflict policy.
8. `persistence.py` with `session.merge()` upserts and stale-row cleanup.
9. `scanner.py` orchestrator + `cli.py`.
10. End-to-end scanner test on the fixture tree.
11. **API (`cryoet_catalog/api/`).** `main.py` + `deps.py` + `schemas.py` + the four route files; `test_api.py` using `TestClient` with dependency override. No frontend required to test — `curl` or the auto-generated `/docs` (Swagger UI) is sufficient during development.
12. **Frontend scaffold.** `npm create vite@latest frontend -- --template react-ts`. Wire up the first real endpoint (`GET /samples`) end-to-end in the browser before adding more routes. This step is a stub — full UI implementation is out of scope for this plan.
13. Manual smoke run against a Postgres instance to validate the dialect-portability claim.
