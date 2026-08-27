---
name: sgrna-design
description: >
  Find or design sgRNAs (guide RNAs) for CRISPR experiments using a three-tiered approach:
  (1) validated sequences from a bundled Addgene database + literature search, (2) precomputed
  CRISPick designs from the Broad Institute GPP, (3) de-novo design rules. Use whenever someone
  asks for a guide RNA / sgRNA / CRISPR guide to knock out (KO), activate (CRISPRa), or inhibit
  (CRISPRi) a gene, mentions SpCas9 / SaCas9 / AsCas12a / enAsCas12a, asks "what guide should I
  use for <gene>", or wants CRISPR targeting sequences for a screen — even if they don't say
  "design". Covers human, mouse, rat, and other model organisms. Does NOT run wet-lab validation
  or genome-wide off-target alignment.
license: CC BY 4.0
---

# sgRNA Design: Three-Tiered Approach

Find or design sgRNAs by **prioritizing validated sequences before computational predictions**.
Always start at Option 1 and only descend to the next tier when the current one yields nothing
usable. Ported from the Biomni `sgRNA_design_guide.md` (snap-stanford/Biomni), with the data
parsing corrected and the literature step wired to this environment's search tools.

## When to use
- "Give me an sgRNA to knock out TP53 in human cells"
- "Design CRISPR guides to activate OCT4" / "CRISPRi guides for MYC"
- "What guide RNA should I use for <gene> with SpCas9 / SaCas9 / Cas12a?"
- Selecting guides for an arrayed or pooled CRISPR screen

## Inputs
- **Gene symbol** (required), e.g. TP53, BRCA1, AAVS1.
- **Organism** (default human), e.g. human/mouse/rat or NCBI TAXID.
- **Application** (default knockout): knockout / activation (CRISPRa) / inhibition (CRISPRi).
- **Cas enzyme** (default SpCas9): SpCas9, SaCas9, AsCas12a, enAsCas12a.

## Outputs
- `<GENE>_selected_sgRNAs.csv` — unified table of 3–4 recommended guides (sequence, source,
  rank/score, exon/position, PAM, citation/dataset, notes).
- `<GENE>_sgRNA_summary.md` — which tier was used and why, the picks, and caveats.

Save both to the user's results directory.

## Bundled resources (work offline; refresh via `references/refresh_resources.md`)
- `references/resource/addgene_grna_sequences.csv` — 321 validated sgRNAs, 197 genes (Addgene).
- `references/resource/CRISPick_download_links.txt` — 238 CRISPick dataset URLs, 13 organisms.

## Scripts
| Script | Purpose |
|--------|---------|
| `scripts/search_addgene.py` | Tier 1 / Method 1 — search the Addgene database (handles the HTML-wrapped IDs and messy species values). |
| `scripts/find_crispick_dataset.py` | Tier 2 / Step 1 — resolve the correct CRISPick download URL for organism+enzyme+application. |
| `scripts/select_crispick_sgrnas.py` | Tier 2 / Step 3 — filter a downloaded CRISPick file to your gene, rank, pick 3–4. |
| `scripts/check_design_rules.py` | Tier 3 — sanity-check a candidate guide (length, GC, TTTT, PAM). |
| `scripts/export_results.py` | Write the unified CSV + markdown summary. |

---

## Option 1 — Validated sequences (ALWAYS try first)

> You MUST complete **both** Method 1 and Method 2 before considering Option 2. Do not skip
> Method 2 even if Method 1 finds nothing — many validated guides live only in the literature.

### Method 1 — Bundled Addgene database
```python
import sys; sys.path.insert(0, "scripts")
from search_addgene import search_addgene

hits = search_addgene("TP53", species="human", application="knockout")
print(len(hits))   # 0 for TP53 -> still do Method 2 before Option 2
```
Each hit carries a clean `Target Sequence`, `pubmed_id`/`pubmed_url`, `plasmid_id`/`plasmid_url`,
and `Depositor`. **Cite the PubMed ID** of the original publication in your methods.

`application` accepts intent words and maps them to Addgene's vocabulary:
knockout→`cut`, activation→`activate`, inhibition/CRISPRi→`interfere`/`RNA targeting`.

### Method 2 — Literature & web search (REQUIRED)
This environment does not expose `advanced_web_search_claude`; use the available tools instead.
Run **both** for coverage:
- `LiteratureSearch` — peer-reviewed papers (validated guides, supplements).
- `WebSearch` — vendor/database hits (GenScript, Horizon, lab protocols).

Query templates (substitute the gene):
```
"sgRNA" OR "guide RNA" "<GENE>" validated experimental
"CRISPR knockout" "<GENE>" sgRNA sequence validated
"<GENE>" sgRNA "cutting efficiency" OR "on-target"
```
Scan ≥10–15 results and check supplementary materials. For any validated guide, record the
sequence, citation (PMID/DOI), and validation details (cell line, cutting efficiency).

**Decision:** if either method yields usable validated guides, select 3–4 and export. Only if
**both** come up empty, go to Option 2.

---

## Option 2 — CRISPick precomputed designs

Use when no validated guides exist, you need genome-wide coverage, or you want ranked options.

### Step 1 — Resolve the dataset URL
```python
from find_crispick_dataset import find_crispick_dataset
info = find_crispick_dataset("human", cas="SpCas9", application="knockout")
print(info["matches"])   # GRCh38 + GRCh37 dataset URLs
print(info["warning"])   # Cas12a variant warning, if applicable
```
> **AsCas12a vs enAsCas12a are different enzymes.** Guides for one may not work with the other.
> The finder matches the exact enzyme token so datasets never cross-contaminate.

### Step 2 — Download & extract (files are 50–700 MB; not bundled)
```bash
wget '<URL from Step 1>'
gunzip sgRNA_design_*.txt.gz
```

### Step 3 — Filter, rank, select
```python
from select_crispick_sgrnas import select_crispick_sgrnas
picks = select_crispick_sgrnas("sgRNA_design_..._CRISPRko_....txt", "TP53", n=4)
```
Ranks by **Combined Rank** (lower = better) by default; `rank_by="on_target"` or
`"off_target"` to prioritize efficiency or specificity. Spreads picks across distinct exons for
redundancy. Optional filters: `exon=`, `cut_position_range=`, `max_target_cut_pct=` (knockout).
Column names are resolved defensively (handles both real CRISPick and abbreviated layouts);
see `references/crispick_file_format.md`. If the gene is absent → Option 3.

---

## Option 3 — De-novo design (last resort)

For genes/organisms not covered above. Follow `references/design_rules.md`:
- Length: 20 bp (SpCas9/SaCas9), 23–25 bp (Cas12a). PAM: SpCas9 NGG, SaCas9 NNGRRT, Cas12a TTTV (5').
- GC 40–60%; avoid TTTT and homopolymer runs >4 nt.
- KO → early exons (first ~50%); CRISPRa → −200 to +1 of TSS; CRISPRi → −50 to +300 of TSS.
```python
from check_design_rules import check_design_rules, format_report
print(format_report(check_design_rules("GAGGTTGTGAGGCGCTGCCC", "SpCas9", pam="AGG")))
```
This checks rules only — for real off-target assessment use Cas-OFFinder/CRISPOR or CRISPick ranks.

---

## Export (all tiers)
```python
from export_results import from_addgene, from_crispick, export
unified = from_addgene(hits, application="knockout", enzyme="SpCas9")  # or from_crispick(...)
export(unified, gene="TP53", tier="Option 1 (validated Addgene)",
       outdir="/path/to/results", rationale="Validated guides found via Method 1.")
```

## Universal best practice
**Test 3–4 sgRNAs per gene experimentally regardless of predicted scores**, and validate edits
(Sanger sequencing; TIDE/T7E1 for indels). Prediction scores guide selection but do not replace
empirical validation.

## Citations & acknowledgments (preserve in user methods)
- **Validated guides (Option 1):** Addgene (https://www.addgene.org). Cite the PubMed ID of each
  guide's original publication. Acknowledge: "Validated sgRNA sequences obtained from Addgene."
- **CRISPick (Option 2):** "Guide designs provided by the CRISPick web tool of the GPP at the
  Broad Institute."
  - Cas9 (SpCas9, SaCas9): Sanson KR, et al. *Nat Commun.* 2018;9(1):5416. PMID: 30575746.
  - Cas12a (AsCas12a, enAsCas12a): DeWeirdt PC, et al. *Nat Biotechnol.* 2021;39(1):94–104.
    PMID: 32661438. (Specify which Cas12a variant you used.)

## Scientific caveats
- Bundled Addgene/CRISPick files are a fixed snapshot (197 genes / 238 datasets); literature
  search (Method 2) is mandatory precisely because the snapshot is incomplete.
- Genome build matters: human defaults GRCh38 (GRCh37 also available), mouse GRCm38 — match
  coordinates to your reference.
- The skill does not perform genome-wide off-target alignment beyond CRISPick's precomputed ranks.
