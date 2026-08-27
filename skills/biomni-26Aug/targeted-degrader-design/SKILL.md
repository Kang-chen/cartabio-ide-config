---
id: "skill_1de0c57c38270f6702c80157011ab521"
name: targeted-degrader-design
description: "Use to design and computationally triage heterobifunctional PROTACs or targeted degraders for a protein. Covers CRBN/VHL E3 selection, warheads and exit vectors, linker enumeration, RDKit assembly, bRo5/ADMET scoring, docking, candidate ranking, and optional ternary-complex modeling."
category: "molecular_design"
visibility: "public"
starting-prompt: "Design and triage targeted protein degraders (PROTACs) for my target: pick E3/warhead/linker, assemble a library, and rank candidates."
---

# Targeted Degrader (PROTAC) Design

Design and triage bifunctional degraders against a protein target with a workflow
that keeps **genuinely computed results** (chemistry, docking, ADMET) explicitly
separate from **precedent-reasoned heuristics** (candidate prioritization). The
single most important habit this skill enforces: never present a prioritization
score as a calibrated potency prediction.

---

## Scope

- **Does:** target/E3/warhead/linker selection, warhead docking for exit-vector
  analysis, combinatorial RDKit assembly of a degrader library, physicochemical /
  bRo5 + ADMET profiling, transparent multi-component prioritization scoring, a
  branded PDF report, and (advanced) ternary-complex structural modeling.
- **Does NOT:** predict absolute DC50 / Dmax; model degradation kinetics or
  ubiquitin transfer; guarantee synthetic accessibility of assembled linkages;
  perform de novo molecular-glue discovery (a distinct problem — see the
  ternary tier and `references/ternary_modeling.md` for how glues differ).

## Inputs

- A **target** (gene/protein name or ChEMBL/UniProt ID). If unspecified, ask; do
  not silently pick one.
- Optional user constraints: E3 ligase(s), warhead source, linker preferences,
  oral vs non-oral intent.
- Optional: a target **PDB structure** (or let the skill choose one from RCSB).

## Outputs (save under `/mnt/results/<TARGET>_PROTAC/`)

- `tables/` — building blocks, full library + descriptors, ADMET-merged, scored,
  shortlist, reference degraders (all CSV).
- `figures/` — building blocks, docking pose, property envelope, ADMET heatmap,
  score decomposition, shortlist structures, linker trends (PNG + SVG).
- `data/` — receptor/pocket files, ADMET raw log, score weights, envelope JSON.
- `report_<TARGET>_PROTAC_design.pdf` — Phylo-branded report (see the
  `pdf-report-generation` skill for the template).

---

## Environment (verified Biomni tools)

Confirm availability before relying on any tool. The following are in the Biomni
catalog / installed and are the intended engines for this skill:

| Purpose | Tool | Notes |
|---|---|---|
| Cheminformatics | **RDKit** | assembly, descriptors, substructure QC |
| Docking (warhead) | **AutoDock Vina** (CLI `vina`), **AutoSite**, **pyscreener** | pocket detection + docking |
| Receptor/ligand prep | **ADFR Suite** (`prepare_receptor`/`prepare_ligand`) | PDBQT prep — **`prohibited` for commercial use**: the MGLTOOLS SOFTWARE LICENSE AGREEMENT states "The programs received by you will be used only for NON COMMERCIAL purposes" and defines commercial usage as "revenues generating activities... using this software for consulting activities and selling applications built on top of, or using this software." Free for academic/non-commercial use only. A commercial license from Scripps Research / Michel Sanner is required for any commercial deployment, or substitute an open-source PDBQT-prep alternative (e.g. **Meeko**, **OpenBabel**, or RDKit-based prep). |
| ADMET | **DeepPurpose** (Python package, `pip install deeppurpose`; `from deeppurpose import ...`) | 16 endpoints from SMILES. Note: `predict_admet_properties` is **not** exported by `biomni.tool` — use the DeepPurpose package directly. |
| Descriptors/ML | **descriptastorus**, **deeppurpose** | feature generation |
| Target / bioactivity | **ChEMBL**, **PubChem** (REST), **RCSB PDB** | IDs, SMILES, structures |
| Report | **reportlab** | PDF (via `pdf-report-generation` skill) |
| Ternary (advanced) | **boltz-2**, **chai-1**, **boltzgen** (HPC) | see ternary tier |

**PubChem SMILES note (2025 schema):** the property field was renamed. Request
SMILES by trying, in order: `IsomericSMILES`, `SMILES`, `ConnectivitySMILES`,
`CanonicalSMILES` — older code that only asks for `CanonicalSMILES` returns empty.

**FUSE write note:** AutoDock receptor/ligand prep and any random-access binary
writes fail on `/mnt/results/` and `/mnt/shared-workspace/`. Do prep in
`/workspace/`, then copy finished files to `/mnt/results/`. R's `file.copy()`
produces 0-byte files on `/mnt/results/` — use a shell `cp` instead.

---

## External Data Sources & Licenses

This skill retrieves data from public scientific databases. Record and honor each
source's license — some are public-domain/CC0 (no obligations), but others are
**CC BY-SA**, which permits commercial use **only if** you provide attribution
**and** share any derived/redistributed data under the same license (share-alike).
See `DATA_SOURCES.md` for the full detail; the summary table is below.

| Data source | Used in skill for | License | Commercial use | Obligations |
|---|---|---|---|---|
| **ChEMBL** (EMBL-EBI) | Target confirmation (ChEMBL IDs), bioactivity, SMILES | **CC BY-SA 3.0** | **Yes** | **Attribution + share-alike required.** Credit ChEMBL/EMBL-EBI and license any redistributed or derived dataset under CC BY-SA 3.0. |
| **Human Protein Atlas** (HPA) | Normal-tissue expression / selectivity context (when tissue-selectivity or off-tissue liability is assessed) | **Current: CC BY 4.0**; **archived/versioned data (older releases): CC BY-SA 3.0** | **Yes** | Credit the Human Protein Atlas (proteinatlas.org) + a primary HPA publication. **Attribution required; share-alike applies if you use CC BY-SA-era (archived/versioned) HPA data** — verify the license of the exact HPA release you pull, since it changed from CC BY-SA 3.0 to CC BY 4.0. |
| **PubChem** (NCBI) | Warhead / E3-ligand / reference-degrader SMILES | Public domain (U.S. Gov / NCBI; works are not copyrighted) | Yes | No license restriction; attribution appreciated as good practice. |
| **RCSB PDB** | Target 3D structures for docking | Public domain / **CC0 1.0** | Yes | No restriction; cite the specific PDB ID(s) and primary depositors as good practice. |
| **UniProt** | Target identity / accession cross-reference | **CC BY 4.0** | Yes | Attribution required (no share-alike). |
| **Literature metadata** (LiteratureSearch / DOIs) | Precedent degraders (E3, warhead, exit vector, DC50/Dmax) | Per-publisher (metadata generally reusable) | Varies | Cite each work by DOI; do not redistribute full-text beyond fair use. |

> **Attribution + share-alike (CC BY-SA) — explicit note (mandatory).**
> **ChEMBL is CC BY-SA 3.0** and the **Human Protein Atlas is CC BY-SA** for its
> **archived/versioned releases** (its **current** release is **CC BY 4.0**). Both
> resources **permit commercial use**. For any **CC BY-SA** data you must (1)
> **attribute** the source, and (2) **share-alike** — any dataset you derive from or
> redistribute that incorporates CC BY-SA data must itself be licensed under the
> same CC BY-SA terms. This is "viral" for a redistributed dataset, so if you export
> a table that embeds ChEMBL (or CC BY-SA-era HPA) values, carry the CC BY-SA
> license and attribution with it. For **CC BY 4.0** data (current HPA, UniProt),
> attribution is required but there is **no** share-alike obligation — always verify
> the license of the exact HPA release you pull, since HPA changed from CC BY-SA 3.0
> to CC BY 4.0. Public-domain/CC0 sources (PubChem, RCSB PDB) carry no obligation.
> **ChEMBL caveat:** some ChEMBL fields are compound-property calculations derived
> from commercial software and carry additional restrictions (e.g., do not extract
> them in isolation to replicate the commercial process) — this skill uses ChEMBL
> only for target IDs / SMILES / bioactivity, not those restricted calculations.
> This is a **data-license** matter, separate from the **software-dependency**
> licenses (RDKit BSD-3-Clause, AutoDock Vina Apache 2.0, OpenBabel GPL-2.0,
> ADMET-AI/Chemprop, ReportLab BSD) and from the ADFR Suite's non-commercial
> restriction documented above.

---

## Core Workflow

### 1. Target & precedent dossier
Confirm the target ID in ChEMBL. Search the literature for existing degraders of
the target using the **Biomni `LiteratureSearch` tool** (not generic web search):
capture the **E3 used, warhead chemotype, exit vector, and any reported
DC50/Dmax** for each precedent. These facts drive warhead/E3/exit-vector choices
downstream. Record every precedent with a citation (DOI).

> **LiteratureSearch visibility guardrail — mandatory.** The `LiteratureSearch`
> tool call MUST be visible in the execution-trace notebook (a real tool call,
> not a docstring mention or a PDF-generation comment). Do **not** claim in the
> PDF or any artifact that "a literature search via the Biomni LiteratureSearch
> tool" was performed unless the tool call is evidenced in the trace. If
> LiteratureSearch is unavailable, state that explicitly and fall back to
> model-knowledge precedents with a clear "not tool-verified" label — never
> fabricate the tool call.
>
> **No hardcoded reference data — mandatory.** Do **not** define the reference
> degraders (or any precedent table) as a Python dict literal in a code cell,
> even with a comment claiming it was "compiled from LiteratureSearch results."
> Instead: (1) call the `LiteratureSearch` tool, (2) read the returned records
> from `/mnt/results/execution_trace/references.jsonl`, and (3) parse those
> records into `tables/reference_degraders.csv`. The `references.jsonl` read
> must appear in the execution trace. If you must use model-knowledge precedents
> as a fallback (e.g. LiteratureSearch returns no results), label **every** entry
> with `"not tool-verified"` in the citation/evidence column — do not claim tool
> use that did not occur.

> **DOI consistency guardrail — mandatory.** Every DOI recorded in
> `tables/reference_degraders.csv` (or any table) MUST match the DOI for the same
> reference in the PDF References section. Before writing the report, cross-check
> each `[N]` citation's DOI across all artifacts (CSV tables, PDF references,
> figure captions). A mismatch (e.g. a CSV DOI pointing to a different journal
> than the PDF cites for the same degrader) is a grounding error — fix it at the
> source so all artifacts agree.

### 2. Select & validate warheads
Choose warheads spanning the relevant mechanisms/chemotypes for the target
(e.g., allosteric vs ATP-site for a kinase). Fetch each from PubChem, validate in
RDKit, and mark the intended **linker attachment atom** with a dummy `[*]`. Place
attachment points at solvent-exposed peripheries so target binding is preserved —
justify each exit vector from docking (step 3) or published SAR (state which).

> **Warhead-uniqueness guardrail — mandatory, run before assembly.** After
> defining each warhead's attachment SMILES (with the `[*]` dummy in place),
> canonicalize every attachment SMILES with RDKit
> (`Chem.MolToSmiles(Chem.MolFromSmiles(attach_smi))`) and compare all pairs for
> uniqueness. **No two warheads may produce the same canonical attachment
> SMILES** — if they do, the assembled PROTACs will be identical molecules
> scored differently, inflating the library and corrupting the ranking. This
> happens when two inhibitors share a core and differ only in a pendant group
> that is the leaving group replaced by the linker (e.g. JQ1 and OTX015 share a
> triazolo-diazepine BET core; their distinct pendant chloroacetamide vs.
> acrylamide groups are both the attachment-leaving group, so the assembled
> PROTACs are identical). If a collision is found, either (a) choose a genuinely
> different attachment point on one warhead that yields a distinct canonical
> SMILES, or (b) drop the duplicate warhead and document why. Report the
> canonical-SMILES uniqueness check result (N unique / N warheads) alongside the
> step-5 QC counts.

### 3. Dock the warhead → exit vector
Pick a target structure from RCSB (right domain, good resolution, ligand-bound if
possible). Prepare the receptor in `/workspace/` (strip waters/ions/cryoprotectants:
HOH, SO4, GOL, EDO, Cl, Na, Mg, PO4, ACT; add polar H). Dock the warhead with
**AutoDock Vina** into a box centered on the co-crystal ligand
(grid 0.375 Å; `--exhaustiveness 16 --num_modes 9 --seed 42`). **Validate the
pose** against the crystallographic ligand centroid (report distance in Å), then
identify the exit vector by counting receptor heavy atoms within 5.0 Å of each
warhead atom via a KD-tree (low count = solvent-exposed = good conjugation point).

> Only warheads you actually dock have a computed exit vector. For warheads whose
> vector you assign from literature SAR, say so explicitly.

### 4. Select E3 ligands + build linker library
Pick E3 recruiters to compare (typically **CRBN** — pomalidomide/lenalidomide —
and **VHL** — VH032), each with a defined attachment atom. Build a linker library
spanning **PEG** (PEG2–PEG5), **alkyl** (C4/C6/C8), and **rigid/semi-rigid**
motifs (piperazine dicarboxamide, triazole, piperazine-PEG). Encode linkers with
two dummies `[*]...[*]`.

> **Junction-chemistry guardrail — read before defining any linker SMILES.**
> Linker attachment-point atoms must **not duplicate the heteroatom already
> present in the warhead or E3 attachment SMILES**, or the joined product will
> contain a chemically invalid/instable bond. Known failure modes:
> - **Perester** `C(=O)O-O` — when a PEG linker starts with `O` (`[*:1]OCCO[*:2]`)
>   is joined to a warhead ester attachment `CC(=O)O[*:1]`.
> - **Hydroxylamine** `O-N` — when a PEG linker ends with `O` is joined to an E3
>   glutarimide-N attachment `N([*:2])`.
> - **Peroxide** `O-O` / **hydrazine** `N-N` — analogous duplications at either
>   junction.
> - **Anhydride** `C(=O)O-C(=O)` — when a dicarboxamide linker retains both acyl
>   oxygens and is joined to an ester warhead attachment.
>
> **Fix at definition time:** place **carbon** (not O/N) at linker attachment
> points so the heteroatom sits in the linker interior. For PEG linkers use
> `[*:1]CCOCC[*:2]` (C at ends, O in middle) instead of `[*:1]OCCO[*:2]`; extend
> the same pattern for PEG3–PEG5, Triazole-PEG, and Piperazine-PEG. For
> piperazine-dicarboxamide, use amide `C(=O)N` junctions, not retained acyl-O.
>
> **Mandatory junction validation (step 5 QC):** after assembly, scan every
> product SMILES for perester `C(=O)O-O`, peroxide `O-O`, hydroxylamine `O-N`,
> and hydrazine `N-N` bonds at the warhead-linker and linker-E3 junctions. Flag
> and exclude any candidate that contains one. Report N chemically-valid /
> N total alongside the existing RDKit-validity and core-intact counts.

### 5. Combinatorially assemble in RDKit
Assemble warhead × linker × E3. Use **atom-map-labeled dummies** to avoid the
common multi-dummy join bug: label warhead attach `[*:1]`, linker `[*:1]` and
`[*:2]`, E3 `[*:2]`; bond matched labels via their heavy-atom neighbors, remove
dummies, clear map numbers, sanitize. **QC every product**: valid molecule AND
both pharmacophore cores intact (SMARTS substructure match of warhead scaffold
and E3 scaffold) AND **no invalid junction bonds** (perester `C(=O)O-O`,
peroxide `O-O`, hydroxylamine `O-N`, hydrazine `N-N` — see the step-4
junction-chemistry guardrail). Report N valid / N total, N both-cores / N total,
and N chemically-valid-junctions / N total.

### 6. Physicochemical + ADMET profiling
Compute RDKit descriptors (MW, cLogP, TPSA, HBD, HBA, RotB, aromatic rings,
Fsp3, heavy atoms). Predict 16 ADMET endpoints for all candidates with
**DeepPurpose** (`from deeppurpose import ...`; `predict_admet_properties` is
not a `biomni.tool` function — use the DeepPurpose package directly).

> **ADMET honesty:** DeepPurpose models are trained on drug-like chemical space.
> Degraders are bRo5, so these predictions are **extrapolations** — use them as
> relative rankings, not absolute values. State this wherever ADMET is reported.

> **ADMET failure-log accuracy guardrail.** When recording which candidates
> failed ADMET prediction (NaN / encoding failure), describe the failure pattern
> by **canonical SMILES**, not by warhead label. If two warhead labels map to the
> same canonical SMILES (see the step-2 warhead-uniqueness guardrail), a failure
> attributed to one label actually affects both. Log the unique failing SMILES
> and the set of candidate labels that share each, so the failure count is not
> double-counted or mis-attributed to a single warhead.

### 7. Prioritize (transparent score) + shortlist
Rank with an explicit, weighted, documented score (0–100). A defensible default,
combining precedent-based and prior-free components:

```python
W = {"c1_warhead":0.22, "c2_e3ligand":0.15, "c3_exitvector":0.15,
     "c4_bro5":0.22, "c5_admet":0.16, "c6_similarity":0.10}
# Degradability_Assessability = sum(df[c]*W[c] for c in W) * 100
```
- c1 warhead precedent, c2 E3-ligand suitability — **encode known priors** (assign
  best-in-class chemotypes high values *by design*).
- c3 exit-vector quality (from docking), c4 bRo5 envelope conformity, c5 ADMET
  desirability, c6 similarity to validated degraders — the **prior-free** part.

Anchor the bRo5 envelope on real oral degraders (e.g. ARV-471, ARV-110):
`MW 700–1100, cLogP 2–6, TPSA 90–180, HBD 0–4, RotB 6–18`. Build a balanced
shortlist (e.g. top-3 CRBN + top-3 VHL) and **flag liabilities explicitly**
(TPSA/HBD over the oral envelope, high RotB, etc.).

> **Critical honesty boundary — read before writing any result.** This score is a
> **prioritization heuristic, NOT a calibrated DC50/Dmax predictor.** Because c1/c2
> deliberately encode literature priors, a top ranking that matches a known
> best-in-class degrader reflects that prior **by construction** — it is an
> internal-consistency check, not an independent rediscovery. Never claim
> otherwise. Docking (step 3) models the **warhead only**, not the productive
> ternary E3–degrader–target complex that actually determines degradation.

### 8. Figures + report
Generate the seven figures (above) as PNG+SVG (`font.family=['Liberation Sans',
'DejaVu Sans']`, `svg.fonttype='none'`; run a media-output-check on each). Build
the PDF **with the `pdf-report-generation` skill** (load it via `Skill` and follow
its template — do not build the PDF by calling `reportlab` directly). The report
MUST include an **Introduction** section (use that exact section name, not
"Executive Summary" or a synonym), a Methods "honesty boundary" callout, and a
Limitations section covering: heuristic score, embedded priors, ADMET
extrapolation, warhead-only docking, literature-based exit vectors, simplified
conjugation chemistry, and real permeability liabilities.

> **Figure-format guardrail — mandatory.** Every figure MUST be saved in **both
> PNG and SVG**. Use `matplotlib` `savefig()` for all figures (including
> building-block montages and shortlist structure grids) — call `savefig()` twice
> with `.png` and `.svg` extensions. Do **not** use `PIL` `Image.save()` for any
> figure: it cannot emit SVG, so figures produced with PIL will be missing the
> required SVG version. If a figure is assembled from multiple images (e.g. a
> structure grid), lay it out on a `matplotlib` `Figure` with `imshow`/`AxesImage`
> and save that figure to both formats. After saving, verify each figure has both
> a `.png` and a `.svg` file before proceeding to the report.

> **Report-engine guardrail — mandatory.** The PDF MUST be built by loading the
> `pdf-report-generation` skill via `Skill(action="load", name="pdf-report-generation")`
> and following its template. Importing `reportlab` directly
> (`SimpleDocTemplate`, `Paragraph`, etc.) and building the PDF programmatically
> is a skill-instruction deviation even if the output is branded and complete —
> do not do it. The `pdf-report-generation` skill ensures consistent branding,
> section structure, and reference formatting.
>
> **Pre-flight check (run before any reportlab import):** verify that
> `Skill(action="load", name="pdf-report-generation")` has been called in this
> session. If it has not, call it now. Do **not** proceed to any `reportlab`
> import or PDF-building code until the skill is loaded. A markdown cell that
> *mentions* the skill is not sufficient — the `Skill()` call must appear as a
> real tool call in the execution trace before the code cell that builds the PDF.
>
> **Post-generation self-check:** after the PDF is written, verify in the
> execution trace that a `Skill()` call for `pdf-report-generation` appears
> **before** any `reportlab` import. If the trace shows a `reportlab` import with
> no preceding `Skill()` load, the guardrail was violated — stop and rebuild the
> PDF via the skill before finishing.

> **References section — mandatory.** The PDF MUST include a **References**
> section listing every cited `[N]` reference with authors, title, journal,
> year, and DOI (or URL if no DOI). Do not cite any `[N]` in the report body
> without defining it in the References section. The `pdf-report-generation`
> skill template already provides a References section — populate it with the
> full citations captured in step 1.

> **NaN-safe text guardrail:** when passing DataFrame values to `Paragraph()`
> (or any reportlab text flowable), coerce nullable/NaN values to safe strings
> first (e.g. `str(v) if pd.notna(v) else "N/A"`). Raw `float('nan')` / `None`
> in `Paragraph()` text raises or renders literal `nan`, corrupting the report.

---

## Advanced tier: ternary-complex structural modeling (optional)

Steps 1–8 model the **warhead** only. The productive **ternary complex**
(E3–degrader–target) is what actually determines degradation, and modeling it is
a harder, fast-moving problem. Add this tier when the user wants a credible
ternary pose or physics-based potency ranking. Full detail and citations are in
`references/ternary_modeling.md`; the essentials:

1. **Structural engine (ternary pose):** use a cofolding model. In-platform:
   **Boltz-2** or **Chai-1** (both handle protein + small-molecule complexes).
   AlphaFold3 is often cited as best for glue ternaries but is **not in this
   platform** (only AlphaFold v2 is), and the "best engine" is genuinely
   **benchmark-dependent and unsettled** — some 2025 benchmarks rank AF3 first,
   a 2026 head-to-head found Boltz-2 beat AF3 on 40 PROTAC/glue complexes. Try
   more than one engine and inspect poses; do not assume one winner.
2. **Modality routing:** small-molecule glue/PROTAC → cofold (Boltz-2/Chai-1);
   **peptide or protein binder → BoltzGen** (universal binder design vs proteins/
   peptides/small molecules/nucleic acids).
3. **Ranking / optimization:** cofolding *scores* rank potency poorly; use
   **physics-based free energy** for ranking analogs. Published degrader-specific
   approaches include ensemble/FEP-style ternary methods (e.g. GlueMap;
   cooperativity-via-binding-free-energy). FEP substantially outperforms
   cofolding scores for ternary ranking in reported benchmarks.
4. **Experimental / un-verified models:** named glue-enhancement tools sometimes
   cited (e.g. steered/guided-diffusion variants) are **not validated in this
   environment** — treat them as candidates to evaluate individually, not as
   dependencies.

> **PROTAC vs glue caveat:** a molecular-glue ternary has a small, cooperative
> interface; a **PROTAC** ternary is dominated by linker conformational sampling
> and is the **harder** case (benchmarks flag degrader complexes and large
> interfaces as failure modes). If you built a PROTAC library in steps 1–8, the
> ternary step needs PROTAC-aware conformational sampling and explicit linker
> treatment — a glue-only pipeline will under-serve it.

---

## Scientific caveats (always surface these)

- The prioritization score is a **heuristic**, not a DC50/Dmax model.
- It **embeds literature priors** (c1/c2) — top hits partly reflect precedent by
  construction.
- **ADMET is extrapolated** to bRo5 space — relative rankings only.
- **Docking models the warhead only** unless the ternary tier is run.
- Assembly uses **simplified direct-bond conjugation**; confirm synthetic routes.
- Common real liability for polar warheads: **TPSA and HBD above the oral
  envelope** → oral-permeability risk to resolve experimentally.
- All designs are prioritizations for **wet-lab validation**, not conclusions.
- **Commercial-use caveat:** the ADFR Suite (`prepare_receptor`/`prepare_ligand`)
  is **PROHIBITED for commercial use** per the MGLTOOLS SOFTWARE LICENSE
  AGREEMENT (non-commercial only; "revenues generating activities" including
  consulting and selling applications built on top of this software are
  commercial usage). It is free for academic/non-commercial use only. A
  commercial license from Scripps Research / Michel Sanner is required for any
  commercial deployment, or substitute an open-source PDBQT-prep alternative
  (Meeko, OpenBabel, or RDKit-based prep). Other software dependencies (RDKit
  BSD-3-Clause, AutoDock Vina Apache 2.0, DeepPurpose BSD-3-Clause, OpenBabel
  GPL-2.0, ADMET-AI/Chemprop, ReportLab BSD) permit commercial use under their
  respective licenses.
- **Data-source licenses (separate from software) — see the "External Data
  Sources & Licenses" section and `DATA_SOURCES.md`.** PubChem and RCSB PDB are
  public domain / CC0 (no obligations); UniProt is CC BY 4.0 (attribution only).
  **ChEMBL (CC BY-SA 3.0)** and the **Human Protein Atlas (CC BY-SA)** permit
  commercial use **but require attribution + share-alike**: credit the source and
  license any derived/redistributed dataset that embeds their data under the same
  CC BY-SA terms.

## Final response (mandatory)

Your final chat response MUST be non-empty. Do not end the run after writing the
PDF and artifacts without producing a final text response. Summarize:
1. **Skill used** — name the skill and confirm it was loaded.
2. **Inputs** — target, E3 ligase(s), warhead/linker choices, any user constraints.
3. **Workflow steps** — the steps executed (target/precedent, warhead selection,
   docking, assembly, ADMET, scoring, figures, report).
4. **Output artifacts** — list the key files written (tables, figures, PDF) with
   filenames.
5. **Material dependencies and commercial-use status** — name the engines used
   (RDKit, AutoDock Vina, DeepPurpose, OpenBabel/Meeko) and confirm ADFR Suite
   was avoided; flag any `needs_commercial_review` dependency.
6. **Failures or limitations** — any step that did not complete, any guardrail
   that required a fallback, and the honesty-boundary caveats (heuristic score,
   ADMET extrapolation, warhead-only docking).

## Files in this skill

- `references/ternary_modeling.md` — the advanced-tier landscape (engines,
  benchmarks, ranking methods) with verified citations and DOIs.
