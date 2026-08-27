# HPC command reference — binder & antibody design pipelines

This file documents the exact HPC tool commands the skill workflow uses, the
mandatory flags, and the known failure modes / workarounds. These commands are
run via the Biomni HPC helpers (`hpc_run_tool`), not on the local machine.

- **§1–§3 — Track A (de novo mini-binder):** RFdiffusion → ProteinMPNN → Boltz-2.
  Validated on the PCSK9 worked example (numbers reproduce exactly).
- **§4 — Track B (antibody / nanobody):** the `rfantibody` tool
  (RFdiffusion_Ab → ProteinMPNN interface design → RF2). Commands transcribed from the
  tool's `usage` doc; run `hpc_search_tools("rfantibody")` to confirm the current container.

```python
from biomni.tool import hpc_search_tools, hpc_run_tool, hpc_get_job_results, hpc_get_logs, hpc_cancel_job
```

Always call `hpc_search_tools("<tool>")` first and read the returned `usage`
field — it is the source of truth for the current container. The commands below
are the validated invocations used for the PCSK9 worked example; adapt paths and
parameters, but keep the flags marked **MANDATORY**.

Submission pattern (all tools):
```python
job = hpc_run_tool(tool_id, command, input_files={"dest_name.ext": "/local/path.ext"})
job_id = job["job_id"]            # returns immediately; wait for the completion callback
res = hpc_get_job_results(job_id) # after callback -> {"files":[...], "output_dir": "..."}
```

---

## 1. RFdiffusion — binder backbone generation

Generates `--num-designs` binder backbones docked onto the target, using the
contig string emitted by `prepare_target.py` (which encodes the kept target
segments plus the binder length range).

```
/app/RFdiffusion/.venv/bin/python /app/RFdiffusion/scripts/run_inference.py \
  inference.input_pdb=/input/<target>.pdb \
  'contigmap.contigs=[<segments>/0 <lo>-<hi>]' \
  inference.ckpt_override_path=/app/RFdiffusion/models/Complex_base_ckpt.pt \
  inference.output_prefix=/output/<prefix> \
  inference.num_designs=<N> \
  denoiser.noise_scale_ca=0.5 \
  denoiser.noise_scale_frame=0.5 \
  inference.model_directory_path=/app/RFdiffusion/models
```

- `input_files = {"<target>.pdb": "<cropped target PDB from prepare_target.py>"}`
- The **contig** is the `contigmap.contigs=[...]` line printed by `prepare_target.py`.
  Example (PCSK9): `contigmap.contigs=[A155-168/A176-212/A219-449/A453-461/0 60-90]`.
  The `/0 ` separates the fixed target chain from the newly built binder; `60-90`
  is the sampled binder length range.
- **`Complex_base_ckpt.pt`** is the correct checkpoint for protein–protein binder
  design. (Do NOT use `ActiveSite_ckpt.pt` unless you are doing motif scaffolding,
  or `RFdiffusion_Ab.pt` which is for antibodies via RFantibody.)
- `noise_scale_ca=0.5` and `noise_scale_frame=0.5` reduce noise for cleaner,
  more designable interfaces (RFdiffusion binder-design recommendation).
- **Hotspots (optional):** to bias the binder toward specific target residues add
  `'ppi.hotspot_res=[A<res>,A<res>,...]'`. Omitting it lets RFdiffusion choose the
  interface (used for the PCSK9 example). If the user specifies an epitope, pass it.
- Output: one PDB per design, `<prefix>_0.pdb ... <prefix>_{N-1}.pdb`, each a
  two-chain complex (binder = chain A, target = chain B).

Preset guidance for `--num-designs`: quick ~8, standard ~15, thorough ~30–50.

---

## 2. ProteinMPNN — sequence design on the backbones

Designs sequences for the binder chain (A) with the target chain fixed as context.

**Container-override 8192-char limit workaround:** pass all backbones as a single
tar.gz and unpack inside the command (a long list of `--pdb_path` args overflows
the limit).

```
mkdir -p /output/pdbs && tar xzf /input/backbones.tar.gz -C /output/pdbs && \
cd /app/proteinmpnn && \
python helper_scripts/parse_multiple_chains.py --input_path=/output/pdbs --output_path=/output/parsed.jsonl && \
python helper_scripts/assign_fixed_chains.py --input_path=/output/parsed.jsonl --output_path=/output/chains.jsonl --chain_list 'A' && \
/app/proteinmpnn/.venv/bin/python protein_mpnn_run.py \
  --jsonl_path /output/parsed.jsonl \
  --chain_id_jsonl /output/chains.jsonl \
  --out_folder /output \
  --num_seq_per_target 4 \
  --sampling_temp '0.1 0.2' \
  --use_soluble_model \
  --seed 37 \
  --batch_size 1
```

- `input_files = {"backbones.tar.gz": "<tar.gz of all RFdiffusion PDBs>"}`
- **`--chain_list 'A'`** = the chain(s) to DESIGN (the binder). The target chain is
  held FIXED. Getting this backwards redesigns the target — a common, silent error.
- `--num_seq_per_target 4` with `--sampling_temp '0.1 0.2'` yields **8 sequences per
  backbone** (4 samples x 2 temperatures). 15 backbones -> 120 sequences.
- **`--use_soluble_model`** biases away from membrane-protein composition (better for
  soluble binders).
- Output: one FASTA per backbone in `/output/seqs/*.fa`. The FIRST record in each
  FASTA is the original backbone sequence (skip it) — `filter_sequences.py` handles this.

---

## 3. Boltz-2 — co-folding validation

Co-folds each selected binder sequence WITH the target and reports interface
confidence (ipTM, PAE). One YAML per candidate.

```
HF_HUB_OFFLINE=1 boltz predict /input/<cid>.yaml \
  --out_dir /output \
  --cache /mnt/fsx/dbs/boltz/cache \
  --num_workers 0 \
  --use_msa_server \
  --no_kernels
```

- `input_files = {"<cid>.yaml": "<candidate YAML>"}`
- **`--no_kernels` is MANDATORY** on Ada (sm_89) GPUs — without it Boltz segfaults.
- **`--num_workers 0` is MANDATORY** — higher worker counts OOM.
- **`--use_msa_server` is MANDATORY** — the target needs an MSA for accurate folding.
  (The de novo binder chain has no natural MSA and folds single-sequence; that is expected.)
- **GPU concurrency: max 3 jobs at once.** Submit in waves; a 429 response means the
  concurrent-GPU limit was hit (no automatic retry).

YAML structure (binder = chain A single sequence, target = chain B):
```yaml
version: 1
sequences:
  - protein:
      id: A
      sequence: <BINDER_SEQUENCE>
  - protein:
      id: B
      sequence: <TARGET_DOMAIN_SEQUENCE>
```

Output directory pattern (per candidate):
```
<output_dir>/boltz_results_<cid>/predictions/<cid>/
    <cid>_model_0.cif                # predicted complex; chain A binder, chain B target
    confidence_<cid>_model_0.json    # iptm, ptm, complex_plddt, ...
    pae_<cid>_model_0.npz            # PAE matrix (L x L)
    plddt_<cid>_model_0.npz          # per-residue pLDDT (L,)
    pde_<cid>_model_0.npz
```
`analyze_interface.py` consumes exactly this directory (`--candidates cid=<...>` or a
`--candidates-csv`). Note Boltz **renumbers** the target chain 1..N, so pass
`--domain-start <native first residue>` to recover native numbering.

---

## 4. RFantibody — Track B antibody / nanobody design

One HPC tool (`tool_id="rfantibody"`) bundles the whole antibody pipeline. It is a
Docker + Poetry environment: every command is `cd /home && poetry run python <script> ...`.
Model weights + example inputs live on the FSx mount `/mnt/fsx/dbs/rfantibody`.

**HLT file format (required for inputs).** RFantibody uses "HLT"-formatted PDBs:
- Heavy chain → chain id `H`; Light chain → chain id `L`; Target chain(s) → chain id `T`.
- Chain order in the file: **Heavy, Light, Target**.
- CDR loops annotated with PDB remarks, 1-indexed absolute residue positions, e.g.
  `REMARK PDBinfo-LABEL:   32 H1`.

**Frameworks baked into the image** (prefer these unless the user supplies one):
- Nanobody (VHH): `/home/scripts/examples/example_inputs/h-NbBCII10.pdb`
- scFv: `/home/scripts/examples/example_inputs/hu-4D5-8_Fv.pdb`
- Example RSV target: `/home/scripts/examples/example_inputs/rsv_site3.pdb`

Prepare the **target** by cropping to ~10 Å around the epitope (you can reuse
`scripts/prepare_target.py`), then relabel the target chain to `T`. Pass the framework and
target either as `/input/*.pdb` uploads or as the baked-in example paths.

### 4.1 RFdiffusion_Ab — dock + diversify CDR loops
```
cd /home && poetry run python scripts/rfdiffusion_inference.py \
  --config-name antibody \
  antibody.target_pdb=/input/target.pdb \
  antibody.framework_pdb=/input/framework.pdb \
  inference.ckpt_override_path=/mnt/fsx/dbs/rfantibody/RFdiffusion_Ab.pt \
  'ppi.hotspot_res=[T305,T456]' \
  'antibody.design_loops=[L1:8-13,L2:7,L3:9-11,H1:7,H2:6,H3:5-13]' \
  inference.num_designs=20 \
  inference.output_prefix=/output/ab_des
```
- **MANDATORY** `inference.ckpt_override_path=.../RFdiffusion_Ab.pt` — the antibody-finetuned
  checkpoint (different from Track A's `Complex_base_ckpt.pt`).
- `ppi.hotspot_res` — epitope residues in **target chain `T`** numbering, format `[T305,T456]`.
  Effectively required: without hotspots the CDRs have no surface to dock to.
- `antibody.design_loops` — CDR loop-length ranges to diversify (omit a loop to keep it fixed).
- Nanobody vs scFv is set by the framework you pass (nanobody framework has no `L` chain).
- Runtime ≈ 30–60 s per design. Pilot 20–100 designs to tune hotspots/loop lengths.

### 4.2 ProteinMPNN — CDR interface sequence design
```
cd /home && poetry run python scripts/proteinmpnn_interface_design.py \
  -pdbdir /output/rfdiffusion \
  -outpdbdir /output/mpnn
```
- Designs the CDR-loop sequences at the interface with the framework + target held fixed.
- Runtime ≈ 5–10 s per design.

### 4.3 RF2 — structure prediction + filtering
```
cd /home && poetry run python scripts/rf2_predict.py \
  input.pdb_dir=/output/mpnn \
  output.pdb_dir=/output/rf2 \
  model.num_recycles=10 \
  +model.hotspot_fraction=0.1
```
- **MANDATORY leading `+` on `+model.hotspot_fraction`** — this key is *not* in the struct
  config, so Hydra requires the append syntax. Omitting the `+` fails with:
  `Could not override 'model.hotspot_fraction'. To append ... use +model.hotspot_fraction=0.1`.
- `model.num_recycles=10` is a plain override (the key exists in the base config).
- Runtime ≈ 2–5 min per design (with 10 recycles).

**Recommended filters:** RF2 pAE < 10; RMSD(design vs RF2-predicted) < 2 Å;
optional Rosetta ddG < −20.

**Quiver files (optional, large campaigns).** For 1k–10k designs use the multi-structure
Quiver format: `qvfrompdbs *.pdb > designs.qv`, then `qvls` / `qvextract` / `qvscorefile`.

### Example submission
```python
job = hpc_run_tool(
    "rfantibody",
    ("cd /home && poetry run python scripts/rfdiffusion_inference.py --config-name antibody "
     "antibody.target_pdb=/input/target.pdb antibody.framework_pdb=/input/framework.pdb "
     "inference.ckpt_override_path=/mnt/fsx/dbs/rfantibody/RFdiffusion_Ab.pt "
     "'ppi.hotspot_res=[T305,T456]' "
     "'antibody.design_loops=[L1:8-13,L2:7,L3:9-11,H1:7,H2:6,H3:5-13]' "
     "inference.num_designs=20 inference.output_prefix=/output/ab_des"),
    input_files={"target.pdb": "/local/target_HLT.pdb",
                 "framework.pdb": "/local/framework_HLT.pdb"},
)
```

---

## Confidence interpretation (used by analyze_interface.py and the report)

| metric | HIGH / confident | MODERATE / uncertain | LOW / unreliable |
|---|---|---|---|
| ipTM (Track A, Boltz-2) | > 0.8 | 0.6 – 0.8 | < 0.6 |
| interface PAE (Å) | < 5 | 5 – 10 | > 10 |

Track A (Boltz-2) ranks on ipTM + interface PAE. **Track B (RF2)** ranks instead on
RF2 pAE (< 10 = pass) and self-consistency RMSD (design vs RF2-predicted < 2 Å), optionally
Rosetta ddG < −20. Both are the **confidence of predicted geometry, not measured affinity**.
Treat high-confidence designs as prioritized hypotheses for experimental testing
(expression + SPR/BLI), never as validated binders.
