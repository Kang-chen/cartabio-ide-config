---
id: "skill_6e83f49077c908119c713fef84e79b6f"
name: "protein-structure-prediction"
description: "Use to predict a protein or complex 3D structure from sequence, FASTA, gene name, or UniProt accession with AlphaFold2, Boltz-2, Chai-1, or ESMCFold2. Returns PDB/CIF, per-residue pLDDT, pTM, confidence-band and UniProt-domain summaries, and supports explicitly requested cross-method comparisons."
category: "molecular_design"
visibility: "public"
starting-prompt: "Predict the 3D structure of human PCSK9 (UniProt Q8NBP7) from its canonical sequence, selecting the predictor by size and running it under the bounded-poll and fallback guard so the run never hangs. Report per-residue pLDDT on a 0-100 scale with pTM where available, the confidence-band breakdown, and a domain-resolved pLDDT breakdown whose ranges come from the fetched UniProt feature table (flagging overlapping and unannotated regions rather than inventing boundaries). State which predictor actually produced the numbers and whether any fallback occurred, then assemble the final PDF report."
---

# Protein Structure Prediction (multi-method, HPC)

Fold a protein with one or more of four state-of-the-art predictors and report
per-residue confidence (pLDDT), plus pTM where available, a confidence-band
breakdown, and a domain-resolved breakdown keyed to UniProt annotations. All four
predictors run as HPC jobs via `biomni.tool` HPC helpers. This skill was built and
verified end-to-end on real jobs (human B2M, mature chain); the exact commands,
output-file layouts, and pLDDT scales in `references/methods_reference.md` are
copied from verified runs, not from memory.

**Anti-improvisation contract.** Three summaries that used to be hand-rolled by
the agent — the confidence-band counts, the per-domain breakdown, and the
predictor/fallback disclosure — are now packaged functions that **derive from the
run's own produced files** (the pLDDT CSV, the fetched UniProt feature table, and
the run manifest). Do not re-implement any of them ad hoc; an earlier audited run
that did so contradicted its own fetched data (wrong domain boundaries; band
labels that did not match the binning).

## Scope

- **Does**: run AlphaFold v2, Boltz-2, Chai-1, and/or ESMCFold2 on a protein
  sequence; extract per-residue pLDDT normalized to 0–100; report pTM; save
  structure files (PDB/CIF), a per-residue CSV, and a confidence plot; produce a
  **confidence-band breakdown** and a **UniProt-domain-resolved breakdown** from
  packaged functions; disclose the predictor actually used and any fallback from
  the run manifest; and assemble a **final PDF report** (required terminal step).
  The **automated orchestrator** (`fold_orchestrate.py`: size default +
  poll-timeout + fallback) is a **single-chain monomer** path and **rejects
  complexes** rather than silently mis-folding them. Multi-chain **complexes** are
  supported only via a **documented manual path** (AlphaFold-multimer / Boltz /
  Chai per `references/methods_reference.md`; ESMCFold2 is monomer-only), not by
  the automated loop.
- **Does NOT**: dock small molecules as a primary task, run MD, do experimental
  structure comparison (RMSD/TM-align to a PDB) unless the user asks, or design
  sequences. Cross-method **comparison is opt-in**, not automatic. It does **not**
  fabricate domain boundaries: if no UniProt feature table is available, the
  domain breakdown is omitted with a stated reason, never approximated.

## Inputs (accept any one)

1. **Raw amino-acid sequence** — a plain protein string pasted by the user.
2. **UniProt accession or gene/protein name** — fetch the sequence from UniProt
   (`https://rest.uniprot.org/uniprotkb/<acc>.fasta`). For a gene/name, resolve to
   an accession first (UniProt search API, prefer reviewed/Swiss-Prot human unless
   told otherwise). **Default to the full canonical sequence.** Only use the mature
   chain (signal peptide / propeptide removed) if the user explicitly asks — and
   if you do, state which residues you removed. An accession is also what unlocks
   the **domain-resolved breakdown** (its feature table is fetched from UniProt).
3. **Uploaded FASTA** — one or more sequences from `/mnt/user-uploads/`.
4. **Multi-chain complex** — two or more chains (homo- or hetero-oligomer),
   optionally with ligands for Boltz/Chai (manual path only).

**Report** the resolved accession, length, and first/last residues so the choice
is auditable. In interactive use you may confirm the sequence before submitting
(folding jobs are GPU-expensive); for **unattended runs, do not block on
confirmation** — proceed with the resolved canonical sequence and record it in the
run manifest.

## Outputs (saved to `/mnt/results/`)

- Structure file(s): top-ranked model per method (`*.pdb` / `*.cif`).
- `<name>_<method>_plddt.csv` — per-residue pLDDT on a 0–100 scale.
- `<name>_<method>_plddt.png` — per-residue confidence plot.
- `<name>_<method>_bands.csv` — confidence-band counts/percent/mean, written by
  `extract_plddt.py` from the canonical `band_breakdown()` (item 2 below).
- `<name>_confidence_breakdown.json` — combined band + domain-resolved breakdown
  from `confidence_breakdown.py`. The domain section reports domain-tier features,
  a separate `sequence_features` category (signal peptide / propeptide / transit
  peptide / chain), per-segment `no_domain_feature` gaps (each listing any covering
  sequence-level feature), and truly `uncovered` residues; present when a UniProt
  feature table is available and **omitted-with-reason** otherwise.
- `<name>_run_manifest.json` — the chosen predictor, every attempt (method,
  job_id, status, seconds, outcome, n_files), the `poll_timeout_s` actually used,
  and the `fallback_trail`. Always written by the orchestrator so an unattended
  run is auditable.
- Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.
- Report the mean pLDDT, pTM (if available), the confidence-band breakdown, the
  domain-resolved breakdown, **and which predictor was used / whether a fallback
  occurred** in chat.

## Method selection (size-first default)

This skill is built to run **unattended and robustly**. The default predictor is
chosen by **sequence length**, not by asking, because the fast MSA-free predictor
finishes reliably while the MSA-based predictors can stall on the cluster's
genetic-search step. Always **announce the chosen predictor and why**.

> **Model identity.** The fast MSA-free default is **ESMCFold2** (built on
> EvolutionaryScale's **ESM‑C / ESM Cambrian** language model), submitted to the
> cluster as `tool_id="esmcfold2"` (`esmc fold …`). This is **not** the older
> 2022 **ESMFold** (ESM‑2-based, Meta) — it is a newer, distinct model. The CLI
> method token and orchestrator parameters keep the legacy spelling
> (`--methods esmfold`, `--esmfold-max-len`) for backward compatibility, but they
> select **ESMCFold2**.

**Default rule (monomer):**

| Sequence length | Default predictor | Why |
|---|---|---|
| **≤ 400 aa** | **ESMCFold2** | Single-sequence LM, **no MSA** → finishes in seconds and never blocks on the MSA server. Strong accuracy on well-folded compact proteins. Pass `--max-length <len>` so 201–400 aa is accepted. |
| **> 400 aa** | **AlphaFold v2** (primary) | Beyond ESMCFold2's default range; MSA + templates. Runs **under the poll-timeout + fallback guard** (see Workflow) so a stalled MSA still yields a result. |

**Overrides:**
- If the user **explicitly names** a method (or asks for "highest accuracy" /
  AlphaFold), use it — but still run it under the poll-timeout + fallback guard.
  AlphaFold on a ≤ 400 aa protein is therefore *opt-in*, not the default.
- **Complexes (multi-chain):** the automated orchestrator
  (`fold_orchestrate.py`) is **single-chain only** and **rejects complexes**
  (it would otherwise silently fold one chain as a monomer). For a complex, build
  the multimer job **manually** per `references/methods_reference.md`
  (AlphaFold-multimer: `--model_preset=multimer` with one FASTA record per chain
  copy; or Boltz/Chai with one block per chain), poll it with a bounded timeout
  the same way, and extract with `scripts/extract_plddt.py`. ESMCFold2 is monomer-
  only and is never a complex option.
- **Ligands / nucleic acids:** use **Boltz-2** or **Chai-1** — also via the
  manual path (the orchestrator's automated loop is protein-monomer only).

The default `esmfold_max_len` boundary (400) is a parameter of the orchestrator;
raise or lower it if the user requests. Above the boundary, ESMCFold2 is still the
last-resort fallback (with `--max-length <len>`, accepting OOM risk).

Rationale for the size-first default (recorded assumption): ESMCFold2's inference
is ~seconds and MSA-free, so for the common case (small/medium monomers) it is
both the fastest and the most reliable choice; AlphaFold's advantage is mainly on
hard/low-homology or larger targets and depends on a healthy MSA server.

## Confidence metrics — and what the numbers do and do not mean

- **pLDDT** — per-residue local confidence, reported on a **0–100** scale (some
  methods emit 0–1 natively; `extract_plddt.py` normalizes). **pLDDT is the
  model's estimate of its own local accuracy (calibrated to lDDT-Cα), NOT a
  measurement of correctness.** A residue can be confidently wrong — high pLDDT
  does not verify the fold against experiment, the biological conformational
  state, or the effect of missing partners/ligands. Never present pLDDT as
  "accuracy."
- **A low-confidence region is often biology, not failure.** Intrinsically
  disordered regions, flexible loops, linkers, and termini genuinely lack a single
  fixed structure and *should* score low pLDDT — that is the model correctly
  reporting disorder. Treat low-pLDDT stretches as candidate disorder/flexibility
  and cross-check annotations before calling a prediction "bad."
- **The predictor changes what the number means.** MSA-based methods (AlphaFold,
  Boltz-2, Chai-1) draw confidence partly from evolutionary-coupling depth;
  single-sequence **ESMCFold2 has no MSA and no pTM**, so its pLDDT is calibrated
  differently. A pLDDT of 80 from ESMCFold2 is not interchangeable with 80 from
  AlphaFold. Always report which predictor produced the number.
- **Confidence bands (item 2 — one function, unambiguous boundaries).** Bands are
  **AlphaFold-calibrated**: very high, confident, low, very low. Compute every
  band count with the single packaged function
  `confidence_breakdown.band_breakdown(plddt)` — **never** an ad-hoc `cut()`/
  histogram. Its stated convention is **lower-bound inclusive, upper-bound
  exclusive**, so the label always matches the computation:
  `very_high = pLDDT ≥ 90`, `confident = 70 ≤ pLDDT < 90`,
  `low = 50 ≤ pLDDT < 70`, `very_low = pLDDT < 50` (exactly 50 → low, 70 →
  confident, 90 → very high). State that these cutoffs are AlphaFold-calibrated
  and only approximately transfer to Boltz-2 / Chai-1 / ESMCFold2.
- **pTM** — global fold confidence (0–1); available for AlphaFold (ptm preset),
  Boltz-2, and Chai-1. **Not available for ESMCFold2.**
- **ipTM** — interface confidence for complexes (Boltz/Chai).

## Workflow

1. **Resolve the input to a sequence.** Get the raw sequence (paste, UniProt
   fetch, gene→accession lookup, or FASTA). Report accession, length, and the
   first/last few residues. Flag signal peptides if the user wants a mature chain.
   *Why:* residue numbering in every output is 1..N of the **submitted** sequence;
   if you trim it, all downstream positions (and domain ranges) shift, so record
   what was submitted.
2. **Choose the predictor by size** (see Method selection). `len ≤ 400` →
   ESMCFold2; `len > 400` → AlphaFold v2; honor explicit user picks. A **complex**
   is not handled by the automated orchestrator — route it to the manual multimer
   path (the orchestrator rejects complexes). Announce the choice and the reason.
   *Why:* the predictor determines both reliability (MSA server dependence) and
   the *meaning* of the confidence number (see Confidence metrics).
3. **Check the ESMCFold2 length.** ESMCFold2's stock `--max-length` is 200; this
   skill passes `--max-length <len>` so anything ≤ the `esmfold_max_len` boundary
   (400 by default) is accepted. For very long sequences the orchestrator uses an
   MSA-based predictor instead (with ESMCFold2 as last-resort fallback).
4. **Run the orchestrator — DO NOT wait for a completion callback.** Prefer
   `scripts/fold_orchestrate.py` (`orchestrate_fold(...)` or its CLI). It performs
   the whole robust loop in one call:
   **submit → poll `hpc_get_job_results()` with a bounded timeout → detect
   stall/timeout/empty-output → fall back to a faster predictor → run the extractor
   on the winning output → write a run manifest.** It uses the exact verified
   commands/flags from `references/methods_reference.md` (AlphaFold
   `--use_gpu_relax=false`, `--model_preset=monomer_ptm`; Boltz-2
   `--num_workers 0 --use_msa_server --no_kernels`; Chai `--num-diffn-samples N`,
   **never** `--num-samples`; ESMCFold2 `--max-length <len>`), persists job IDs to
   `/mnt/shared-workspace/shared/`, and respects the **3-concurrent-GPU-job limit**
   (HTTP 429 → defer). **Poll parameters (defaults):** `poll_timeout_s=900`
   (~15 min/job), `poll_interval_s=30`, with an early-exit when a job is still
   searching with **0 output files** at the timeout bound. Raise `poll_timeout_s`
   for large/low-homology MSA targets that legitimately need longer. **Fallback
   chain:** ≤ 400 aa → ESMCFold2; > 400 aa → Boltz-2 then ESMCFold2
   (`--max-length <len>`); if all fail → proceed with whatever completed, else
   report a structured failure. A method is tried **at most once** and never falls
   back to itself. This design means the run **always produces a deliverable and
   never hangs** on a callback.
5. **Read the results from the orchestrator.** It returns / records mean pLDDT,
   pTM (if available), the selected model, the structure/CSV/plot paths, and a
   **`<name>_run_manifest.json`** capturing `chosen_predictor`, every `attempt`
   (method, job_id, status, seconds, n_files, outcome), the `poll_timeout_s`
   actually used, and the `fallback_trail`. (If you run a single method by hand
   instead of the orchestrator, still poll `hpc_get_job_results()` with a bounded
   timeout — never block on a callback — then run `scripts/extract_plddt.py` on its
   output directory.)
6. **Confidence-band breakdown (required whenever bands are reported).** Take band
   counts from `<name>_<method>_bands.csv` (written by `extract_plddt.py`) or call
   `confidence_breakdown.band_breakdown(plddt)` directly. **Do not re-bin pLDDT
   with an ad-hoc `cut()`/histogram** — the packaged function is the single source
   and its boundary convention is fixed and labelled (see Confidence metrics).
   *Why:* bands communicate what fraction of the model is backbone-reliable vs.
   likely disordered; if the printed cutoffs do not match the computation the
   summary is not reproducible.
7. **Domain-resolved breakdown (required whenever a domain breakdown is
   reported).** Fetch the UniProt feature table with
   `confidence_breakdown.fetch_uniprot_features(accession)` and compute
   `confidence_breakdown.domain_breakdown(plddt, features)`. **The ranges MUST come
   from that fetched feature table — never a partition you compose by eye.** The
   function fetches two tiers and handles the real shape of UniProt data:
   domain-tier **overlapping** features (reported as belonging to *both*, or
   flagged as an `overlap` segment listing every covering feature — never silently
   assigned to one); sequence-level features (**signal peptide, propeptide,
   transit peptide, chain**) reported in their own `sequence_features` category
   rather than dropped; domain **gaps** labelled `no_domain_feature`, each listing
   any covering sequence-level feature (so a low-pLDDT signal peptide reads as a
   signal peptide, not a blank); and only residues covered by **no** feature at all
   are flagged `uncovered` — never given an invented boundary. If no feature table
   is available (no accession, or UniProt returns none), the breakdown is
   **omitted with a stated reason** — never approximated. *Why:* proteins are
   modular — prodomains, linkers, and termini fold with different intrinsic order
   than catalytic cores — so per-domain
   confidence localizes where the model is trustworthy vs. where it reports genuine
   flexibility. Using self-composed bins instead of the annotated boundaries
   misattributes (dis)order to the wrong domain (the exact error this step
   prevents: residues at a domain seam were assigned to one domain when UniProt
   annotates two).
8. **Run-provenance disclosure + gate (required).** Read the manifest and, when
   `fallback_trail` is non-empty (the delivered numbers did not come from the
   requested/primary predictor), **disclose it in the results narrative — not only
   a caveats footnote** — using `run_provenance.render_run_provenance(manifest)`.
   The disclosure must state: which method was **requested**, that it was
   **cancelled at the poll bound while still running with 0 output files**, which
   method **actually produced every reported number**, the **poll bound actually
   used** (from the manifest's `poll_timeout_s`, not a documented default), and
   that the requested method **may have been obtainable with a longer bound**.
   Before finalizing the report, run the gate
   `run_provenance.check_report(manifest, report_text)` (CLI:
   `python scripts/run_provenance.py --manifest <json> --report <file>`); it
   **fails loudly** if a fallback is present but the disclosure is missing. *Why:*
   a stalled MSA can silently swap the predictor the user asked for; an
   undisclosed swap makes the confidence numbers mean something different from what
   was requested.
9. **Comparison (ONLY if the user asked).** After ≥2 methods finish, align all
   per-residue pLDDT vectors by residue index (they must be equal length — same
   input sequence), then compute: overall mean per-residue pLDDT, per-residue
   cross-method mean and SD, and the highest-disagreement residues (largest SD).
   Produce a per-residue overlay plot, a distribution plot, and a summary-stats
   table. Do **not** compute inter-method RMSD/TM-score unless explicitly asked
   (that is structural superposition, out of default scope).
10. **Figure/infographic value integrity (required whenever a figure or
    infographic states a number).** Every number shown on the summary infographic
    or a figure caption MUST be read from the exported tables at generation time,
    never typed by hand. Get the canonical `label: value` strings with
    `figure_value_guard.derive_infographic_values(<bands_csv|breakdown_json>)` and
    paste them into the GenerateImage prompt; do not type a percentage. Before
    finalizing, run the gate
    `figure_value_guard.check_infographic(prompt_text, <bands_csv|breakdown_json>)`
    (CLI: `python scripts/figure_value_guard.py --breakdown <table>
    --infographic-text <file>`); it **fails loudly** if any stated value disagrees
    with the exported table. *Why:* a hand-typed, silently-rounded value on the
    headline figure contradicts the very band table the report is built from (the
    exact defect this prevents: an infographic reading "Confident 70-90: 30%" when
    the band table says 29.48%).
11. **Final report (required terminal step — the run is not complete until it is
    produced).**
    Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

## Scientific caveats

- **pLDDT is confidence, not correctness.** It is the model's self-estimate of
  local accuracy; it does not verify the fold experimentally or confirm the
  biological conformational state. Do not report it as accuracy.
- **Low pLDDT often means genuine disorder/flexibility**, not a failed
  prediction. Disordered regions, loops, and termini legitimately score low; treat
  them as candidate flexible/disordered regions rather than errors.
- pLDDT bands are AlphaFold-calibrated; treat cross-method band comparisons as
  approximate. Use the single `band_breakdown()` convention so labels always match
  the binning.
- **Domain breakdown depends on UniProt annotation.** Ranges are taken verbatim
  from the fetched feature table; overlaps and gaps are real and reported as such.
  If you trimmed the sequence, UniProt feature coordinates will not line up with
  the submitted 1..N numbering — re-map or note the offset. With no feature table
  the breakdown is omitted, not approximated.
- ESMCFold2 (single-sequence) is typically less accurate than MSA-based methods on
  hard/low-homology targets and has no pTM; expect lower, differently-calibrated
  pLDDT.
- MSA-based methods depend on the MSA server being reachable; a failed MSA
  silently degrades quality — check logs (`hpc_get_logs`) if pLDDT is unexpectedly
  low.
- Use the **full canonical** sequence unless a mature/domain chain is explicitly
  requested; residue numbering in all outputs is 1..N of the **submitted**
  sequence, which will differ from UniProt numbering if you trimmed it.
- Complexes: ESMCFold2 is monomer-only; use AlphaFold-multimer, Boltz, or Chai via
  the manual path.
- Folding jobs are GPU-expensive and rate-limited (3 concurrent). Avoid redundant
  runs.
- **Unattended robustness (this skill's contract):** never block on a completion
  callback. Always poll with a bounded timeout and fall back to a faster predictor
  (or proceed with whatever finished) so a stalled MSA/genetic-search step can
  never hang the run. When a fallback fires, the delivered model is whatever
  finished — record it in the manifest and **disclose it in the results** (step 8),
  noting the size/quality tradeoff (e.g. ESMCFold2 has no pTM and is
  single-sequence) and that the requested method might have finished with a longer
  poll bound.

## Files in this skill

- `scripts/fold_orchestrate.py` — **the robust unattended driver.** One call
  resolves the sequence, picks the predictor by size, submits the HPC job, polls
  `hpc_get_job_results()` with a bounded timeout, falls back to a faster predictor
  on stall/timeout/empty output, runs `extract_plddt.py` on the winner, and writes
  `<name>_run_manifest.json` (chosen predictor + every attempt + `poll_timeout_s` +
  fallback trail). Python: `from fold_orchestrate import orchestrate_fold`; CLI:
  `python fold_orchestrate.py --seq <SEQ> --name <NAME> --out <DIR>
  [--methods esmfold|alphafold|boltz|chai ...] [--esmfold-max-len 400]
  [--poll-timeout-s 900] [--poll-interval-s 30]`. Use this by default instead of
  hand-submitting jobs and waiting for callbacks.
- `scripts/extract_plddt.py` — unified per-residue pLDDT extractor for all four
  methods (auto-detects method from an output dir, normalizes to 0–100, writes
  CSV + plot, and a `<prefix>_bands.csv` from the canonical `band_breakdown()`).
  CLI: `python extract_plddt.py --method auto --job-dir <dir>
  --out-prefix <prefix>`. Verified to reproduce AlphaFold 97.16, Boltz-2 93.73,
  Chai-1 95.95, ESMCFold2 86.47 on the B2M validation set. Called automatically by
  `fold_orchestrate.py`.
- `scripts/confidence_breakdown.py` — **canonical confidence breakdowns (single
  source).** `band_breakdown(plddt)` for AlphaFold-calibrated band counts with a
  fixed, labelled boundary convention; `fetch_uniprot_features(accession)` to pull
  the feature table (**domain tier + a separate sequence-level tier**: signal
  peptide, propeptide, transit peptide, chain); `domain_breakdown(plddt, features)`
  for the overlap-aware per-domain breakdown that reports `sequence_features` in
  their own category, labels domain gaps `no_domain_feature` (listing any covering
  sequence-level feature), and flags only truly `uncovered` residues. CLI:
  `python confidence_breakdown.py --plddt-csv <csv> --accession <ACC>
  [--name <NAME>] [--features-json <json>] --out <json>`. **Use these instead of
  hand-rolling bands or domain bins.**
- `scripts/run_provenance.py` — **predictor/fallback disclosure + gate.**
  `render_run_provenance(manifest)` builds the disclosure text from the manifest;
  `check_report(manifest, report_text)` fails loudly if a fallback occurred but the
  report omits it. CLI: `python run_provenance.py --manifest <json>
  [--report <file> | --render]`.
- `scripts/figure_value_guard.py` — **figure/infographic value integrity + gate.**
  `derive_infographic_values(<bands_csv|breakdown_json>)` returns the canonical
  `label: value` strings (band percents at the exported precision) to paste into
  the GenerateImage prompt so no number is typed; `check_infographic(prompt_text,
  <table>)` fails loudly if any value stated on the figure/infographic disagrees
  with the exported table. CLI: `python figure_value_guard.py --breakdown <table>
  [--infographic-text <file> | --render]`.
- `references/methods_reference.md` — verified commands, mandatory flags, input
  formats, output-file layouts, pLDDT extraction, band/domain breakdown semantics,
  and the poll-timeout + fallback policy. **Read this before submitting any job.**
- `assets/eval/` — offline unit tests for the band boundaries, the domain-breakdown
  overlap / no-domain-feature / sequence-level-category logic (the real Q8NBP7
  case), the provenance gate, and the figure-value guard.
