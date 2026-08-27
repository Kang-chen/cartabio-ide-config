---
id: "skill_af98a09aa8536dce4a7157f8f270a880"
name: "binder-antibody-design"
description: "Use to design new protein or peptide binders, mini-binders, antibodies, nanobodies, scFv, or VHH against a target from a PDB structure or UniProt ID. Covers RFdiffusion/RFantibody backbone generation, ProteinMPNN sequence design, co-fold validation, interface scoring, on-target epitope recovery, and candidate ranking."
category: "molecular_design"
visibility: "public"
starting-prompt: "Design de novo minibinders against the PD-L1 IgV domain (UniProt Q9NZQ7, PDB 5O45), targeting the PD-1 binding face. Restrict the hotspot set to a single tight cluster on that interface, use 55-65 residue binders, and run a small focused campaign. Report the campaign scale against the recommended range and produce the final PDF report."
---

# Binder & Antibody Design (RFdiffusion / RFantibody → ProteinMPNN → co-fold validation)

Design binders against a protein target and deliver ranked candidates with sequences,
an epitope map, figures, and a PDF report. Two tracks share the same target-prep,
interface-analysis, figure, and report tooling:

- **Track A — de novo mini-protein binder** (typically 60–90 aa): RFdiffusion →
  ProteinMPNN → Boltz-2. Fully scripted and validated (see PCSK9 worked example).
- **Track B — antibody / nanobody** (CDR-loop design onto a framework): the RFantibody
  pipeline (RFdiffusion_Ab → ProteinMPNN interface design → RF2 prediction/filtering).

Pick the track from what the user wants to make. If they say "binder", "mini-binder", or
"de novo protein" → Track A. If they say "antibody", "nanobody", "VHH", "scFv", or "CDR"
→ Track B. If unclear, **ask** which modality they want.

## Scope

**Does:** end-to-end structure-based binder design against a single protein target, in two
modalities. Shared for both: crop the target epitope, design sequences with ProteinMPNN,
predict/validate the complex, rank by interface confidence (ipTM/pAE, interface PAE, contacts)
and on-target epitope recovery (hotspot recovery), map the epitope in native numbering, and
build a PDF report.
- **Track A (de novo mini-binder):** generate binder backbones with RFdiffusion, co-fold each
  candidate with the target using Boltz-2. Fully scripted and reproduced (PCSK9 example).
- **Track B (antibody/nanobody):** dock + diversify CDR loops onto an antibody framework with
  RFantibody's antibody-finetuned RFdiffusion, then predict/filter with RF2. Uses the RFantibody
  container's own pipeline scripts (documented in `references/hpc_commands.md`).

**Does NOT:** predict experimental binding affinity (ipTM/PAE are confidence, not Kd),
optimize small molecules, design multi-specifics or conjugates, or run wet-lab validation. It
produces prioritized computational hypotheses for experimental testing, not validated binders.
Track B does de novo CDR design onto an existing framework — it is not antibody humanization,
affinity maturation of an existing mAb, or epitope binning of known antibodies.

## Inputs

- **Target structure:** a PDB/mmCIF file (local or fetched by PDB ID), OR a UniProt/AlphaFold
  model. The user should indicate which chain and which residue range (domain/epitope) to target.
- **Optional epitope/hotspots:** specific target residues to bias the interface toward. If the
  user does not specify, **ask** whether to target a defined epitope or let RFdiffusion choose
  (unrestrained). Track any functional site (e.g., a catalytic triad, a receptor-binding loop)
  to report overlap.
- **Binder length range (Track A):** default 60–90 aa (override per request).
- **Framework + CDR loop ranges (Track B):** an HLT-formatted antibody framework (nanobody VHH
  or scFv; RFantibody ships example frameworks) and the CDR loop-length ranges to diversify
  (e.g. `H3:5-13`). Epitope **hotspots are effectively required** for the antibody track.
- **Scale preset:** Track A — quick (~8 backbones), standard (~15), thorough (~30–50). Track B —
  pilot 20–100 designs to tune hotspots/loop lengths, full campaigns 1k–10k.

## Outputs (saved to `/mnt/results/`)

- A ranked candidate table (source of truth): `boltz2_validation_ranking.csv` (Track A) or the
  RF2 score/pAE table (Track B). Includes hotspot recovery and epitope_status columns.
- `all_candidate_metrics.json` — full per-candidate metrics: ipTM/confidence, PAE, contacts,
  target contact residues (native numbering), binder/CDR interface positions, sequence,
  confidence tier, hotspot recovery, epitope_status, domain_start, target_range.
- `construct_scope.json` — sidecar from `prepare_target.py` recording domain_start, target_range
  (the realised construct span), core_range/margin/margin_mode/min_segment (the crop that produced
  it), is_truncated, hotspots, hotspots_present_after_crop, hotspots_dropped_by_crop. This is the
  single source of the construct span for the report.
- `structure_metadata.json` — sidecar from `fetch_structure_metadata.py` recording the PDB entry's
  fetched resolution, experimental method, deposition/release date, title, and chain composition,
  plus a provenance block (source URL + fetch timestamp). Structure facts in the report come from
  here, never hand-typed.
- Figures (PNG + SVG): design-score distribution, 4-panel confidence metrics, target contact
  occupancy map with hotspot guides.
- `report_content.json` — the validated, style-free report content (sections, tables, callouts,
  caveats, sequences) emitted by `build_report.py` after its consistency gate. It is the single
  source the final PDF is rendered from, not a separate report.
- Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.
- The selected candidate sequences as FASTA.

## Prerequisites

- HPC helpers: `from biomni.tool import hpc_search_tools, hpc_run_tool, hpc_get_job_results, hpc_get_logs, hpc_cancel_job`
- A Python env with: `biopython`, `numpy`, `scipy`, `pandas`, `matplotlib`, `pillow`, `reportlab`.
- GPU tools accessed via HPC — Track A: RFdiffusion, ProteinMPNN, Boltz-2; Track B: the
  `rfantibody` tool (bundles antibody-finetuned RFdiffusion, ProteinMPNN interface design, RF2).
  See `references/hpc_commands.md` for exact commands, mandatory flags, and failure modes.

## Report Packaging

Report **styling is owned by the platform**: the final PDF is rendered by the Biomni
`pdf-report-generation` skill (the terminal Workflow step), which supplies the running-header and
footer rules and all layout. This skill does not style or render its own PDF.

`scripts/build_report.py` **prepares and validates** the report before rendering. It implements
the pilot-scale disclosure, truncated-construct disclosure, hotspot-spread check, and a provenance
consistency gate that verifies report claims against the produced artifacts — including that
structure facts were **fetched** from the PDB (not hand-typed) and that the construct span was
**derived** from `construct_scope.json` (not restated in the config). It then writes the report's
content **style-free** to `report_content.json` for `pdf-report-generation` to render. Structure
facts (resolution, experimental method, deposition date) come from `fetch_structure_metadata.py`;
the construct span comes from `prepare_target.py`'s `construct_scope.json`; campaign counts
(n_backbones, n_sequences, n_passed, n_validated) are derived from the pipeline artifacts. None of
these are hand-typed in the config.

**Producing the final PDF report is a required terminal step of this workflow, not optional.**
The report must not be skipped — it is the deliverable that communicates the ranked candidates,
their on-target status, and the limitations to the user.

## Workflow — Track A (de novo mini-protein binder)

Run these in order. Each helper is in `scripts/` and self-checks its inputs (exits non-zero
on a problem). **Verify every stage's output before launching the next HPC job** — GPU jobs
are slow and rate-limited (max 3 concurrent), so a silent mistake is expensive.

### Step 0 — Prepare the target (`scripts/prepare_target.py`)
Crop the target to the domain/epitope of interest so RFdiffusion docks binders to the right
surface, and emit the RFdiffusion contig string.
```
python scripts/prepare_target.py \
  --in <target>.pdb --chain <A> --range <lo-hi> \
  --margin 10 --margin-mode spatial --min-segment 4 \
  --key-residues <r1,r2,...> --hotspots <h1,h2,...> \
  --out target_cropped.pdb --contig-out contig.txt --scope-out construct_scope.json
```
- `--margin-mode spatial` keeps residues within N Å of the core (captures a real 3D surface
  patch); `sequence` keeps a contiguous residue window. `--min-segment` prunes scattered
  singleton residues that would fragment the contig (bad for RFdiffusion). Runs containing a
  `--key-residue` are always kept, and the script **fails** if a key residue would be dropped.
- `--hotspots` records the declared epitope hotspot set in the `construct_scope.json` sidecar
  (the same set passed to `analyze_interface.py --hotspots` and to `ppi.hotspot_res`). It is
  distinct from `--key-residues` (hotspots = epitope-bias set; key-residues = must-survive-crop).
- `--scope-out` writes a `construct_scope.json` sidecar with domain_start, target_range (the
  realised span), core_range/margin/margin_mode/min_segment (the crop that produced it),
  is_truncated, hotspots, hotspots_present_after_crop, hotspots_dropped_by_crop. `build_report.py`
  **derives** the reported construct span from this sidecar (it is never restated in the config)
  and uses core_range/margin to explain nominal-vs-realised when the crop pulled in extra residues.
- **Biological point:** targeting the wrong face (or leaving a blocking prodomain in) wastes the
  whole campaign. Confirm the domain range and any functional site with the user.
- Copy the printed `contigmap.contigs=[...]` line — you pass it to RFdiffusion verbatim.

### Step 1 — Generate binder backbones (RFdiffusion, HPC)
Submit with the contig from Step 0. See `references/hpc_commands.md` §1.
- Use `Complex_base_ckpt.pt` (protein–protein), `noise_scale_ca=0.5`, `noise_scale_frame=0.5`.
- Set `inference.num_designs` from the scale preset. Add `ppi.hotspot_res=[...]` with the same
  hotspot residues passed to `prepare_target.py --hotspots` (only if the user specified an epitope).
- Output: N two-chain PDBs (binder = chain A, target = chain B). Compute a quick per-backbone
  interface-contact count and save a `backbone_summary.json` mapping design id → {contacts}.

### Step 2 — Design sequences (ProteinMPNN, HPC)
Redesign the binder chain with the target held fixed. See §2.
- **Pass all backbones as one tar.gz** (avoids the 8192-char container-override limit).
- `--chain_list 'A'` = chain to DESIGN (the binder). Getting this backwards silently redesigns
  the target. `--num_seq_per_target 4 --sampling_temp '0.1 0.2'` → 8 sequences/backbone.
  `--use_soluble_model` for soluble binders.

### Step 3 — Filter and select (`scripts/filter_sequences.py`)
```
python scripts/filter_sequences.py \
  --seq-dir <mpnn_out>/seqs --backbone-json backbone_summary.json \
  --out-csv analysis/all_sequences.csv --top-n 6
```
- Skips the first FASTA record per file (the original backbone sequence).
- Default filters: `entropy >= 2.0`, `top1_frac <= 0.42`, `n_cys <= 2`, `interface_contacts >= 8`
  — reject low-complexity/repetitive designs, disulfide/oxidation liabilities, and tiny interfaces.
- Selects the **best sequence per backbone**, then the top-N backbones, for topological diversity
  (avoids N near-clones from one backbone).

### Step 4 — Co-fold validation (Boltz-2, HPC)
One YAML per candidate: binder = chain A (single sequence), target = chain B. See §3.
- **Mandatory flags:** `--no_kernels` (Ada sm_89 segfault), `--num_workers 0` (OOM),
  `--use_msa_server` (target MSA). Submit in **waves of ≤3** (GPU concurrency cap; 429 = limit hit).
- The de novo binder has no natural MSA and folds single-sequence — expected.

### Step 5 — Analyze interfaces + rank (`scripts/analyze_interface.py`)
```
python scripts/analyze_interface.py \
  --candidates-csv cand_specs.csv \
  --domain-start <native_first_residue> --hotspots <h1,h2,...> \
  [--catalytic-triad <r1,r2,r3>] [--functional-site <r1,r2,...>] \
  --out-json all_candidate_metrics.json --out-csv boltz2_validation_ranking.csv
```
- Boltz **renumbers** the target 1..N — pass `--domain-start` (native number of the target's
  first modeled residue) to recover native epitope numbering. Getting this wrong mislabels every
  reported residue number — it is the highest-consequence single argument.
- `--hotspots` (the same set passed to `prepare_target.py --hotspots` and `ppi.hotspot_res`)
  scores each candidate for hotspot recovery and assigns an `epitope_status`:
  ON_TARGET (≥ `--min-hotspot-recovery` hotspots recovered), PARTIAL (>0 but fewer), OFF_TARGET
  (0 recovered), or NOT_ASSESSED (no `--hotspots` given — never falls back to ON_TARGET).
- `--catalytic-triad` and `--functional-site` are **optional** functional-site overlap checks;
  use `--catalytic-triad` only when the target has a catalytic triad (e.g. a protease). For a
  receptor-blocking target like PD-L1, use `--hotspots` and omit `--catalytic-triad`.
- Interface PAE uses the **binder-rows × target-cols** PAE block (directional; do not symmetrize).
- Ranks by ipTM (default). Reports a confidence tier per candidate (see below).

### Step 6 — Fetch structure metadata, figures, and validated report content (`fetch_structure_metadata.py`, `make_figures.py`, `build_report.py`)
```
# 6a. Read the target structure's facts FROM the PDB (resolution, method, deposition date,
#     chains) so the report never carries a hand-typed number. Use the target's real PDB id.
python scripts/fetch_structure_metadata.py --pdb 5O45 --out structure_metadata.json

python scripts/make_figures.py --metrics-json all_candidate_metrics.json \
  --ranking-csv boltz2_validation_ranking.csv \
  --all-sequences analysis/all_sequences.csv --selected-csv analysis/all_sequences_selected.csv \
  --outdir figures --prefix fig

python scripts/build_report.py --config report_config.json \
  --ranking-csv boltz2_validation_ranking.csv --metrics-json all_candidate_metrics.json \
  --all-sequences-csv analysis/all_sequences.csv --selected-csv analysis/all_sequences_selected.csv \
  --structure-metadata structure_metadata.json --construct-scope construct_scope.json \
  --out report_content.json   # runs the consistency gate, then emits style-free report content
```
- **Structure facts are fetched, never typed.** `fetch_structure_metadata.py` reads resolution,
  experimental method, and deposition date from the RCSB entry for the target PDB and writes a
  provenance-stamped `structure_metadata.json`. `build_report.py` renders those values (or
  "unavailable" if the fetch failed) — the config has **no** resolution/method field to hand-type.
  This closes the one class of hallucination that config review cannot catch: an external-database
  fact written by hand (e.g. a structure logged at 2.10 Å that RCSB reports at 0.99 Å).
- **The construct span is derived, not restated.** Pass `--construct-scope construct_scope.json`
  (or set `construct_scope` in the config). `build_report.py` takes the reported residue range from
  the scope sidecar's `target_range`, and when the crop pulled in residues beyond the requested
  core it states both ("nominal &lt;domain_label&gt; core &lt;core_range&gt;; the built construct
  spans &lt;target_range&gt; after a &lt;margin&gt; Å &lt;mode&gt; margin"). Put only the domain
  **name** in `domain_label` — never a residue range.
- Fill the rest of `report_config.json` from `references/report_config_template.json` (target
  name/PDB/UniProt, rationale, figure paths, hotspots). Campaign counts are **derived** from the
  artifacts (`--all-sequences-csv`, `--selected-csv`, the ranking CSV) — do not hand-type them.
- `build_report.py` runs a **consistency gate** automatically before emitting the report content.
  It fails (exit 2) if: the config hand-types any structure fact or a `domain_range`; a PDB is
  named but no fetched `structure_metadata.json` is supplied (or it is for a different PDB, or lacks
  provenance); the scope's `target_range` disagrees with the metrics records; or the ranking CSV and
  metrics JSON disagree on candidate sets, ipTM/iface_pae, hotspots, or figures. Use `--no-strict`
  to downgrade to warnings + a 'Provenance warnings' callout carried into the content.
- The validated `report_content.json` (headings, paragraphs, tables, callouts, figures, sequences)
  is the single source the final PDF is rendered from — the report's **styling is applied by
  `pdf-report-generation`** in the terminal step, not here.
- Always run a `Read` media-output-check on each figure PNG; fix and re-check.

### Step 7 — Generate the PDF report, then verify before returning (mandatory terminal step)
This step is mandatory: the run is not complete until the PDF report has been produced.

Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

Render from the validated `report_content.json` (Step 6) so the report says exactly what the
consistency gate verified. Then, before presenting the PDF to the user, confirm:
- The ranked table's **hotspot column** (k/N) matches the metrics JSON for every candidate.
- The **derived counts** (n_backbones, n_sequences, n_passed, n_validated) in the report match
  the all-sequences CSV and the ranking CSV.
- The **construct range** in the report is the `target_range` **derived** from `construct_scope.json`
  (and equals the `target_range` on the metrics records). Where it differs from the requested
  `core_range`, the report states both and explains the margin.
- The **structure facts** (resolution, method, deposition date) shown in the report came from
  `structure_metadata.json` (fetched from RCSB, with a provenance line) — or read "unavailable" if
  the fetch failed. There is no hand-typed resolution anywhere.
- Any **OFF_TARGET** candidates are flagged in the report's off-target (danger) callout.
- Any **NOT_ASSESSED** candidates are flagged in the report's not-assessed (warning) callout.
- Run a `Read` media-output-check on a couple of report pages; fix and re-check.
`build_report.py` runs its consistency gate automatically, but the agent must visually confirm
these items in the produced PDF before returning it.

## Workflow — Track B (antibody / nanobody, RFantibody)

Track B uses the **RFantibody container's own pipeline** (RFdiffusion_Ab → ProteinMPNN
interface design → RF2), submitted through the `rfantibody` HPC tool. There are no local
`scripts/` for the design steps — RFantibody bundles them — so this track is documented as
verified HPC commands in `references/hpc_commands.md` §4. The shared target-prep, epitope
mapping, figures, and report tooling from Track A still apply to the final ranked hits.

### Step A0 — Prepare HLT inputs
- **Target:** crop to ~10 Å around the epitope (reuse `scripts/prepare_target.py` with
  `--hotspots` and `--scope-out`), then relabel the target chain to **`T`** for HLT format.
  The hotspot set recorded in the scope sidecar is the same set passed to `ppi.hotspot_res`
  and later to `analyze_interface.py --hotspots`.
- **Framework:** supply an HLT-formatted antibody framework — chain **`H`** (heavy), **`L`**
  (light, omit for a nanobody), target **`T`**, in that chain order, with `REMARK PDBinfo-LABEL`
  CDR annotations. RFantibody ships ready-made frameworks (nanobody `h-NbBCII10.pdb`,
  scFv `hu-4D5-8_Fv.pdb`) baked into the container — prefer these unless the user provides one.
- **Hotspots:** identify epitope residues as `[T<res>,...]` (target chain T numbering). Effectively
  required for antibodies — without them the CDRs have no surface to dock to. The biological
  reason: CDR loops are diversified to contact a specific epitope patch; without hotspots the
  diffusion has no target surface and produces non-functional loops.

### Step A1 — Dock + diversify CDRs (RFantibody RFdiffusion, HPC)
Antibody-finetuned RFdiffusion with `inference.ckpt_override_path=.../RFdiffusion_Ab.pt`,
`antibody.design_loops=[...]` for CDR length ranges, and `ppi.hotspot_res=[...]`. See §4.1.
Pilot with 20–100 designs to tune hotspots/loop lengths before scaling. The antibody-finetuned
checkpoint produces CDR backbones that are geometrically compatible with the framework's
canonical loop scaffolds — a plain RFdiffusion checkpoint cannot do this.

### Step A2 — Design CDR sequences (RFantibody ProteinMPNN interface design, HPC)
`scripts/proteinmpnn_interface_design.py -pdbdir ... -outpdbdir ...` — designs the CDR-loop
sequences at the interface while keeping the framework fixed. See §4.2. Holding the framework
fixed preserves the antibody's structural integrity while exploring sequence space only where
the CDR contacts the target.

### Step A3 — Predict + filter (RF2, HPC)
`scripts/rf2_predict.py` with `model.num_recycles=10` and `+model.hotspot_fraction=0.1` (the
leading **`+`** is mandatory — it appends a key absent from the struct config; omitting it errors).
See §4.3. **Filter:** RF2 pAE < 10, RMSD(design vs RF2-predicted) < 2 Å, optional Rosetta ddG < −20.
The self-consistency RMSD checks that the designed CDR structure is recoverable by an independent
predictor — a design that RF2 cannot reproduce is likely an artifact of the design process.

### Step A4 — Analyze, rank, report (shared)
Map the epitope in native numbering and build the PDF report as in Track A Steps 5–7. Rank by
RF2 pAE / RMSD instead of Boltz ipTM. (`analyze_interface.py` is written for Boltz-2 CIF/PAE
output; for RF2 outputs, rank on the RF2 score file and reuse the report scaffold rather than the
Boltz-specific parser.) The hotspot recovery and consistency-gate logic from Track A applies
identically — pass `--hotspots` to `analyze_interface.py` so off-target CDR designs are flagged.

**Track B outputs** (in addition to Track A outputs): RFdiffusion_Ab design PDBs, ProteinMPNN
interface-design PDBs, RF2 prediction PDBs + score files. The final ranked table, metrics JSON,
figures, and PDF report use the same formats as Track A.

## Confidence interpretation (report these honestly)

| metric | HIGH / confident | MODERATE / uncertain | LOW / unreliable |
|---|---|---|---|
| ipTM | > 0.8 | 0.6 – 0.8 | < 0.6 |
| interface PAE (Å) | < 5 | 5 – 10 | > 10 |
| hotspot recovery | all N declared hotspots recovered | partial (1 to N−1) | 0 of N (off-target) |

ipTM/PAE report **predicted-geometry confidence, not measured affinity**. On-target epitope
recovery is a **separate axis** from ipTM/PAE: a high-ipTM design that recovers 0 of N declared
hotspots is an **off-target fold, not a hit**, and must be reported as such (OFF_TARGET). A
candidate can legitimately be labelled MODERATE on ipTM while recovering zero hotspots — that
is exactly why hotspot recovery is reported as its own column and callout, not folded into the
confidence tier. Present high-confidence designs as prioritized hypotheses for experimental
validation (expression, SPR/BLI, functional/competition assay), never as confirmed binders.

## Scientific caveats

- **MPNN score ≠ binding.** ProteinMPNN score is sequence recoverability of a backbone, not an
  affinity predictor — that is exactly why Boltz-2 co-folding is an independent filter.
- **Correlation sign trap (important for figures/reports).** MPNN score is better when LOW; ipTM
  is better when HIGH. A scatter of mpnn_score vs ipTM therefore slopes DOWNWARD when the metrics
  AGREE, and the raw Spearman is NEGATIVE. Report the **raw** correlation (with its negative sign,
  matching the plot) and then interpret it ("negative = metrics agree"). Do NOT report only a
  flipped "+ρ (quality-aligned)" value with no plot context — it reads as contradicting the figure.
  Also check whether the top-N / bottom-N candidate **sets** agree even when fine ordering differs.
- **Native numbering.** Always set `--domain-start` so the reported epitope uses the target's real
  residue numbers, not Boltz's 1..N renumbering. Getting `--domain-start` wrong mislabels every
  reported residue number — it is the highest-consequence single argument in the workflow.
- **Charged-sequence tendency.** ProteinMPNN interface designs are often E/R/K-rich; flag
  solubility/developability before synthesis.
- **Pick the right track.** Track A designs a fresh mini-protein backbone (no framework); Track B
  designs CDR loops onto a fixed antibody framework. Do not run a plain-backbone binder workflow
  when the user wants an antibody/nanobody, or vice versa.
- **Track B confidence ≠ Track A metrics.** RFantibody hits are filtered by RF2 pAE (< 10) and
  self-consistency RMSD (design vs RF2-predicted < 2 Å), not Boltz ipTM. Developability of a de
  novo CDR set (immunogenicity, aggregation, expressibility) is not assessed here.
- **This is compute, not proof.** No claim of experimental binding should be made from these outputs.

## References
- `references/hpc_commands.md` — exact commands, mandatory flags, container limits, GPU
  concurrency, and output paths. §1–3 Track A (RFdiffusion / ProteinMPNN / Boltz-2);
  §4 Track B (RFantibody: RFdiffusion_Ab / ProteinMPNN interface design / RF2, HLT format).
- `references/worked_example_pcsk9.md` — a fully reproduced Track A PCSK9 catalytic-domain run
  (15 backbones → 120 sequences → 83 pass → 6 validated → cand2 wins, ipTM 0.9466, PAE 1.76 Å,
  convergent epitope 260–296). The helper scripts reproduce these numbers exactly.
- `references/quick_start_pdl1.md` — a PD-L1 quick-start example showing the hotspot-based
  workflow (no catalytic triad): `--hotspots 115,116,121,122,123`, `--domain-start 18`.
- `references/report_config_template.json` — template config for `build_report.py`.
