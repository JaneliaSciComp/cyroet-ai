"""Pydantic schema for CryoET + AI sample metadata.

Covers every field in schema_info.md. Fields are grouped by authoritative source within each class:

- sample.toml / acquisition.toml — researcher-authored; required on ingest only for ``sample.data_source`` and ``sample.project``.
- MDOC — parsed from ``.mdoc`` files under each acquisition's ``Frames/``.
- MRC header — read from tomogram ``.mrc`` headers.
- OME-Zarr .zattrs — read from multiscale ``.ome.zarr`` arrays.
- frame extension — ``.eer`` / ``.tiff`` implies camera family.
- directory — implicit from sample / acquisition / processing folder names. Entity IDs (``sample_id``, ``acquisition_id``, ``tomogram_id``, ``annotation_id``) carry the folder name. For tomograms and annotations the TOML-authored ``id`` field is accepted as an alias for the same value; for samples and acquisitions the IDs are injected on load from the directory structure.
- derived — computed on ingest from other fields.

All auto-derived fields are optional so the validator can load a TOML-only
sample directory before the ingest pipeline has run. Unknown fields are
preserved (``extra='allow'``) and reported as warnings rather than errors.
"""

from __future__ import annotations

import datetime as _dt
import re as _re
import warnings as _warnings
from enum import Enum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from rapidfuzz import fuzz, process

_TYPO_SCORE_CUTOFF = 80

# Identity fields (sample_id, acquisition_id, tomogram_id, annotation_id) become
# DB primary keys and live inside path strings, URLs, and shell commands, so we
# restrict them to a conservative, cross-platform-safe allowlist.
_ID_MAX_LEN = 128
_ID_PATTERN = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _validate_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("id must be a string")
    if not value:
        raise ValueError("id must not be empty")
    if len(value) > _ID_MAX_LEN:
        raise ValueError(f"id must be at most {_ID_MAX_LEN} characters")
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError(
            "id must start with [A-Za-z0-9] and contain only letters, digits, "
            "'.', '_', or '-' (no spaces, slashes, or other punctuation)"
        )
    if value.endswith((".", "-")):
        raise ValueError("id must not end with '.' or '-'")
    if ".." in value:
        raise ValueError("id must not contain '..'")
    if value.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"id '{value}' is a reserved name on Windows")
    return value


IdStr = Annotated[str, AfterValidator(_validate_id)]


def _case_insensitive_duplicates(values, label: str) -> list[str]:
    """Return error strings for any case-insensitive collisions among `values`."""
    seen: dict[str, str] = {}
    problems: list[str] = []
    for v in values:
        key = v.casefold()
        if key in seen and seen[key] != v:
            problems.append(
                f"{label} '{v}' collides case-insensitively with '{seen[key]}'"
            )
        else:
            seen.setdefault(key, v)
    return problems


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @model_validator(mode="after")
    def _warn_extra_field_typos(self):
        extras = self.model_extra or {}
        if not extras:
            return self
        known: set[str] = set()
        for fname, finfo in type(self).model_fields.items():
            known.add(fname)
            if finfo.alias:
                known.add(finfo.alias)
        known -= set(extras)
        if not known:
            return self
        for name in extras:
            match = process.extractOne(
                name, known, scorer=fuzz.ratio, score_cutoff=_TYPO_SCORE_CUTOFF
            )
            if match is None:
                continue
            suggestion, score, _ = match
            _warnings.warn(
                f"extra field '{name}' on {type(self).__name__} "
                f"closely matches known field '{suggestion}' "
                f"(similarity {score:.0f}); possible typo",
                UserWarning,
                stacklevel=2,
            )
        return self


class DataSource(str, Enum):
    cryoet = "cryoet"
    simulation = "simulation"


class Project(str, Enum):
    chromatin = "chromatin"
    synapse = "synapse"


class Sample(_Base):
    # directory (sample folder name, injected on load)
    sample_id: IdStr | None = None
    # sample.toml ([sample])
    data_source: DataSource
    project: Project
    type: str | None = None
    cell_type: str | None = None
    description: str | None = None


class Simulation(_Base):
    dataset_type: str | None = None


class Chromatin(_Base):
    # sample.toml ([chromatin])
    substrate: str | None = None
    linker_length_bp: float | None = None
    linker_pattern: list[int] | None = None
    linker_distribution: str | None = None
    buffer: str | None = None
    ptm: str | None = None
    histone_variants: str | None = None
    transcription_factors: str | None = None
    nucleosome_count: int | None = None
    dna_length_bp: int | None = None
    nucleosome_uM: float | None = None
    sequence_identity: str | None = None
    nucleosome_footprint: list[int] | None = None
    # derived (sequence_footprint - 1; computed on ingest)
    linker_length_fraction: float | None = None


class Synapse(_Base):
    label_target: str | None = None
    label_strategy: str | None = None


class Aunp(_Base):
    size_nm: float | None = None
    type: str | None = None
    fluorophore: str | None = None
    concentration_value: float | None = None
    concentration_unit: str | None = None
    conjugation: str | None = None
    conjugation_target: str | None = None
    notes: str | None = None


class Freezing(_Base):
    grid_type: str | None = None
    cryoprotectant: str | None = None
    method: str | None = None
    planchette_size: str | None = None
    spacer_thickness: str | None = None


class Milling(_Base):
    scheme: str | None = None
    date: _dt.date | None = None


class Acquisition(_Base):
    # directory (acquisition folder name, injected on load)
    acquisition_id: IdStr | None = None
    # acquisition.toml ([acquisition])
    resolution: float | None = None          # angstrom
    tilt_spacing: float | None = None        # degrees
    defocus_range: str | None = None         # micrometres, free-text range
    energy_filter: str | None = None
    phase_plate: bool | None = None
    microscope: str | None = None
    # MDOC
    pixel_size: float | None = None          # angstrom
    dose_per_tilt: list[float] | None = None # e/Å² per tilt
    total_dose: float | None = None          # e/Å², summed
    tilt_min: float | None = None            # degrees
    tilt_max: float | None = None            # degrees
    tilt_axis: float | None = None           # degrees
    defocus_per_image: list[float] | None = None  # micrometres, per tilt
    date_collected: _dt.date | None = None
    voltage: float | None = None             # kV
    energy_filter_slit_width: float | None = None  # eV
    frame_count: int | None = None
    # .eer / .tiff (frame extension)
    camera: str | None = None


class Tomogram(_Base):
    # directory / acquisition.toml [[tomogram]] (folder name = tomogram_id = TOML `id`)
    tomogram_id: IdStr = Field(alias="id")
    pipeline: str | None = None
    software: str | None = None
    voxel_bin: int | None = None
    voxel_spacing_angstrom: float | None = None      # cross-checked with MRC header
    derived_from: list[IdStr] = Field(default_factory=list)
    # derived
    is_raw: bool | None = None                        # derived_from == []
    # MRC header
    image_size_x: int | None = None
    image_size_y: int | None = None
    image_size_z: int | None = None
    # directory (prescribed layout)
    mrc_path: str | None = None
    zarr_path: str | None = None
    # OME-Zarr .zattrs
    zarr_axes: str | None = None
    zarr_scale: list[float] | None = None


class Annotation(_Base):
    # directory / acquisition.toml [[annotation]] (folder name = annotation_id = TOML `id`)
    annotation_id: IdStr = Field(alias="id")
    type: str | None = None
    target_tomogram: IdStr | None = None
    # directory scan (artifacts discovered in the annotation folder)
    files: list[str] = Field(default_factory=list)


class AcquisitionFile(_Base):
    """Parsed contents of one acquisition.toml."""

    acquisition: Acquisition
    tomogram: list[Tomogram] = Field(default_factory=list)
    annotation: list[Annotation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_cross_refs(self) -> "AcquisitionFile":
        tomo_ids = {t.tomogram_id for t in self.tomogram}
        problems: list[str] = []
        problems.extend(_case_insensitive_duplicates(
            (t.tomogram_id for t in self.tomogram), "tomogram id"
        ))
        problems.extend(_case_insensitive_duplicates(
            (a.annotation_id for a in self.annotation), "annotation id"
        ))
        for t in self.tomogram:
            for ref in t.derived_from:
                if ref not in tomo_ids:
                    problems.append(
                        f"tomogram '{t.tomogram_id}' derived_from references unknown tomogram '{ref}'"
                    )
        for a in self.annotation:
            if a.target_tomogram is not None and a.target_tomogram not in tomo_ids:
                problems.append(
                    f"annotation '{a.annotation_id}' target_tomogram '{a.target_tomogram}' "
                    f"not found in this acquisition"
                )
        if problems:
            raise ValueError("; ".join(problems))
        return self


class SampleRecord(_Base):
    """Merged sample.toml + every acquisition.toml under the sample directory.

    `acquisitions` is keyed by acquisition directory name (path-injected by the
    validator, not authored in the TOML).
    """

    sample: Sample
    simulation: Simulation | None = None
    chromatin: Chromatin | None = None
    synapse: Synapse | None = None
    aunp: list[Aunp] = Field(default_factory=list)
    freezing: Freezing | None = None
    milling: Milling | None = None
    acquisitions: dict[str, AcquisitionFile] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_project_blocks(self) -> "SampleRecord":
        if self.sample.project == Project.chromatin and self.synapse is not None:
            raise ValueError("sample.project is 'chromatin' but a [synapse] block is present")
        if self.sample.project == Project.synapse and self.chromatin is not None:
            raise ValueError("sample.project is 'synapse' but a [chromatin] block is present")
        if self.sample.data_source == DataSource.cryoet and self.simulation is not None:
            raise ValueError(
                "sample.data_source is 'cryoet' but a [simulation] block is present"
            )
        return self

    @model_validator(mode="after")
    def _check_acquisition_name_collisions(self) -> "SampleRecord":
        problems = _case_insensitive_duplicates(self.acquisitions.keys(), "acquisition id")
        if problems:
            raise ValueError("; ".join(problems))
        return self
