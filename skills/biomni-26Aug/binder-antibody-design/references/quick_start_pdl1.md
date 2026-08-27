# Quick start — de novo mini-binders against PD-L1

A compact worked example for a receptor-blocking target (PD-L1). Use this as the template
when the target is NOT a protease and the goal is to block a protein–protein interface
(here: the PD-1-binding face of PD-L1). The key difference from the PCSK9 example is that
the epitope is defined by **hotspots** on the binding face, not by a catalytic triad.

## Target

- **Protein:** PD-L1 (programmed death-ligand 1, CD274)
- **Structure:** PDB `5NIK` (or another PD-L1 structure with the PD-1-binding face resolved)
- **Domain used:** IgV domain, native residues ~18–127 (the PD-1-binding face)
- **Hotspots (PD-1-binding face):** 115, 116, 121, 122, 123 — residues that contact PD-1
  directly; a binder that recovers these is on-target, one that does not is off-target.
- **Rationale:** PD-L1 drives T-cell suppression via PD-1 engagement; a mini-binder that
  blocks the PD-1-binding face is a checkpoint-inhibitor alternative to antibodies.

## Step 0 — target prep (`prepare_target.py`)

```
python scripts/prepare_target.py \
  --in 5nik.pdb --chain A --range 18-127 \
  --margin 10 --margin-mode spatial --min-segment 4 \
  --hotspots 115,116,121,122,123 \
  --out target_pdl1_igv.pdb \
  --contig-out contig.txt --scope-out construct_scope.json
```

- `--hotspots 115,116,121,122,123` records the declared epitope set in
  `construct_scope.json`. This is the same set passed to `ppi.hotspot_res` in Step 1
  and to `analyze_interface.py --hotspots` in Step 5.
- `--scope-out construct_scope.json` writes the sidecar that `build_report.py` cross-checks
  against the report's domain range and hotspot declarations.
- **Derive `--domain-start` from the scope sidecar; do not hand-assert it.** A 10 Å spatial
  margin can pull in a residue just outside the nominal core: cropping the nominal 18–127 IgV
  domain lands a construct that starts at **17**, not 18. `construct_scope.json` records the
  realised `domain_start` (17) and `target_range` (17–137); pass that `domain_start` to
  `analyze_interface.py`. Deriving it rather than restating the nominal 18 is what keeps every
  reported residue number correct — it is the highest-consequence single argument in the workflow.
- No `--catalytic-triad` — PD-L1 is not a protease. Use `--hotspots` for the epitope set and
  `--functional-site` only if there is a separate functional site to track.

## Steps 1–4 — RFdiffusion, ProteinMPNN, filter, Boltz-2

See `references/hpc_commands.md` §1–3 and `references/worked_example_pcsk9.md` for the full
commands. The only PD-L1-specific note:

- **Step 1 (RFdiffusion):** pass `ppi.hotspot_res=[A115,A116,A121,A122,A123]` — the same
  hotspot set from `prepare_target.py --hotspots`, in target-chain numbering.
- **Step 3 (filter):** `--top-n 6` (or whatever the scale preset calls for).

## Step 5 — interface analysis (`analyze_interface.py`)

```
# Use the domain_start recorded in construct_scope.json (17 after the 10 A spatial crop),
# not the nominal 18 -- read it from the sidecar rather than retyping it.
python scripts/analyze_interface.py \
  --candidates-csv cand_specs.csv \
  --domain-start 17 --hotspots 115,116,121,122,123 \
  --out-json all_candidate_metrics.json --out-csv boltz2_validation_ranking.csv
```

- `--domain-start 17` (the value in `construct_scope.json`, not the nominal 18) recovers native
  PD-L1 numbering (Boltz renumbers the target 1..N). This makes the metrics `target_range` (17–137)
  agree with the scope sidecar, which `build_report.py` requires.
- `--hotspots 115,116,121,122,123` — the same set from Step 0. Each candidate is scored for
  hotspot recovery and assigned an `epitope_status`: ON_TARGET, PARTIAL, OFF_TARGET, or
  NOT_ASSESSED (only if `--hotspots` is omitted — never falls back to ON_TARGET).
- **No `--catalytic-triad`** — PD-L1 has no catalytic triad. Omitting it means no
  `triad_overlap` key appears in the metrics JSON (the key only exists when its input exists).

## Steps 6–7 — fetch structure metadata, figures, report content, then render + verify

```
# Read PD-L1's structure facts from the PDB (resolution/method/deposition), never hand-type them.
python scripts/fetch_structure_metadata.py --pdb 5NIK --out structure_metadata.json

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

Then render the final PDF from `report_content.json` via the pdf-report-generation skill, which
owns the report styling (running-header and footer rules) — see the terminal Workflow step.

**Report config (`report_config.json`):**
- **Carry `hotspots`:** set `"hotspots": [115, 116, 121, 122, 123]` — the same set from
  Steps 0 and 5. `build_report.py` cross-checks this against `hotspots_declared` on the metrics
  records; a mismatch fails the build.
- **Omit the count keys:** do not set `n_backbones`/`n_sequences`/`n_passed`/`n_validated`.
  They are derived from `--all-sequences-csv`, `--selected-csv`, and the ranking CSV. If you do
  include them, a value disagreeing with the derived one fails the build.
- **Do NOT set a construct range.** There is no `domain_range` key. The reported span is derived
  from `construct_scope.json` (`target_range`, here `17-137` after the 10 Å spatial crop). Set the
  domain **name** only: `"domain_label": "IgV domain"`. The report then states "nominal IgV domain
  core 18-127; the built construct spans 17-137 after a 10 Å spatial margin", so no reader has to
  reconcile two numbers. Point `"construct_scope": "construct_scope.json"` (or pass
  `--construct-scope`).
- **Do NOT set a resolution/method.** There is no `target_resolution` key. Point
  `"structure_metadata": "structure_metadata.json"` (or pass `--structure-metadata`) at the fetched
  sidecar; the report shows the RCSB resolution/method/deposition with provenance, or "unavailable"
  if the fetch failed. It never falls back to a typed default.

**Verify before returning (Step 7):**
- The ranked table's hotspot column (k/N) matches the metrics JSON for every candidate.
- Any OFF_TARGET candidates (0 of 5 hotspots) appear in the report's off-target (danger) callout.
- The construct range in the report and figures is the `target_range` derived from
  `construct_scope.json` (17–137) and matches the metrics records; the nominal core 18–127 is
  explained alongside it.
- The resolution/method/deposition shown came from `structure_metadata.json` (fetched from RCSB),
  not a hand-typed value.
- `build_report.py` runs its consistency gate automatically — confirm it passed (exit 0).
