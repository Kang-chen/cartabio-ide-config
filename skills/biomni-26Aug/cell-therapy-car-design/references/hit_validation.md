# Hit validation: is a screen hit a real, context-specific target?

A MAGeCK "hit" is a statistical nomination, not a validated target. Before you
put a gene forward as a CAR-T engineering candidate (e.g., a knockout to enhance
persistence/proliferation), sanity-check it. This skill's default cross-check is
**DepMap broad-essentiality**, but you should offer the user alternatives.

> **Decision point (ask the user):** "Which reference should I cross-check hits
> against? Default = DepMap (broad cancer-cell-line essentiality, to flag genes
> that just deplete everywhere). Alternatives: a lineage-restricted DepMap subset,
> a published primary-T-cell fitness screen, pathway/ontology enrichment, or skip."
> Proceed with DepMap only if they don't specify.

---

## 1. DepMap broad-essentiality cross-check (default)

**Question it answers:** "Is this gene essential in *almost any* proliferating
cell (so its screen signal may be generic), or is the dependency
*context-specific* (more interesting for a T-cell phenotype)?"

**Data (data lake):**
`/mnt/datalake/depmap/crispr_screen/CRISPRGeneEffect.csv`
- Chronos gene-effect matrix. **Rows = cell-line models** (`ModelID`, `ACH-######`;
  the first column header is empty / auto-named `Unnamed: 0`). **Columns = genes**
  named `SYMBOL (ENTREZ)`, e.g. `CD3D (915)`. ~1,186 lines x ~18,435 genes.
- Interpretation: gene-effect ~0 = no effect; **more negative = more depleting /
  more essential**. Threshold commonly used for "dependent line": **< -0.5**.
- Related files in the same folder: `CRISPRGeneDependency.csv` (probabilities),
  `ScreenGeneEffect.csv`. Use `CRISPRGeneEffect.csv` unless you need probabilities.

**Memory safety (critical):** the matrix is ~430 MB. **Never load it whole.**
Read only the queried gene columns via `pandas.read_csv(..., usecols=<callable>)`.
Note the empty first-column header becomes `Unnamed: 0`; the helper simply reads
gene columns and aggregates column-wise (ModelID index not needed for the stats).

**Helper:** `scripts/depmap_crosscheck.py`
```bash
python scripts/depmap_crosscheck.py \
  --gene-summary <mageck>.gene_summary.txt --top 15 \
  --out .../tables/depmap_crosscheck.csv
# or explicit genes:
python scripts/depmap_crosscheck.py --genes CD3D CBLB CD5 PTEN --out ...
# alternative reference matrix (same orientation rows=models, cols='SYMBOL ...'):
python scripts/depmap_crosscheck.py --genes ... --matrix /path/other_matrix.csv --out ...
```
Outputs per gene: `depmap_mean_gene_effect`, `depmap_median_gene_effect`,
`frac_lines_dependent(<-0.5)`, `n_lines`, `pan_essential_flag` (True if dependent
in >=90% of lines), and a plain-language `interpretation`.

**Worked example (this skill's screen, verified against DepMap):**
| gene | mean gene-effect | frac lines dependent | pan-essential? | reading |
|---|---|---|---|---|
| RPL3 (control) | -2.65 | 1.00 | **yes** | broadly essential (as expected for a ribosomal gene) |
| CD3D (#1 essential hit) | -0.13 | 0.011 | no | **T-cell-specific** — depletes in the screen via TCR-complex biology, not generic essentiality |
| CD5 | -0.15 | 0.019 | no | context-specific |
| CBLB (#1 brake) | 0.00 | 0.00 | no | not essential; consistent with a regulatory (brake) role |
| PTEN (#3 brake) | +0.41 | 0.012 | no | not essential |

The RPL3 positive control confirms the essentiality logic; CD3D being *not*
pan-essential is the key insight — it shows the screen is capturing T-cell
biology rather than housekeeping fitness.

### CRITICAL caveat (must appear in the report)
DepMap lines are **cancer cell lines, not primary T cells**. Use this cross-check
**only to flag broad essentiality**, never to *confirm* T-cell-specific biology.
A gene that is not pan-essential in DepMap is a *more plausible* context-specific
target; it is not thereby validated. Genes silent in cancer lines (e.g., many
immune-receptor genes) can look "not essential" simply because they are not
expressed there.

---

## 2. Alternative / complementary cross-checks (offer when relevant)

- **Lineage-restricted DepMap:** subset `CRISPRGeneEffect.csv` rows to blood/lymphoid
  models via `/mnt/datalake/depmap/.../model*` metadata before aggregating. Closer to
  T-cell context than pan-cancer, still not primary cells.
- **Published primary-T-cell fitness/CRISPR screens:** the most relevant comparator.
  Use `LiteratureSearch` to find them (e.g., "primary human T cell CRISPR proliferation
  screen"), then compare hit direction/rank qualitatively.
- **Pathway / ontology context:** if `gseapy`/`clusterProfiler` is available, run
  enrichment on the ranked gene list (ORA on hits or GSEA on the full ranking) to see
  whether hits converge on coherent pathways (e.g., TCR signaling, PI3K, cytokine
  signaling). `gseapy` may need `uv pip install gseapy`; MSigDB gene sets are in the
  data lake. This is corroboration, not validation.
- **Known-biology check via `LiteratureSearch`:** confirm each top hit's established
  role in T-cell function and cite it. E.g., CBLB and PTEN are well-known negative
  regulators of T-cell activation; CD3D is a TCR/CD3 complex subunit. Ground these in
  retrieved papers rather than asserting from memory.

---

## 3. Reporting the validation
- Add the DepMap cross-check table to the PDF Results section.
- State the direction convention explicitly (negative gene-effect = essential).
- Always include the "cancer lines != primary T cells" caveat in the report's
  Limitations section.
- In the infographic, you may annotate whether the top essential hit is
  pan-essential or context-specific (a one-word tag), since that is the headline
  interpretation.
