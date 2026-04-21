# CryoET + AI Data Organization

> **Status: draft / proposed.** This repository contains a working draft of the file-system layout and metadata scheme for the CryoET + AI project data portal. Fields, controlled vocabularies, and directory conventions are expected to evolve as researchers start authoring metadata against it.

The central design goal is answering one question across both the experimental and simulation arms of the project: **which conditions have we covered, and which still need cryoET imaging, simulation, or both?**

## What's in this repo

| File | Purpose |
|---|---|
| `schema_info.md` | Human-readable reference for every field that will land in the portal database, grouped by entity (Sample → Acquisition → Tomogram → Annotation) with the authoritative source of each (TOML vs MDOC vs MRC vs directory vs derived). |
| `schema.py` | Authoritative Pydantic schema — defines every metadata field, its type, and any constraints. |
| `schema.json` | Language-neutral JSON Schema generated from `schema.py` for non-Python consumers (portal UI, etc.). |
| `sample_template.toml` | Starter template for `sample.toml` — copy into each sample directory and fill in. |
| `acquisition_template.toml` | Starter template for `acquisition.toml` — copy into each acquisition directory and fill in. |
| `validate_sample.py` | Validator: `pixi run validate {sample_dir}`. |
| `generate_json_schema.py` | Regenerates `schema.json` from the Pydantic models: `pixi run json-schema`. |
| `pixi.toml` / `pixi.lock` | Pinned Python + Pydantic + tomli environment. |

---

## Proposed directory structure

### CryoET (experimental) data

```
{sample_name}/                               # sample identity = directory name
  sample.toml                                # sample-level conditions
  {acquisition_name}/                        # acquisition identity = directory name
    acquisition.toml                         # per-acquisition params + processing log
    Frames/                                  # raw movie frames (.eer / .tiff) + .mdoc
    Gains/                                   # gain reference
    TiltSeries/                              # .mrc + .zarr + .rawtlt
    Alignments/                              # per-alignment .json (machine-emitted)
    Reconstructions/
      Tomograms/
        {processing_id}/                     # one subfolder per processing pipeline
          *.mrc
          *.zarr
      Annotations/
        {annotation_id}/
          *.star
          *.mrc / *.zarr
```

### MD simulation data

```
{sample_name}/
  sample.toml                                # sample-level conditions + simulation params
  {acquisition_name}/
    acquisition.toml                         # per-acquisition params + processing log
    Trajectories/                            # raw simulation output
    Snapshots/                               # extracted conformations
    SyntheticCryoET/                         # simulated tomograms generated from snapshots
      {processing_id}/
        *.mrc
        *.zarr
```

The directory skeleton is adapted from the [CZI CryoET Data Portal](https://chanzuckerberg.github.io/cryoet-data-portal/stable/cryoet_data_portal_docsite_data.html) at the Sample > Acquisition > (Frames, Gains, TiltSeries, Alignments, Reconstructions) level, with three deliberate departures:

- **Two metadata files per sample.** Sample-level conditions live in `sample.toml` at the sample root. Per-acquisition parameters and the processing log live in `{acquisition}/acquisition.toml`. Fields derivable from MDOC files and file headers are authored in neither file; the ingest pipeline will read them directly.
- **Tomograms are kept in per-pipeline subfolders** (e.g., `bp_3dctf_bin4/`, `bp_3dctf_bin4_ddw/`) rather than flattened into `Tomograms/`. This avoids filename collisions when new processing versions are added, and the folder name acts as the `processing_id`.
- **No `VoxelSpacing{N}/` subfolder.** Voxel spacing is recorded directly in `acquisition.toml` (as `voxel_bin` and `voxel_spacing_angstrom` on each `[[tomogram]]` entry) and cross-checked against the MRC header. Keeping it out of the path avoids duplicating information that lives in the file itself.

Simulation data uses a parallel structure with domain-appropriate folder names. Both share the same schema, which is what makes cross-comparison possible.

---

## Metadata files

### `sample.toml` — sample-level conditions

One file per sample, placed at the root of the sample directory. Contains only what was imaged or simulated — not how. The sample directory name *is* the sample's identity, so `sample.id` is omitted from the file.

### `acquisition.toml` — per-acquisition parameters + processing log

One file per acquisition, placed at the root of each acquisition directory. It contains:

1. Researcher-authored imaging parameters not available from MDOC files (nominal resolution, nominal tilt spacing, target defocus range, energy filter model, phase plate, microscope model).
2. A **processing log**: `[[tomogram]]` and `[[annotation]]` entries appended over time as processing produces new outputs.

The acquisition directory name *is* the acquisition's identity, so `acquisition.id` is omitted from the file.

### What the ingest pipeline will derive automatically

Researchers do **not** enter these fields in `acquisition.toml`:

| Source | Fields derived |
|---|---|
| `.mdoc` files | pixel size, tilt angles (min/max), tilt dose per tilt, total dose, defocus per image, date/time, voltage, energy filter slit width |
| Frame file extension (`.eer` vs `.tiff`) | camera type (Falcon vs K3) |
| MRC headers | voxel size, grid dimensions |
| OME-Zarr `.zattrs` | axis order, scale |
| Sample directory name | sample identity |
| Acquisition directory name | acquisition identity |
| Tomogram / annotation folder names | path to each output |

---

## Schema rules

### Required fields

Only two fields are required for all entries: `sample.data_source` and `sample.project`. All other fields are optional, allowing the schema to grow as researcher needs settle. These two fields are also the only enums — all other fields are open text, with the potential to be tightened into enums later based on how researchers use them.

### Extra fields

You may add any key-value pair to any section of `sample.toml` or `acquisition.toml` that is not yet in the schema. For example:

```toml
[chromatin]
substrate        = "synthetic"
linker_length_bp = 187.0
# Fields not yet in schema.py — captured here for later formalization:
ionic_strength_mM = 154.0
assembly_method   = "salt_dialysis"
```

Each Pydantic model is configured with `extra="allow"`, so unknown keys are preserved on the parsed record. The validator walks the tree after validation and reports every extra key as a **warning**, not an error — the file still passes and the extra fields survive will into the ingest record. If a field proves useful, notify the SciComp team so it can be formally added to `schema.py` with the appropriate type and description.

### Lineage: `derived_from` and `target_tomogram`

`derived_from` records lineage across tomogram entries, and `target_tomogram` links annotations to the tomogram they were generated from. Both reference ids within the same `acquisition.toml`:

```toml
# In .../Position_86/acquisition.toml

# Raw reconstruction
[[tomogram]]
id                     = "bp_3dctf_bin4"
voxel_bin              = 4
voxel_spacing_angstrom = 10.0
derived_from           = []

# Denoised version derived from the raw
[[tomogram]]
id                     = "bp_3dctf_bin4_ddw"
voxel_bin              = 4
voxel_spacing_angstrom = 10.0
derived_from           = ["bp_3dctf_bin4"]

# Segmentation run on the denoised tomogram
[[annotation]]
id              = "membrain_seg_v10"
type            = "membrane_segmentation"
target_tomogram = "bp_3dctf_bin4_ddw"
```

---

## Researcher workflow: creating metadata

### 1. Lay out the sample directory

Create a directory named after the sample. The directory name *is* the sample's identity and will be used by the ingest pipeline.

```
gouauxlab_20250418_AMmilled29-2/
```

Inside, create one subdirectory per acquisition. Each acquisition directory name is also its identity.

```
gouauxlab_20250418_AMmilled29-2/
  Position_86/
  Position_87/
```

### 2. Fill out `sample.toml`

Copy `sample_template.toml` to the sample root as `sample.toml` and fill it in:

- Every field marked `← FILL IN` must be completed.
- Delete the `[synapse]` block if your project is `chromatin`, or vice versa.
- Uncomment optional blocks (`[[aunp]]`, `[freezing]`, `[milling]`) only if they apply.

Sample-level conditions only — do not put imaging parameters here.

### 3. Fill out `acquisition.toml` in each acquisition directory

Copy `acquisition_template.toml` into each acquisition directory as `acquisition.toml` and fill in the researcher-authored imaging parameters (nominal resolution, microscope, defocus range, …). The template pre-populates fields that are constant across a lab's acquisitions, so you should only need to change what differs between acquisitions.

Leave the processing-log section (the `[[tomogram]]` and `[[annotation]]` blocks) empty at this stage — you'll append to it as processing happens.

### 4. Append to the processing log as outputs are produced

Each `acquisition.toml` grows over time. For each new output — a new tomogram reconstruction, a denoised version, a segmentation, an STA result — append a new `[[tomogram]]` or `[[annotation]]` entry to the relevant acquisition's file.

**Rules:**
- Entries are immutable once added. Reprocessing produces a **new** entry with a new `id`, not a modification of an existing one.
- The `id` must match the folder name under `Reconstructions/Tomograms/` or `Reconstructions/Annotations/`.
- Use `derived_from` and `target_tomogram` to record lineage (see above).

Because each acquisition has its own file, appends are strictly tail-append and parallel work on different acquisitions never causes merge conflicts.

### 5. (Optional) Validate before committing

```
pixi run validate {sample_dir}
```

This validates `sample.toml` and every `acquisition.toml` under the sample directory. Validation will also run during database ingestion — see `schema_info.md` for the full list of fields that will be stored, including those auto-derived from MDOCs, MRC headers, OME-Zarr metadata, and directory structure. Running the validator locally is a convenience, not a requirement.

---

## Example: mapping Gouaux lab data to this structure

```
gouauxlab_20250418_AMmilled29-2/             # sample identity = directory name
  sample.toml                                # sample-level conditions
  Position_86/                               # acquisition identity = directory name
    acquisition.toml                         # per-acquisition params + processing log
    Frames/
      *.eer
      *.eer.mdoc                             # acquisition metadata lives here
    Gains/
      gain_reference.gain
    TiltSeries/                              # TO CREATE: from .eer conversion
      *.mrc
      *.zarr
      *.rawtlt
    Reconstructions/
      Tomograms/
        bp_3dctf_bin4/                       # renamed from "raw/"
          *_BP_3DCTF_BIN4.mrc
          *_BP_3DCTF_BIN4.zarr
        bp_3dctf_bin4_ddw/                   # renamed from "ddw/"
          *_BP_3DCTF_BIN4_ddw.mrc
          *_BP_3DCTF_BIN4_ddw.zarr
      Annotations/
        activezone_1/                        # renamed to match star-file id
          activezone_1.star
          active_zonogram_0.mrc
          active_zonogram_0.zarr
          active_zonogram_0_annotated.png
        membrain_seg_v10/
          *_MemBrain_seg_v10_*_smooth.mrc
          *_MemBrain_seg_v10_*_smooth.zarr
  Position_87/
    acquisition.toml
    Frames/
    ...
```

Changes from the current `annotation_HHMI_reorg` layout:

1. Rename `raw/` → `bp_3dctf_bin4/` and `ddw/` → `bp_3dctf_bin4_ddw/`.
2. Rename `activezone/` → `activezone_{N}/` to match the star-file id (schema rule: annotation `id` = folder name).
3. Add `sample.toml` at the sample level.
4. Add `acquisition.toml` in each acquisition directory.
5. Create `TiltSeries/` (pending `.eer` conversion).
