---
id: "skill_56f6a5b3514ba76baf5bca5c0c83d704"
name: "ligand-binding-mode-analysis"
description: "Use to analyze how a ligand binds in a protein-ligand co-crystal from a PDB ID, uploaded PDB/CIF, or target-ligand pair resolved through RCSB. Identifies pocket residues, heavy-atom contacts, hydrogen bonds, ionic/hydrophobic/pi/halogen interactions, and confidence tiers with PLIP or geometry fallback."
category: "molecular_design"
visibility: "public"
starting-prompt: "Map the binding-pocket contacts for <ligand> bound to <protein> using its co-crystal structure and generate a PDF report."
---

# Ligand Binding-Mode Analysis

Map how a small-molecule ligand binds its protein target from a co-crystal
structure: which residues line the pocket, how close each approaches, what type
of interaction it makes, which chemical fragment of the ligand it touches, and
(optionally) whether the pocket is reproduced across structures. Produces a
machine-readable contact table and a Phylo-branded PDF report with an infographic.

This skill generalizes a validated imatinib-ABL1 (PDB 1IEP) analysis into a
target-, ligand-, and structure-agnostic workflow.

## When to Use This Skill

Use this skill when the user wants to:
- **Understand a binding mode** — "How does imatinib bind ABL1?", "What contacts does ritonavir make in HIV protease?"
- **List pocket / binding-site residues** for a co-crystal structure
- **Find hydrogen bonds / salt bridges / hydrophobic contacts** between a ligand and its target
- **Map a ligand's fragments to the residues** they engage (SAR starting point)
- **Compare a pocket across structures** (e.g. two orthologs, apo vs holo, wild-type vs mutant)
- **Generate a binding-pocket / interaction / contact-map report (PDF)** for a drug-target complex

**Don't use for:**
- De novo docking or pose prediction (no ligand pose available) — use AutoDock Vina / Boltz-2
- Protein-protein interface mapping (this is protein-*ligand*)
- Binding free-energy / affinity prediction (contacts are geometric, not energetic)
- Structures with no bound small-molecule ligand (apo) — the skill will say so

**Key concept:** All contacts and interactions are **geometric** assignments from a
single static crystal structure — not energies. Interaction *typing* is done first
with **PLIP** (which protonates the complex and checks donor/acceptor angles), with a
hardened geometry engine as the fallback; both enforce σ-hole angles for halogen
bonds and derive ionic centres from **RDKit formal charges** (not a naive "every N
is +, every O is −" proxy). Every interaction call is tiered:

- **high** — meets strict geometry (angles, genuine formal charges) or is confirmed by PLIP.
- **tentative** — distance-only, near a threshold, or dependent on an assumed
  protonation state (e.g. a weakly basic morpholine N, pKa ~5–6). Tentative calls are
  flagged in the CSV, the report table, and the figures, and should be confirmed
  structurally before they support a conclusion.

Crystallographic models usually lack hydrogens, so hydrogen bonds remain distance/
geometry *candidates*. State this in any summary.

## Installation

| Software | Version | License | Commercial Use | Install |
|----------|---------|---------|----------------|---------|
| biopython | >=1.80 | BSD-3-Clause | Permitted | `uv pip install biopython` |
| numpy | >=1.20 | BSD-3-Clause | Permitted | `uv pip install numpy` |
| rdkit | >=2022.9 | BSD-3-Clause | Permitted | `uv pip install rdkit` |
| matplotlib | >=3.5 | PSF/BSD | Permitted | `uv pip install matplotlib` |
| reportlab | >=3.6 | BSD | Permitted | `uv pip install reportlab` |
| pypdf | >=3.0 | BSD | Permitted | `uv pip install pypdf` |
| pillow | >=9.0 | HPND | Permitted | `uv pip install pillow` |
| openbabel | >=3.1 | GPL-2.0 | Permitted (tool) | present in the Biomni conda env |
| PLIP | >=2.3 | GPL-2.0 | Permitted (tool) | see recipe below (primary interaction engine) |
| pymol-open-source | >=2.5 | BSD-like | Permitted | `conda install -c conda-forge pymol-open-source -y` |

All of biopython, numpy, rdkit, matplotlib, reportlab, pypdf, pillow are in the
Biomni environment.

**PLIP (primary interaction-typing engine).** Interaction typing is done first with
the **Protein-Ligand Interaction Profiler (PLIP)** — a peer-reviewed, community-
standard tool that protonates the complex and applies published geometric criteria
*including donor/acceptor angles* (Salentin et al., *Nucleic Acids Res.* 2015;
Adasme et al., *NAR* 2021). It is invoked as an external tool (GPL-2.0), not vendored
into the skill's BSD code, and OpenBabel (its dependency) is already in the env.

> ⚠️ The PyPI `plip` sdist's `setup.py` tries to **recompile OpenBabel from source**
> and fails in the Biomni image even though OpenBabel is already installed. Install
> from the GitHub source tree instead (pure-Python package):
> ```bash
> git clone --depth 1 https://github.com/pharmai/plip.git /workspace/plip_src
> SITE=$(python3 -c "import site; print(site.getsitepackages()[0])")
> cp -r /workspace/plip_src/plip "$SITE/plip"    # importable, no rebuild
> python3 -c "from plip.structure.preparation import PDBComplex; print('PLIP OK')"
> ```

**If PLIP is unavailable, the skill automatically falls back** to a hardened in-house
distance/angle geometry engine (`classify_interactions.py`) that still enforces
halogen-bond σ-hole angles, derives ionic centres from RDKit formal charges, and
tiers every call by confidence — so the analysis stays defensible either way.

PyMOL is optional — if absent, the skill auto-falls back to a matplotlib 3D view
(install PyMOL for the publication-quality render).

**System requirements:** Internet access for RCSB PDB downloads and (in an agent
session) for the LiteratureSearch tool. No GPU or HPC needed; each structure runs
in ~1-2 min on a standard worker.

## Inputs

The skill accepts three input modes (pick one for the primary structure):

1. **PDB ID** — a 4-character accession, e.g. `"1IEP"`. Downloaded from RCSB.
2. **Local file** — path to a `.pdb`/`.cif`/`.ent` (optionally `.gz`) the user uploaded.
3. **Target + ligand** — a dict `{"target": "ABL1 kinase", "ligand": "STI"}`; the
   skill queries RCSB, ranks co-crystals by resolution + ligand presence, and picks the best.

**Parameters:**
- **ligand_code** (optional): chem-comp code (e.g. `"STI"`). Auto-detected if omitted
  (largest non-additive ligand; ions/buffers/cryoprotectants are filtered out).
- **comparisons** (optional): list of PDB IDs / paths for cross-structure concordance.
- **extended_interactions** (bool, default `False`): core 3 types by default;
  set `True` to also detect pi-stacking, pi-cation, and halogen bonds.
- **use_plip** (bool, default `True`): use PLIP as the primary interaction-typing
  engine (recommended). Set `False` to force the hardened in-house geometry engine.
- **make_3d** (bool, default `True`): render the 3D pocket.

## Outputs

Saved under the chosen `out_dir` (copy user-facing files to `/mnt/results/`):
- **`pocket_contacts.csv`** — one row per pocket residue: residue, min distance,
  core flag, contact counts at 4.0/4.5 A, nearest ligand atom + fragment,
  interaction type, **interaction_confidence** (`high`/`tentative`),
  **interaction_source** (`PLIP`/`geometry`), H-bond details, kinase region (if
  kinase), and per-comparison distances + identity conservation.
- **`figures/F1_interaction_diagram.png/.svg`** — radial interaction diagram.
- **`figures/F2_contact_distance.png/.svg`** — per-residue distance bar chart.
- **`figures/F3_fragment_heatmap.png/.svg`** — fragment x residue contact heatmap.
- **`figures/F4_pocket_3D.png`** and **`F4b_hbond_closeup.png`** — 3D pocket views.
- **PDF report** (write to `/mnt/results/report_<name>.pdf`) — infographic +
  introduction, methods, results (table + figures), conclusions, references, next steps.

## Clarification Questions

Ask these before running if not already clear:

1. **Which structure?** (ASK FIRST)
   - A specific PDB ID? An uploaded structure file? Or a target + ligand to look up?
   - **Or use the bundled example?** `imatinib_abl1` (imatinib/STI bound to ABL1, PDB 1IEP vs 2HYY).
2. **Interaction depth?**
   - **Core (default):** H-bonds, salt bridges, hydrophobic/vdW — well validated.
   - **Extended:** also pi-stacking, pi-cation, halogen bonds (more complete but heuristic).
   - **Always confirm this with the user** — the skill defaults to core 3.
3. **Cross-structure comparison?** Any second structure(s) to check the pocket is reproducible?
4. **Report?** Generate the full PDF, or just the contact table + figures?

## Standard Workflow

**USE THE SCRIPTS. Do not re-implement contact geometry, fragmentation, or the PDF
inline** — the scripts encode validated cutoffs and the Phylo report layout.

All scripts live in `scripts/` and import each other by module name, so run from
the `scripts/` directory (or add it to `sys.path`).

**Step 0 - Confirm interaction depth with the user** (core vs extended). Default core.

**Step 1 - (Optional) Load the bundled example:**
```python
import sys; sys.path.insert(0, "scripts")
from load_example import load_example, check_expectations
params = load_example(extended_interactions=False)   # imatinib-ABL1 kwargs
```

**Step 2 - Run the analysis:**
```python
from run_pipeline import run_analysis

payload = run_analysis(
    primary="1IEP",                 # PDB id | local path | {"target":..,"ligand":..}
    ligand_code="STI",              # optional; auto-detected if omitted
    comparisons=["2HYY"],           # optional
    extended_interactions=False,    # confirm with user first
    use_plip=True,                  # PLIP primary; auto-falls back to hardened geometry
    out_dir="pocket_analysis",
)
# (or: payload = run_analysis(**params) when using the example)
```
**VERIFICATION:** prints `[OK] wrote .../pocket_contacts.csv` and figure paths, and
either `[OK] PLIP typing applied (v...)` or the geometry-fallback notice. Every
contact row carries `interaction_confidence` and `interaction_source`.

**Step 3 - Ground in the literature (agent session):**
Call the platform **LiteratureSearch** tool with queries from
`literature_context.suggested_queries(target_name, ligand_name, is_kinase)`.
Pass the REAL returned references into the payload:
```python
import literature_context as lit
payload["references"] = lit.format_references(my_literature_results)  # real results only
```
Never fabricate references. If none are found, leave it empty — the report will
state the pocket was not cross-referenced.

**Step 4 - Build and validate the PDF:**
```python
from build_report import build_report, validate_pdf
out = build_report(payload, "/mnt/results/report_pocket_1IEP.pdf")
validate_pdf(out)                      # asserts pages >= 2, has text, > 5 KB
```
Then run a visual check:
```
Read(file_path="/mnt/results/report_pocket_1IEP.pdf", mode="media_output_check")
```
Also media-check each figure PNG. If a figure is blank/clipped, regenerate.

**Step 5 - (Regression only) verify against expectations:**
```python
ok, msgs = check_expectations(payload)   # for the imatinib example
print("\n".join(msgs))
```

⚠️ **IF SCRIPTS FAIL — hierarchy:**
1. **Fix and retry (90%)** — install a missing package, check the PDB ID/internet, re-run.
2. **Modify the script (5%)** — edit in place, document the change.
3. **Use as reference (4%)** — read the script, adapt the approach.
4. **Write from scratch (1%)** — only if truly necessary; explain why.

## Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| "No drug-like ligand found" | Structure is apo, or only ions/buffers present | Confirm the structure has a bound ligand; pass `ligand_code` explicitly; see `references/troubleshooting.md`. |
| Wrong ligand picked | Multiple ligands / cofactor present | Pass `ligand_code="XXX"` to force the intended one. |
| RDKit fragmentation all "scaffold" | Bond perception failed on odd coordinates | The skill auto-falls back to a graph-based fragmenter; fragments will be generic (ring sizes/elements). |
| Morpholine labelled "piperidine" | (fixed) single-N ring branch fired before the O check | Fixed: mixed-heteroatom rings (morpholine 1N+1O, thiomorpholine 1N+1S) are now tested first; single-heteroatom branches require exactly one heteroatom. |
| `plip` pip install fails building OpenBabel | PyPI sdist recompiles OpenBabel | Install PLIP from the GitHub source tree (see Installation); OpenBabel is already in the env. Or ignore — the skill auto-falls back to the hardened geometry engine. |
| Salt bridge / pi-cation seems over-called | Ligand has no genuine formal charge, or interaction is long/off-axis | The revised engine only asserts these against RDKit-formal-charged ligand atoms and downgrades borderline calls to `tentative`; check the `interaction_confidence` column. |
| PyMOL import error | pymol-open-source not installed | Install via conda, or accept the matplotlib 3D fallback (auto). |
| Kinase motifs mislabeled | Non-standard numbering / atypical kinase | Motifs are detected from sequence + refined by contacts; if wrong, treat kinase labels as advisory. See `references/kinase_motifs.md`. |
| Unicode boxes in PDF | Raw Greek/subscript characters | The builder already converts alphaC/beta3/Angstrom; keep new text ASCII or use `<sub>`/`<super>`. |
| Two ligand copies mixed | Multi-chain crystal | The pipeline keeps the dominant contacting protomer automatically. |

## Interpretation Guidelines

- **Distances:** minimum heavy-atom distance to the ligand. `<= 4.0 A` = core
  packing contact; `<= 4.5 A` = wider shell (captures longer polar contacts).
- **Candidate H-bonds:** polar-polar within 3.5 A; PLIP additionally enforces a
  donor-H...acceptor angle. Verify geometry in the 3D view before asserting a
  specific donor/acceptor role.
- **Interaction type + confidence per residue:** a residue can carry several tags
  (e.g. "H-bond + hydrophobic"). Each call has a `confidence` tier and a `source`
  (`PLIP` or `geometry`). **Salt bridges and pi-cation interactions require a
  genuinely charged ligand atom** (RDKit formal charge); if the ligand is neutral as
  modelled, these are not asserted. **Halogen bonds require σ-hole directionality**
  (C-X...acceptor ~140-180 deg) to be `high`. Treat any `tentative` call cautiously.
- **Fragment map:** shows which chemical piece of the ligand engages each residue —
  a direct SAR handle (modify a fragment to reach/avoid a residue). Saturated
  N/O six-membered rings are labelled **morpholine** (1N+1O), **thiomorpholine**
  (1N+1S), **piperidine** (1N), **piperazine** (2N) or **pyran** (1O) — the mixed-
  heteroatom cases are tested before the single-heteroatom ones.
- **Kinase layer:** when the target is a kinase, gatekeeper/hinge/DFG/P-loop/
  catalytic-Lys/alphaC-Glu labels are added. Hinge and gatekeeper are refined from
  actual contacts; DFG/HRD/P-loop/beta3-Lys come from conserved sequence motifs.
- **Concordance:** residues reproduced across independent structures with conserved
  identity are more likely to be genuine binding contacts than packing artifacts.
- **Accuracy:** when quoting distances or counts in a summary, copy them from
  `pocket_contacts.csv` or the report payload — do not re-derive from memory.

## Scientific Caveats

- Geometry from a **single static structure** — no dynamics, solvent screening, or
  affinity. Contacts describe *a* binding pose, not the ensemble.
- Hydrogen bonds are **candidates**: even with PLIP's angle check, proton positions
  are not observed in most X-ray models.
- **Salt bridges / pi-cation** depend on the ligand protonation/charge state (PLIP's
  protonation, or RDKit formal charges in the fallback). Weakly basic groups may be
  largely neutral at physiological pH, so such ionic calls are tiered `tentative`
  when charge-model-dependent.
- **Halogen bonds** are only `high` when the C-X...acceptor angle is near-linear;
  distance-only hits are downgraded.
- Ordered **waters are excluded** from the quantitative map; PLIP-detected water
  bridges (if any) are listed separately, not counted as residue contacts.
- **Ligand fragmentation** depends on correct bond perception; the graph fallback
  gives generic fragment names when RDKit cannot sanitize the molecule.
- The **kinase annotation** is heuristic and can mislabel atypical or truncated
  kinases (e.g. it may place the gatekeeper label one residue off); treat labels as
  advisory unless confirmed structurally.

## Suggested Next Steps

1. Inspect key H-bonds / salt bridges in the 3D structure to confirm geometry.
2. Prioritize pocket residues for mutagenesis.
3. Use the fragment-residue map to design analogs.
4. Predict ADMET (`predict_admet_properties`) and dock analogs (AutoDock Vina).
5. For kinases: classify type I vs II from the DFG conformation and hinge pattern.

## References (methods)

- Contact/interaction definitions: `references/contact_definitions.md`, `references/interaction_types.md`
- Ligand & PDB handling: `references/pdb_ligand_handling.md`
- Kinase motifs: `references/kinase_motifs.md`
- Troubleshooting: `references/troubleshooting.md`
- Biomni resources used: `references/biomni_resources.md`

Biological references for a specific pocket must come from the **LiteratureSearch**
tool at run time (never fabricated). The imatinib-ABL1 example is grounded in
Nagar et al., *Cancer Res.* 2002 (PDB 1IEP); Schindler et al., *Science* 2000; and
Cowan-Jacob et al. 2002 (human ABL1-imatinib).
