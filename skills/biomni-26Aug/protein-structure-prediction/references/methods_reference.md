# Methods reference — verified commands, inputs, outputs, pLDDT extraction

All commands, flags, and output layouts below were confirmed on **real HPC jobs**
in the Biomni environment (human B2M mature chain, 99 aa). Do not "simplify" the
mandatory flags — several are required to avoid segfaults / crashes on this
environment's GPUs.

Common pattern for every method:
```python
from biomni.tool import hpc_search_tools, hpc_run_tool, hpc_get_job_results, hpc_get_logs
# 1. (optional) hpc_search_tools("<method>") -> read the `usage` field
# 2. hpc_run_tool(tool_id, command, input_files={dest_name: local_path})
# 3. on completion callback: hpc_get_job_results(job_id)
# 4. python scripts/extract_plddt.py --method <m> --job-dir <output_dir> --out-prefix <prefix>
```
`command` uses container paths: inputs land in `/input/`, write to `/output/`.
`input_files` maps a destination filename (that appears as `/input/<name>`) to a
local path. Respect the **3 concurrent GPU jobs** limit (HTTP 429 = defer).

---

## 1. AlphaFold v2 — `tool_id="alphafold-v2"`

**Accuracy leader for monomers. MSA + templates. Emits pTM (with ptm preset).**

Input file: plain FASTA `>name\nSEQUENCE`.

**Monomer command** (verified):
```
python3 /app/alphafold/run_alphafold.py \
  --fasta_paths=/input/<name>.fasta \
  --output_dir=/output \
  --data_dir=/mnt/fsx/dbs/alphafold2 \
  --uniref90_database_path=/mnt/fsx/dbs/alphafold2/uniref90/uniref90.fasta \
  --mgnify_database_path=/mnt/fsx/dbs/alphafold2/mgnify/mgy_clusters_2022_05.fa \
  --small_bfd_database_path=/mnt/fsx/dbs/alphafold2/small_bfd/bfd-first_non_consensus_sequences.fasta \
  --pdb70_database_path=/mnt/fsx/dbs/alphafold2/pdb70/pdb70 \
  --template_mmcif_dir=/mnt/fsx/dbs/alphafold2/pdb_mmcif/mmcif_files \
  --obsolete_pdbs_path=/mnt/fsx/dbs/alphafold2/pdb_mmcif/obsolete.dat \
  --max_template_date=2024-01-01 \
  --model_preset=monomer_ptm \
  --db_preset=reduced_dbs \
  --use_gpu_relax=false
```
- **`--use_gpu_relax=false` is MANDATORY** (no GPU relax available).
- Use **`--model_preset=monomer_ptm`** so pTM is produced (plain `monomer` omits pTM).
- `--db_preset=reduced_dbs` uses small_bfd (faster); `full_dbs` is slower.
- `--max_template_date` prevents template leakage; set to a recent date.

**Multimer (complex)** — MUST change these:
- `--model_preset=multimer`
- **remove** `--pdb70_database_path` (incompatible with multimer)
- **add** `--pdb_seqres_database_path=/mnt/fsx/dbs/alphafold2/pdb_seqres/pdb_seqres.txt`
- **add** `--uniprot_database_path=/mnt/fsx/dbs/alphafold2/uniprot/uniprot.fasta`
- FASTA lists one record per chain copy (homomer = repeat the sequence; heteromer
  = list each chain, repeated per copy).

Runtime (A100, 3 recycles): ~100 aa 5 s, 500 aa 29 s, 1000 aa 96 s (plus MSA time).

**Outputs** (in `/output/<target_name>/`):
- `ranked_0.pdb` … `ranked_4.pdb` — models ranked by pLDDT (0 = best).
- `ranking_debug.json` — keys `order` (list of model names best→worst) and `plddts`.
- `confidence_model_<name>.json` — per-residue `confidenceScore` (**0–100**),
  plus `residueNumber`, `confidenceCategory`.
- `result_model_<name>.pkl` — full outputs incl. `ptm`.
- `relaxed_model_*.pdb`, `unrelaxed_model_*.pdb`, `features.pkl`, `timings.json`, `msas/`.

**pLDDT extraction**: top model = `ranking_debug.json["order"][0]`; read
`confidence_<top>.json["confidenceScore"]` (already 0–100). pTM from
`result_<top>.pkl["ptm"]`. (Cross-check: CA B-factor of `ranked_0.pdb`.)
Verified B2M: mean pLDDT **97.16**, pTM **0.879**, top = `model_1_ptm_pred_0`.

---

## 2. Boltz-2 — `tool_id="boltz-2"`

**Balanced, MSA-based; handles complexes + ligands. Emits pTM.**

Input file: YAML.
```yaml
version: 1
sequences:
  - protein:
      id: A
      sequence: MKFLKFSLLTAVLLSVVFAFSSCGDDDDTGYLPPSQAIQDLLKRMKV
```
Complex = add more `- protein:` blocks with `id: B`, `id: C`, …; ligands are
supported (see tool usage for ligand syntax).

**Command** (verified):
```
HF_HUB_OFFLINE=1 boltz predict /input/<name>.yaml \
  --out_dir /output \
  --cache /mnt/fsx/dbs/boltz/cache \
  --num_workers 0 \
  --use_msa_server \
  --no_kernels
```
- **`--no_kernels` is MANDATORY on this environment.** Without it Boltz-2
  segfaults (exit 139) at the first GPU inference step on L4/L40S (Ada sm_89)
  GPUs. Tradeoff: PyTorch-native ops are slower / use more VRAM but give
  equivalent results.
- **`--num_workers 0`** and **`--use_msa_server`** are also required as used.
- `HF_HUB_OFFLINE=1` prevents model re-download (models pre-cached on FSx).

**Outputs** (under `/output/.../predictions/<name>/`):
- `<name>_model_0.cif` — top predicted structure.
- `plddt_<name>_model_0.npz` — per-residue pLDDT array, key `plddt` (**0–1**, ×100).
- `confidence_<name>_model_0.json` — keys `ptm`, `complex_plddt`, `confidence_score`, `iptm`.
- `pae_<name>_model_0.npz` — PAE matrix.

**pLDDT extraction**: load `plddt_<name>_model_0.npz["plddt"]`, ×100. pTM from
`confidence_<name>_model_0.json["ptm"]`.
Verified B2M: mean pLDDT **93.73**, pTM **0.938**.

---

## 3. Chai-1 — `tool_id="chai-1"`

**Multimodal (proteins, ligands, DNA/RNA, PTMs, complexes). 5 samples by default. Emits pTM.**

Input file: FASTA with typed headers.
```
>protein|name=example
MKTAYIAKQRQISFVKSHFSRQLEER...
```
- Ligand: `>ligand|name=aspirin` then a SMILES line.
- DNA/RNA: `>dna|name=x` / `>rna|name=x`.
- Complex: multiple `>protein|name=chain_A`, `>protein|name=chain_B`, … blocks.

**Command** (verified, with MSA for accuracy):
```
chai-lab fold --use-msa-server /input/<name>.fasta /output
```
- Add `--use-templates-server` for templates.
- More samples: **`--num-diffn-samples N`** (default 5).
  **NEVER use `--num-samples` — that flag does not exist.**
- `--num-trunk-recycles` (default 3); `--output-cif` for CIF output.

Runtime (GPU): ~100 aa ~1–2 min/sample; 5 samples ≈ 5–10 min for a small protein.

**Outputs**:
- `pred.model_idx_0.cif` … `pred.model_idx_4.cif` — 5 ranked structures.
- `scores.model_idx_<i>.npz` — per-sample **scalar** scores: `aggregate_score`,
  `ptm`, `iptm`, `per_chain_ptm`. **No per-residue array here.**
- `aligned_confidence.pdb` — aggregated confidence visualization.

**pLDDT extraction**: Chai stores **per-residue pLDDT in the CA B-factor** of each
CIF, not in the npz. Pick the best sample by highest `aggregate_score`, read that
CIF's CA B-factors (**0–100**). pTM = that sample's `scores...npz["ptm"]`.
Verified B2M: mean pLDDT **95.95**, pTM **0.953**, best = `model_idx_1`
(all 5 samples within ~0.05 pLDDT of each other).

---

## 4. ESMCFold2 — `tool_id="esmcfold2"` (or `esmcfold2-fast`)

**Fast, single-sequence (NO MSA), monomer-only. No pTM. Memory-heavy.**

Input file: plain FASTA `>name\nSEQUENCE`.

**Command** (verified):
```
esmc fold --model full --input /input/<name>.fasta --output /output --max-length 200
```
- **Default `--max-length 200`** — sequences longer than this are rejected. Raise
  it for longer proteins (higher memory, OOM risk) or pick another method.
- `--model fast` (or `tool_id="esmcfold2-fast"`) for lower memory / speed.
- OOM levers: `--model fast`, split multi-sequence FASTA into separate jobs,
  lower `--chunk-size` (default 64), reduce `--num-diffusion-samples` /
  `--num-recycles` / `--num-sampling-steps` only if the user accepts a quality
  tradeoff.
- Runs on 1 GPU (~45 GiB VRAM). **Monomer only — skip ESMCFold2 for complexes.**

**Outputs**:
- `<name>.pdb` — predicted structure; per-residue pLDDT in **CA B-factor** (**0–1**, ×100).
- `<name>.json` — has `mean_plddt` (0–1) for cross-check.
- `summary.json` (list), `run_metadata.json`.

**pLDDT extraction**: read CA B-factors from `<name>.pdb`, ×100. Cross-check mean
against `<name>.json["mean_plddt"]`. **No pTM available.**
Verified B2M: mean pLDDT **86.47** (matched reported 0.8646).

---

## Unattended orchestration: size default + poll-timeout + fallback

Implemented in `scripts/fold_orchestrate.py`. This is the default way to run the
skill; it never blocks on an HPC completion callback.

**Size-based default predictor (monomer).** Chosen by sequence length, not by
asking:
- `len ≤ esmfold_max_len` (default **400 aa**) → **ESMCFold2** primary (MSA-free,
  finishes in seconds; passes `--max-length max(cutoff, len)` so 201–400 aa is
  accepted).
- `len > esmfold_max_len` → **AlphaFold v2** primary (MSA + templates).
- Explicit user method(s) override the primary but the size-based fallback chain
  is still appended.
- **Scope: single-chain monomers only.** `orchestrate_fold()` **rejects**
  `is_complex=True` (and `':'`-joined multi-chain input) with a `NotImplementedError`
  — the per-method builders emit single-chain inputs, so folding a complex through
  the automated loop would silently produce a monomer. Fold complexes via the
  **manual multimer path** below.

**Fallback chain (each method tried at most once, never falls back to itself):**
- monomer, `len ≤ cutoff`: `[ESMCFold2, Boltz-2]` — guaranteed-finish first.
- monomer, `len > cutoff`: `[Boltz-2, ESMCFold2(--max-length len)]` — Boltz handles
  long sequences and emits pTM; ESMCFold2 is the last-resort finish (accept OOM
  risk / quality tradeoff).
If nothing succeeds within timeouts → proceed with whatever completed, else emit a
structured failure (status `no_success`). Never hang.

**Complexes (manual, not via the orchestrator).** Build the multimer job by hand:
AlphaFold-multimer (`--model_preset=multimer`, one FASTA record per chain copy,
drop `--pdb70_database_path`, add `--pdb_seqres_database_path` +
`--uniprot_database_path`; see §1), or Boltz/Chai with one block per chain (see §2/§3).
Submit with `hpc_run_tool(...)`, poll `hpc_get_job_results()` with a bounded
timeout the same way, then extract per-residue pLDDT with `extract_plddt.py`.

**Bounded poll loop (the callback replacement).** After `hpc_run_tool(...)`,
call `hpc_get_job_results(job_id)` every `poll_interval_s` (default **30 s**) up
to `poll_timeout_s` (default **900 s ≈ 15 min per job**). Outcomes:
- terminal-good status (`completed`/`succeeded`/…) **and ≥ 1 output file** →
  `completed` (success);
- terminal-good but **0 files** → `empty` (treat as failure → fall back);
- terminal-bad status (`failed`/`cancelled`/…) → `failed` (fall back);
- elapsed ≥ timeout → `timeout` (stalled; the classic AlphaFold MSA/jackhmmer
  case) → cancel the job and fall back.
On `timeout`/`empty` the orchestrator calls `hpc_cancel_job(job_id)` to free the
GPU slot before trying the next predictor. HTTP 429 at submit = defer (GPU-limit),
handled as a normal exception → next method.

**Run manifest.** The orchestrator always writes `<name>_run_manifest.json` to the
output dir with: `chosen_predictor`, `predictor_order`, `status`
(`success_primary` / `success_fallback` / `no_success` / …), `attempts[]` (method,
`job_id`, `status`, `outcome`, `seconds`, `n_files`), `fallback_trail[]`, and the
winning `mean_plddt` / `ptm` / `structure` / `csv` / `plot`. Progress is also
checkpointed to `/mnt/shared-workspace/shared/<name>_fold_jobs.json` so a run
survives context loss. **Always report the chosen predictor and any fallback.**

## pLDDT normalization rule (implemented in extract_plddt.py)

Detect scale per array: if `np.nanmax(arr) <= 1.5`, multiply by 100; else it is
already 0–100.
- Native **0–100**: AlphaFold (`confidenceScore`), Chai (CIF B-factor).
- Native **0–1**: Boltz-2 (`plddt` npz), ESMCFold2 (PDB B-factor).

After normalization, assert `max > 1.5` for every method and that all methods
returned the **same residue count** before any cross-method comparison.

## Confidence bands (AlphaFold-calibrated)

Compute every band count with the single packaged function
`confidence_breakdown.band_breakdown(plddt)` — **never** an ad-hoc `cut()` /
histogram (a right-closed `cut()` silently makes "≥ 90" mean "> 90" and "< 50"
include 50, so the printed labels stop matching the binning). The convention is
**lower-bound inclusive, upper-bound exclusive**, so the label always matches the
computation and the four bands partition `[0, 100]` with no overlap and no gap:

| Band | pLDDT interval | Boundary rule | Interpretation |
|---|---|---|---|
| Very high | `[90, ∞)` — `pLDDT ≥ 90` | 90 → very high | Backbone + side-chain reliable |
| Confident | `[70, 90)` — `70 ≤ pLDDT < 90` | 70 → confident | Backbone reliable |
| Low | `[50, 70)` — `50 ≤ pLDDT < 70` | 50 → low | Caution; likely flexible/uncertain |
| Very low | `(-∞, 50)` — `pLDDT < 50` | 50 excluded | Likely disordered / unreliable |

`band_breakdown()` returns per-band `count`, `percent`, and `mean_plddt`, states
its `convention`, and asserts the counts sum to N (a binning gap/overlap fails
loud). `extract_plddt.py` writes these to `<prefix>_bands.csv`. State that these
cutoffs are AlphaFold-calibrated and only approximately transfer to Boltz-2 /
Chai-1 / ESMCFold2 (single-sequence ESMCFold2 pLDDT is calibrated differently and
has no pTM).

## Domain-resolved confidence breakdown

Summarize per-residue pLDDT over UniProt-annotated features with
`confidence_breakdown.domain_breakdown(plddt, features)`, where `features` comes
from `confidence_breakdown.fetch_uniprot_features(accession)`. **Ranges are taken
verbatim from the fetched feature table — never a partition composed by eye.**
This exists because an audited PCSK9 (Q8NBP7) run hand-drew contiguous "domain"
bins (prodomain 31–152, catalytic 153–461, C-terminal 462–692) that contradicted
the UniProt features the same run had fetched (Inhibitor I9 `77–149`, Peptidase S8
`155–461`, C-terminal region `450–692`): residues 450–461 were assigned to one
domain when UniProt annotates two, and the "Inhibitor I9" label was stretched
across the whole prodomain.

`fetch_uniprot_features` pulls **two tiers** of features from
`https://rest.uniprot.org/uniprotkb/<acc>.json` (configurable type set), returning
`{name, type, start, end, category}` (1-based, inclusive):
- **domain tier** (`category="domain"`): `Domain`/`Region`/`Repeat`/`Motif`/… —
  the structural/functional segments that drive the coverage/overlap map;
- **sequence-level tier** (`category="sequence"`): `Signal`/`Propeptide`/
  `Transit peptide`/`Chain`/`Peptide` — real UniProt annotations a domain-only
  fetch would drop. Without them, PCSK9 residues **1–76** (signal 1–30 + propeptide
  31–76) and **150–154** came back as "unannotated" although UniProt fully
  annotates them; they are now fetched and reported in their own category instead
  of mislabelled as a gap. (Exact UniProt JSON type strings, verified on Q8NBP7:
  `Signal`, `Propeptide`, `Chain` — not "Signal peptide".)

`domain_breakdown` returns:
- **per-DOMAIN-feature rows** (`features`) — `name, type, start, end` (verbatim),
  `n_res`, `mean_plddt`, `bands` (via the same `band_breakdown`);
- **per-SEQUENCE-feature rows** (`sequence_features`) — same shape, for the
  signal/propeptide/transit/chain tier;
- **a coverage segment map** (`segments`) — contiguous segments partitioned by the
  exact set of covering **domain** features. A segment covered by **≥ 2** domain
  features has `status="overlap"` and lists **every** covering feature (residues
  belong to both, never silently one); a segment covered by **no** domain feature
  has `status="no_domain_feature"` and lists any covering sequence-level feature
  (an invented boundary is never substituted). Sequence-level features are kept out
  of the coverage map on purpose — the chain-spanning `Chain` feature would
  otherwise bury the real domain overlaps;
- **`overlap`** (residues in ≥ 2 domain features), **`no_domain_feature`**
  (residues in no domain feature — which may still be a signal peptide / chain),
  and **`uncovered`** (residues in NO feature of EITHER tier — the only true
  blanks) counts and ranges;
- **`requested_feature_types`** — the domain and sequence type sets used, so the
  scope of what was tested is explicit.

If no accession/feature table is available (or UniProt returns none), the
breakdown is **omitted with a stated reason** (`available: false`) — it is never
approximated. Note: feature coordinates are in UniProt (full-canonical) numbering;
if you submitted a trimmed sequence, re-map or the domain ranges will be offset
from the 1..N output numbering.

## Run provenance and fallback disclosure

The delivered report must state the predictor actually used, the poll bound
actually used, and any fallback **by reading the run manifest**, not from memory.
Use `run_provenance.render_run_provenance(manifest)` to build the disclosure and
`run_provenance.check_report(manifest, report_text)` as a gate that fails loudly
when a fallback occurred but the report omits it (CLI:
`python run_provenance.py --manifest <json> --report <file>`; `--render` prints the
text). The reported poll bound is always the manifest's `poll_timeout_s` — the
value actually used — because a run may override the 900 s default (an audited run
used 2700 s). When `fallback_trail` is non-empty the disclosure states, in the
results narrative, which method was requested, that it was cancelled at the poll
bound while still running with 0 output files, which method produced every reported
number, and that the requested method may have finished with a longer bound.

## Figure / infographic value integrity

Every number shown on the summary infographic or a figure caption must be READ
from the exported table at generation time, never typed by hand. In an audited run
the infographic said "Confident 70-90: 30%" while the band table said **29.48%** —
a hand-typed, silently-rounded value that contradicted the very table the report
was built from. Use
`figure_value_guard.derive_infographic_values(<bands_csv|breakdown_json>)` to get
the canonical `label: value` strings (band percents at the exported 2-decimal
precision) and paste them into the GenerateImage prompt; then gate the prompt with
`figure_value_guard.check_infographic(prompt_text, <table>)` (CLI:
`python figure_value_guard.py --breakdown <table> --infographic-text <file>`),
which fails loudly if any stated value disagrees with the exported table. The
single source of truth is the exported band table — `<name>_<method>_bands.csv`
or the `band_breakdown` block in `<name>_confidence_breakdown.json`.

## Sequence retrieval (UniProt)

- Accession → sequence: `https://rest.uniprot.org/uniprotkb/<acc>.fasta`.
- Gene/protein name → accession: UniProt search API, e.g.
  `https://rest.uniprot.org/uniprotkb/search?query=gene:<G>+AND+organism_id:9606+AND+reviewed:true&format=fasta&size=1`
  (prefer reviewed/Swiss-Prot). Confirm the hit with the user in interactive use;
  for unattended runs proceed with the top reviewed hit and record it in the
  manifest.
- **Default to the full canonical sequence.** Only trim to a mature/domain chain
  if explicitly requested, and then report exactly which residues were removed
  (signal peptide / propeptide) and that output numbering is 1..N of the
  submitted (trimmed) sequence.
