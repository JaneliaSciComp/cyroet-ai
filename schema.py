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
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class DataSource(str, Enum):
    cryoet = "cryoet"
    simulation = "simulation"


class Project(str, Enum):
    chromatin = "chromatin"
    synapse = "synapse"


class Sample(_Base):
    # directory (sample folder name, injected on load)
    sample_id: str | None = None
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
    acquisition_id: str | None = None
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
    tomogram_id: str = Field(alias="id")
    pipeline: str | None = None
    software: str | None = None
    voxel_bin: int | None = None
    voxel_spacing_angstrom: float | None = None      # cross-checked with MRC header
    derived_from: list[str] = Field(default_factory=list)
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
    annotation_id: str = Field(alias="id")
    type: str | None = None
    target_tomogram: str | None = None
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
