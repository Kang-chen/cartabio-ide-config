---
id: "skill_9375050e400a2b52a377933c2f508d68"
name: "target-tractability-druggability"
description: "Use to assess whether a human target is druggable and which modality is most viable. Integrates Open Targets tractability, known drugs, DepMap essentiality, safety, structural pocket detection, and evidence for small molecules, antibodies, or degraders; triggers on druggable pocket or modality-selection questions."
category: "drug_discovery"
visibility: "public"
starting-prompt: "Assess the druggability of EGFR across all modalities and tell me which modality is most viable."
---

# Target Tractability & Druggability Assessment

Given a human protein-coding target, this skill answers a focused drug-discovery
question: **is it druggable, and by which modality?** It composes existing evidence
sources into one reproducible verdict:

1. **Open Targets** tractability buckets (per modality), essentiality (DepMap),
   known drugs / clinical precedent, and safety liabilities.
2. **Structural pocket analysis** with fpocket on the best experimental structure
   (auto-selected from the PDB), falling back to the AlphaFold model, then skipping
   gracefully if neither exists.
3. **LiteratureSearch** for the modality/mechanism context that databases miss.
4. A transparent, target-agnostic **modality scorecard** (0-3 per dimension) that
   names the most viable modality and the emerging frontier.
5. A **Phylo-branded PDF report** (infographic + intro + methods + results + figures
   + conclusions + references + next steps).

This is a "search + compose" skill: it queries the Open Targets Platform GraphQL API directly
(via `opentargets_druggability.py`), reuses the DepMap sign convention documented in the
`gene-essentiality` skill, and ships its own reportlab-based PDF builder
(`build_druggability_report.py`) inspired by the `pdf-report-generation` skill pattern rather than
invoking that skill as a subprocess. The thin helper scripts handle only the fiddly, bug-prone parts
(Open Targets v26+ drug query, structure retrieval/cleaning, fpocket parsing, scorecard, figures,
PDF). LiteratureSearch and GenerateImage are used as Biomni-native tools in Steps 5 and 7.

## When to Use This Skill

Use it when the user wants a druggability / tractability verdict for a **specific
human protein-coding target**, e.g.:

- "Is KRAS druggable? Which modality?"
- "Assess EGFR / PCSK9 / BCL2 tractability across modalities."
- "Does this target have a druggable pocket?"
- "Small molecule vs antibody vs degrader for TARGET X?"
- "Give me a druggability report for TARGET, in the context of DISEASE."

**Scope — this skill is ONLY for human protein-coding targets.** Do **not** use it for:

- Non-human targets (mouse, pathogen, viral proteins) — Open Targets tractability
  and DepMap are human-centric.
- Non-protein targets (RNA, lipids, metabolites) — the pocket/tractability model
  does not apply.
- Disease-first questions ("what are the best targets for disease X?") — use the
  `open-targets` skill's `associatedTargets` query first to pick a target, then
  bring it here.
- Bulk screening of hundreds of targets — this is a per-target deep dive. For
  systematic extraction use Open Targets FTP/BigQuery downloads.

If the target is not protein-coding (check `biotype` from step 1), stop and tell
the user rather than forcing a druggability call.

## Inputs

**Required:**
- A target identifier: gene symbol (e.g. `EGFR`) **or** Ensembl gene ID
  (e.g. `ENSG00000146648`). The symbol is resolved automatically.

**Optional:**
- `--disease "<name>"` or `--efo <EFO/MONDO id>` — adds the target-disease
  association score for disease context. The core assessment is target-centric and
  works without it.
- `--pdb <ID>` — force a specific PDB structure instead of auto-selecting.
- Modality focus — by default **all** Open Targets modalities are assessed; narrow
  in the narrative only if the user asks.

## Outputs

Everything the user should see goes under `/mnt/results/`:

- `report_<SYMBOL>_druggability.pdf` — the primary deliverable.
- `<SYMBOL>_druggability.json` — Open Targets evidence bundle.
- `<SYMBOL>_scorecard.json` — per-modality scores + most-viable / frontier verdict.
- `<SYMBOL>_pockets.json` — fpocket results (if a structure was available).
- figures (`*_tractability_buckets`, `*_pocket_druggability`, `*_modality_scorecard`,
  plus the generated infographic) as `.png` + `.svg`.

Write intermediate/scratch files to `/workspace/` first, then copy finished
deliverables to `/mnt/results/`.

## Reference Docs (read before running)

- `references/opentargets_tractability.md` — what each tractability bucket means
  per modality, the interpretation rubric, the **antibody-intracellular
  deal-breaker**, and Open Targets field-name stability warnings.
- `references/structure_and_pockets.md` — structure source priority, RCSB/AlphaFold
  endpoints, PDB cleaning, running fpocket, the **critical fpocket 1-indexing
  gotcha**, and the druggability-score guide.
- `references/scorecard_rubric.md` — the 0-3 scoring dimensions and how the verdict
  (most viable modality + frontier) is derived.

## Workflow

Run the scripts in order. All paths below assume the skill dir is
`SKILL=/mnt/skills/user/target-tractability-druggability` (adjust if staged
elsewhere) and a working dir `W=/workspace`. Replace `EGFR` with the real target.

### Step 0 — Environment check (once)

`fpocket` is required for the structural step. It is usually preinstalled
(`/opt/conda/bin/fpocket`). If missing:

```bash
conda install -n base -y -c conda-forge -c bioconda fpocket
```

If fpocket cannot be installed, the skill still runs — the structural section is
skipped with an explicit note.

### Step 1 — Open Targets evidence

Resolves the symbol, pulls all-modality tractability, DepMap essentiality, known
drugs, safety liabilities, and (optionally) a disease association.

```bash
python $SKILL/scripts/opentargets_druggability.py \
  --target EGFR \
  --out $W/EGFR_druggability.json
# optional disease context:
#   --disease "lung carcinoma"   (or --efo EFO_0001071)
```

**Check `target.biotype` in the output.** If it is not `protein_coding`, stop and
tell the user this skill does not apply. Also note `data_version` (report which
Open Targets release the numbers came from).

### Step 2 — Structure retrieval (auto PDB -> AlphaFold)

Selects the best experimental structure for the target's UniProt accession (prefers
ligand-bound, high-resolution), classifies bound ligands by heavy-atom count (a
robust, target-agnostic way to find a drug-like reference ligand), and cleans a
single chain for fpocket. Falls back to the AlphaFold model if no suitable PDB
exists.

```bash
# 2a. pick + download the best structure (uniprot comes from step 1 output)
python $SKILL/scripts/fetch_structure.py \
  --uniprot P00533 \
  --outdir $W/EGFR_struct \
  --alphafold-fallback \
  --out $W/EGFR_struct.json

# 2b. clean the chosen structure for fpocket (chosen.pdb_path + keep_ligand
#     come from EGFR_struct.json; keeps the drug-like ligand as the reference)
python $SKILL/scripts/fetch_structure.py \
  --clean $W/EGFR_struct/<chosen>.pdb \
  --keep-ligand FMM \
  --clean-out $W/EGFR_struct/EGFR_holo.pdb \
  --out $W/EGFR_struct_clean.json
```

The clean step reports the **reference-ligand centroid** — pass it to fpocket so
pockets can be labelled "drug/ligand-engaged" vs "distinct/allosteric-type"
generically (no hardcoded residues).

If `source == "none"` (no PDB and no AlphaFold), skip Step 3 and record that the
structural analysis was not possible.

### Step 3 — Pocket detection with fpocket

```bash
python $SKILL/scripts/run_fpocket.py \
  --pdb $W/EGFR_struct/EGFR_holo.pdb \
  --ref-ligand-centroid 17.182 33.932 38.428 \
  --label "EGFR (1XKK, holo)" \
  --out $W/EGFR_pockets.json
```

Optional apo/holo contrast: pass a second cleaned structure via `--pdb-holo` to
detect **cryptic pockets** (a large apo->holo jump in druggability score signals a
pocket that only opens on ligand binding — a key finding for "undruggable"-looking
targets). Druggability score guide: `>0.5` druggable, `0.2-0.5` borderline,
`<0.2` poorly druggable.

### Step 4 — Modality scorecard

Combines tractability + structure + clinical precedent into a 0-3 score per
modality and names the verdict. Antibodies against a target with no
surface-accessibility evidence are forced to 0 (deal-breaker).

```bash
python $SKILL/scripts/compute_scorecard.py \
  --ot $W/EGFR_druggability.json \
  --pockets $W/EGFR_pockets.json \
  --out $W/EGFR_scorecard.json
```

Output gives `most_viable` (best modality) and `frontier` (best *emerging*
modality among those not yet clinically mature) — see `references/scorecard_rubric.md`.

### Step 5 — Literature context (LiteratureSearch tool)

**Use the `LiteratureSearch` tool** (not a script) to gather real, citable evidence
for the narrative and references. Run a few targeted queries, e.g.:

- `"<TARGET> druggability"` / `"<TARGET> small molecule inhibitor"`
- `"<TARGET> antibody therapy"` / `"<TARGET> PROTAC degrader"`
- `"<TARGET> allosteric OR cryptic pocket"`
- `"<TARGET> <disease>"` when a disease context was given

Collect the real citations (authors, year, journal) for the report's reference
list. **Never invent citations** — the report builder only prints references you
pass to it.

### Step 6 — Figures

```bash
python $SKILL/scripts/make_figures.py \
  --ot $W/EGFR_druggability.json \
  --pockets $W/EGFR_pockets.json \
  --scores $W/EGFR_scorecard.json \
  --outdir $W/EGFR_figs
```

Produces three data figures (tractability matrix, pocket druggability bars,
modality scorecard) as `.png` + `.svg`.

### Step 7 — Infographic (GenerateImage tool)

**Use the `GenerateImage` tool** to create a one-page summary infographic (a
left-to-right pipeline: TARGET -> TRACTABILITY -> STRUCTURE+POCKET -> VERDICT, plus
a modality verdict strip). Fill the labels with the real numbers from steps 1-4.
Save as `<SYMBOL>_infographic.png`. Verify it with a `Read` media-output check
before embedding. (AI-rendered text can garble the Ångström symbol — an ASCII "A"
for Å in the image is acceptable.)

### Step 8 — Build the PDF report

Write a `report_config.json` (see the schema the script prints and the EGFR example
below), then:

```bash
python $SKILL/scripts/build_druggability_report.py --config $W/EGFR_report_config.json
cp $W/report_EGFR_druggability.pdf /mnt/results/report_EGFR_druggability.pdf
```

The config wires together the JSON outputs, the figures, the infographic, the
narrative text (which **you** write from the evidence + literature), the real
references, and the optional disease. The builder validates page count and text
extractability. **Always** run a `Read` media-output check on the final PDF and fix
any defects before delivering.

## Report config schema (Step 8)

```json
{
  "target_symbol": "EGFR",
  "ot_json":       "/workspace/EGFR_druggability.json",
  "pockets_json":  "/workspace/EGFR_pockets.json",
  "scores_json":   "/workspace/EGFR_scorecard.json",
  "disease":       null,
  "figures": {
    "tractability": "/workspace/EGFR_figs/fig1_tractability_buckets.png",
    "pockets":      "/workspace/EGFR_figs/fig2_pocket_druggability.png",
    "scorecard":    "/workspace/EGFR_figs/fig3_modality_scorecard.png",
    "infographic":  "/mnt/results/EGFR_infographic.png"
  },
  "narrative": {
    "executive_summary": "...",
    "intro": "...",
    "tractability_note": "...",
    "structure_context": "...",
    "modality_discussion": "...",
    "caveats": ["...", "..."],
    "next_steps": ["...", "..."]
  },
  "references": [
    {"n": 1, "text": "Author et al. Journal Year. Title."}
  ],
  "out": "/workspace/report_EGFR_druggability.pdf"
}
```

`caveats` and `next_steps` may be a list of strings or a single string. Omit any
figure key that does not exist (e.g. no `pockets` when the structure step was
skipped) — the builder degrades gracefully.

## Mandatory caveats (include in every report)

The report builder ships a default caveats block; keep it and add target-specific
ones. Always communicate:

- Open Targets tractability buckets are **heuristic flags**, not proof a program
  will succeed. Field names change between releases — always state the data version.
- fpocket scores are **geometry- and conformation-dependent**. A high score on a
  drug-bound crystal partly reflects the pocket being captured open; a low apo score
  does not rule out a **cryptic** pocket.
- AlphaFold-model pockets (fallback) are **less reliable** than experimental ones.
- The known-drugs list is a **snapshot**, not an exhaustive competitive landscape.
- A favourable pocket/tractability signal is a **hypothesis for experimental
  follow-up**, not a validated druggable site.
- **Antibodies require surface accessibility.** An intracellular target with no
  accessibility evidence is not antibody-tractable regardless of other signals.

## Interpretation notes

- **DepMap sign convention (from `gene-essentiality`):** negative gene-effect =
  essential. More negative = more essential. Do not flip this.
- **Most viable vs frontier:** "most viable" is the highest-scoring modality
  overall (often the clinically mature one); "frontier" is the most promising
  *emerging* modality (e.g. a degrader for a target only drugged by small molecules
  today). Report both — the frontier is usually the more interesting scientific
  insight.
- **Cryptic-pocket story:** if the orthosteric/effector surface scores poorly but a
  ligand-engaged or apo->holo pocket scores high, that is the headline — it explains
  how a "flat, undruggable" target became druggable.

## Worked examples (illustrative — do not hardcode these numbers)

**KRAS (the canonical hard target).** The WT effector-binding surface is shallow
(fpocket ~0.195, poorly druggable), but a WT allosteric α3/loop7 pocket scores
~0.646, and the mutant G12C switch-II pocket scores ~0.838 apo and ~0.993 when
sotorasib-bound. Verdict: small molecule = most viable (High, driven by covalent
G12C drugs); degrader = emerging frontier (Medium); antibody = not viable (None,
intracellular). This is the cryptic/allosteric-pocket story in action.

**EGFR (a well-drugged receptor kinase).** Small molecule and antibody both clear
clinical-precedent buckets; the ectodomain is surface-accessible (so antibodies are
viable, unlike KRAS). The best structure (PDB 1XKK, 2.4 Å, bound to lapatinib/FMM)
yields a druggable ATP-site pocket (fpocket ~0.981). Verdict: small molecule = most
viable (High), antibody = High, degrader = emerging.

## Common pitfalls

- **Forcing a verdict on a non-protein-coding or non-human target.** Check biotype;
  refuse politely if out of scope.
- **fpocket 1-indexing.** Pocket files start at `pocket1` (there is no `pocket0`).
  Parse the integer from the filename directly — do **not** add an offset. See
  `references/structure_and_pockets.md`.
- **Trusting RCSB REST ligand flags.** They are unreliable; classify ligands by
  heavy-atom count from coordinates (the `fetch_structure.py` approach).
- **Inventing citations.** Only pass references gathered via `LiteratureSearch`.
- **Skipping the media-output check** on figures and the final PDF.
