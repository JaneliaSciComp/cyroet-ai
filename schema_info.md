# Database Model: CryoET + AI Portal

This document enumerates every field that will be stored in the portal database, organized by entity (Sample → Acquisition → Tomogram → Annotation). For each field it lists the data type and the **authoritative source**:

| Source | What it means |
|---|---|
| `sample.toml` | Researcher-authored sample-level metadata — one file at the sample root. Field definitions live in `conditions.json`. Section shown in parentheses. |
| `acquisition.toml` | Researcher-authored per-acquisition parameters and processing log (`[[tomogram]]`, `[[annotation]]` entries) — one file in each acquisition directory. Section shown in parentheses. |
| `MDOC` | Parsed from `.mdoc` files in the `Frames/` directory by `ingest_mdoc.py`. |
| `.eer` / `.tiff` | Derived from frame file extension or EER header metadata. |
| `MRC header` | Read from the `.mrc` file header on ingest. |
| `OME-Zarr .zattrs` | Read from the multiscale metadata in `.ome.zarr` arrays. |
| `directory` | Implicit from the prescribed directory structure (sample dir name, acquisition dir name, processing folder name). |
| `derived` | Computed on ingest from other DB fields (e.g., tilt range formatted string). |

Researcher-authored fields live in one of two files: sample-level metadata in `sample.toml` at the sample root, and per-acquisition parameters plus the processing log in `acquisition.toml` inside each acquisition directory. Both files are governed by `conditions.json`; the section in parentheses identifies the TOML table (`[sample]`, `[chromatin]`, `[acquisition]`, `[[tomogram]]`, etc.). Fields coming from any other source are **not** entered by researchers and are not duplicated in either TOML (no-duplication principle).

**Key annotations**: `(PK)` marks a **primary key** — the column that uniquely identifies each row in that table. `(FK)` marks a **foreign key** — a column whose value references the primary key of another table, used to link rows across entities (e.g., a tomogram's `acquisition_id` points back to its parent acquisition row).

Every item in the researcher's requested metadata list is cross-referenced in the rightmost column as `[researcher: <label>]`.

---

## 1. Sample entity

One row per sample. Primary key: `sample_id` (the sample directory name).

| Field | Type | Source | Notes / researcher mapping |
|---|---|---|---|
| `sample_id` | text (PK) | `directory` | Sample folder name. |
| `data_source` | enum | `sample.toml` (`[sample]`) | `cryoet` or `simulation`. |
| `project` | enum | `sample.toml` (`[sample]`) | `chromatin` or `synapse`. |
| `type` | text | `sample.toml` (`[sample]`) | e.g. `cellular` / `reconstituted`. [researcher: Cellular vs Reconstituted branch] |
| `cell_type` | text | `sample.toml` (`[sample]`) | Required when `type = cellular`. [researcher: Cell type] |
| `description` | text | `sample.toml` (`[sample]`) | Free text. |

### 1a. Chromatin sub-entity (one row per sample when `project = chromatin`)

| Field | Type | Source | Notes / researcher mapping |
|---|---|---|---|
| `substrate` | text | `sample.toml` (`[chromatin]`) | e.g. `synthetic` / `native` / `n/a`. [researcher: Synthetic arrays vs Native sequences] |
| `linker_length_bp` | float | `sample.toml` (`[chromatin]`) | Homogenous linker length. [researcher: Linker length (homogenous)] |
| `linker_pattern` | list[int] | `sample.toml` (`[chromatin]`) | Patterned linker lengths. [researcher: Linker length (patterned)] |
| `linker_distribution` | text | `sample.toml` (`[chromatin]`) | Free-text distribution description. [researcher: Linker length (distribution)] |
| `buffer` | text | `sample.toml` (`[chromatin]`) | Monovalent/divalent species + conc + additives. [researcher: buffer conditions] |
| `ptm` | text | `sample.toml` (`[chromatin]`) | [researcher: post translational modifications] |
| `histone_variants` | text | `sample.toml` (`[chromatin]`) | [researcher: histone variants] |
| `transcription_factors` | text | `sample.toml` (`[chromatin]`) | [researcher: Transcription factors / binding proteins] |
| `nucleosome_count` | integer | `sample.toml` (`[chromatin]`) | [researcher: nucleosome number] |
| `dna_length_bp` | integer | `sample.toml` (`[chromatin]`) | [researcher: DNA length] |
| `nucleosome_uM` | float | `sample.toml` (`[chromatin]`) | [researcher: nucleosome concentration] |
| `sequence_identity` | text | `sample.toml` (`[chromatin]`) | Native-substrate only. [researcher: sequence identity] |
| `nucleosome_footprint` | list | `sample.toml` (`[chromatin]`) | Native-substrate only. [researcher: nucleosome footprint] |
| `linker_length_fraction` | float | `derived` | `sequence_footprint − 1`; computed on ingest. [researcher: linker length (size footprint-1)] |

### 1b. Synapse sub-entity (one row per sample when `project = synapse`)

| Field | Type | Source | Notes |
|---|---|---|---|
| `label_target` | text | `sample.toml` (`[synapse]`) | e.g. glutamate_receptor, AMPA. |
| `label_strategy` | text | `sample.toml` (`[synapse]`) | e.g. single_label, dual_label. |

### 1c. AuNP labeling sub-entity (0..N per sample)

[researcher: Gold NP's]

| Field | Type | Source | Notes / researcher mapping |
|---|---|---|---|
| `size_nm` | float | `sample.toml` (`[aunp]`) | [researcher: Size] |
| `type` | text | `sample.toml` (`[aunp]`) | [researcher: type] |
| `fluorophore` | text | `sample.toml` (`[aunp]`) | [researcher: Fluorophore] |
| `concentration_value` | float | `sample.toml` (`[aunp]`) | Numeric concentration. [researcher: Concentration] |
| `concentration_unit` | text | `sample.toml` (`[aunp]`) | Unit string, e.g. `nM`, `µg/mL`. Kept separate so the numeric value is filterable/sortable in the UI. |
| `conjugation` | text | `sample.toml` (`[aunp]`) | Fab / nanobody / chemical_tag / none. [researcher: Conjugation partner] |
| `conjugation_target` | text | `sample.toml` (`[aunp]`) | e.g. GluA2. [researcher: Conjugation partner target] |
| `notes` | text | `sample.toml` (`[aunp]`) | |

### 1d. Freezing sub-entity (one per sample)

[researcher: Freezing conditions]

| Field | Type | Source | Notes / researcher mapping |
|---|---|---|---|
| `grid_type` | text | `sample.toml` (`[freezing]`) | [researcher: grid type] |
| `cryoprotectant` | text | `sample.toml` (`[freezing]`) | [researcher: cryo protectant] |
| `method` | text | `sample.toml` (`[freezing]`) | `plunge_frozen` / `HPF`. [researcher: freezing method] |
| `planchette_size` | text | `sample.toml` (`[freezing]`) | HPF only. [researcher: planchette size] |
| `spacer_thickness` | text | `sample.toml` (`[freezing]`) | HPF only. [researcher: spacer thickness] |

### 1e. Milling sub-entity (one per sample)

[researcher: Milling]

| Field | Type | Source | Notes / researcher mapping |
|---|---|---|---|
| `scheme` | text | `sample.toml` (`[milling]`) | [researcher: milling scheme] |
| `date` | date | `sample.toml` (`[milling]`) | YYYY-MM-DD. [researcher: date] |

---

## 2. Acquisition entity

One row per imaging position. Primary key: `(sample_id, acquisition_id)`.

[researcher: Tomogram level → Acquisition Scheme + Acquisition Type]

| Field | Type | Source | Notes / researcher mapping |
|---|---|---|---|
| `acquisition_id` | text (PK) | `directory` | Acquisition folder name, e.g. `Position_86`. |
| `sample_id` | text (FK) | `directory` | Parent sample directory name. |
| `resolution` | float | `acquisition.toml` (`[acquisition]`) | Angstrom. Nominal target. [researcher: Resolution] |
| `tilt_spacing` | float | `acquisition.toml` (`[acquisition]`) | Degrees. Nominal step. [researcher: tilt spacing] |
| `defocus_range` | text | `acquisition.toml` (`[acquisition]`) | Micrometres, free-text range. [researcher: defocus range] |
| `energy_filter` | text | `acquisition.toml` (`[acquisition]`) | Model name. [researcher: energy filter] |
| `phase_plate` | boolean | `acquisition.toml` (`[acquisition]`) | [researcher: phase plate] |
| `microscope` | text | `acquisition.toml` (`[acquisition]`) | Model name. [researcher: scope type] |
| `pixel_size` | float | `MDOC` | Angstrom. [researcher: pixel size] |
| `dose_per_tilt` | list[float] | `MDOC` | e/Å² per tilt. [researcher: dose] |
| `total_dose` | float | `MDOC` (summed) | e/Å². [researcher: dose (total)] |
| `tilt_min` | float | `MDOC` | Degrees. Minimum tilt angle recorded. [researcher: tilt range (min)] |
| `tilt_max` | float | `MDOC` | Degrees. [researcher: tilt range (max)] |
| `tilt_axis` | float | `MDOC` | Degrees. [researcher: tilt axis] |
| `defocus_per_image` | list[float] | `MDOC` | Micrometres, per tilt. |
| `date_collected` | date | `MDOC` | [researcher: date of collection] |
| `voltage` | float | `MDOC` | kV. [researcher: operating voltage] |
| `energy_filter_slit_width` | float | `MDOC` | eV. [researcher: energy filter slit width] |
| `camera` | text | `.eer` / `.tiff` | Derived from frame extension (`.eer` → Falcon; `.tiff` → K3). [researcher: Camera] |
| `frame_count` | integer | `MDOC` | Number of tilts. |

---

## 3. Tomogram entity

[researcher: Processing level → Raw tomogram / Processed tomograms]

One row per tomogram output. Primary key: `(sample_id, acquisition_id, tomogram_id)`.

| Field | Type | Source | Notes / researcher mapping |
|---|---|---|---|
| `tomogram_id` | text (PK) | `directory` ↔ `acquisition.toml` (`[[tomogram]].id`) | Processing folder name, e.g. `bp_3dctf_bin4`; the TOML `id` must match the folder. [researcher: Processing Steps] |
| `acquisition_id` | text (FK) | `directory` | Parent acquisition folder name. |
| `sample_id` | text (FK) | `directory` | Parent sample folder name. |
| `pipeline` | text | `acquisition.toml` (`[[tomogram]]`) | Human description. [researcher: Processing Steps] |
| `software` | text | `acquisition.toml` (`[[tomogram]]`) | [researcher: software] |
| `voxel_bin` | integer | `acquisition.toml` (`[[tomogram]]`) | |
| `voxel_spacing_angstrom` | float | `acquisition.toml` (`[[tomogram]]`) ↔ cross-checked with `MRC header` | [researcher: voxel size] |
| `derived_from` | list[text] | `acquisition.toml` (`[[tomogram]]`) | Lineage; empty for raw reconstructions. |
| `is_raw` | boolean | `derived` | `derived_from == []`. [researcher: Raw tomogram flag] |
| `image_size_x` | integer | `MRC header` | [researcher: image size] |
| `image_size_y` | integer | `MRC header` | |
| `image_size_z` | integer | `MRC header` | |
| `mrc_path` | text | `directory` | Derived from prescribed layout. |
| `zarr_path` | text | `directory` | Derived from prescribed layout. |
| `zarr_axes` | text | `OME-Zarr .zattrs` | Axis order. |
| `zarr_scale` | list[float] | `OME-Zarr .zattrs` | Multiscale scale factors. |

---

## 4. Annotation entity

[researcher: Processing level → Segmentation / Nucleosome orientation / STA results]

One row per annotation output. Primary key: `(sample_id, acquisition_id, annotation_id)`.

| Field | Type | Source | Notes / researcher mapping |
|---|---|---|---|
| `annotation_id` | text (PK) | `directory` ↔ `acquisition.toml` (`[[annotation]].id`) | Annotation folder name, e.g. `membrain_seg_v10`; the TOML `id` must match the folder. |
| `acquisition_id` | text (FK) | `directory` | Parent acquisition folder name. |
| `sample_id` | text (FK) | `directory` | Parent sample folder name. |
| `type` | text | `acquisition.toml` (`[[annotation]]`) | e.g. `membrane_segmentation`, `nucleosome_placement`, `nucleosome_orientation`, `sta_result`. [researcher: Segmentation / Nucleosome orientation / STA results] |
| `target_tomogram` | text (FK) | `acquisition.toml` (`[[annotation]]`) | Tomogram this was generated from. |
| `files` | list[text] | `directory` | `.star`, `.mrc`, `.ome.zarr`, `.png` artifacts discovered in the folder. |
