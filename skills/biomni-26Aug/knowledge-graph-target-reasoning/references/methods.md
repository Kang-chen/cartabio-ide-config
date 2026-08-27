# Methods & rationale — knowledge-graph target reasoning

This file backs the **Methods** and **Limitations** sections of the PDF report.
Adapt wording to the disease at hand; do not present the illustrative IBD numbers
below as results for another disease.

## 1. Data sources

- **PrimeKG** — a precision-medicine knowledge graph. In the reference environment
  it has ~**8.1 million** typed edges (8,100,498), **10 node types**
  (gene/protein, disease, drug, pathway, biological process, molecular function,
  cellular component, anatomy, phenotype, exposure) and **29 relation types**.
  Node ids used as anchors are PrimeKG's internal `x_id`/`y_id` for `disease` nodes.
- **TxGNN** (optional layer) — precomputed graph-neural-network **drug→disease**
  repurposing scores. In the reference environment: **17,080 diseases × 7,957
  drugs**. Crucially, TxGNN scores drug→disease pairs, **not** target scores, so
  it is used only as an indirect drug-target evidence layer (drugs → their protein
  targets), weighted below the network signal.

## 2. Gene–gene network

Two edge families over the gene/protein universe:
1. **Protein–protein interactions** (`protein_protein`), weight **1.0**.
2. **Shared-concept edges**: genes co-annotated to the same `pathway_protein`,
   `bioprocess_protein`, or `molfunc_protein` concept are connected, with weight
   **0.5 / (n − 1)** for a concept shared by *n* genes. Concepts with **n < 2** or
   **n > 200** (`MAX_CONCEPT`) are dropped — very large concepts (e.g. "signal
   transduction") are too generic and would create dense, uninformative hubs.

The adjacency is symmetric and sparse (reference IBD run: 21,234 genes,
6,569,902 non-zeros).

## 3. Seeds

Seeds are genes with a direct `disease_protein` edge to any anchor disease node.
A gene's **initial mass = the number of anchors it links to** (rewarding genes
shared across subtypes), then normalized to sum to 1. If an anchor has too few
seeds the propagation is uninformative — check seed counts with
`find_disease_anchors.py` first.

*Illustrative (IBD reference run):* IBD 100 / CD 50 / UC 63 seeds → 213
disease→gene links, 100 unique seed genes.

## 4. Random Walk with Restart (RWR)

`p_{t+1} = (1 − r)·W·p_t + r·p_0`, where `W` is the column-normalized (degree-
scaled) transition matrix, `p_0` the normalized seed vector, and **r = 0.30** the
restart probability. Iterated to L1 convergence (tol 1e-10). Higher `r` keeps mass
closer to the seeds (more conservative); lower `r` explores farther. *IBD run:
converged in 31 iterations.*

## 5. TxGNN drug-target layer (optional)

For each anchor, take the **top 50** TxGNN-predicted drugs, map each to its protein
targets via `drug_protein`, and accumulate the sum of those drugs' TxGNN scores per
gene → a per-gene drug-target support score. Skipped when no TxGNN key is supplied
(ranking is then RWR-only).

## 6. Combined score

`combined = 0.70 · rank_norm(RWR) + 0.30 · rank_norm(TxGNN_support)`.

**Tie-aware `rank_norm` is essential.** Normalization uses **average ranks**
(`scipy.stats.rankdata(method='average')`), not ordinal argsort-of-argsort. The
vast majority of genes have exactly-zero TxGNN support; ordinal ranking would
spread those tied zeros arbitrarily across [0, 1] and inject a meaningless
tie-break into the combined score (it can even invert two genes purely on the zero
tie-break). Average ranks collapse all no-support genes to a single neutral value,
so their order is decided entirely by the RWR term, and only genes with genuine
drug-target support are lifted above that baseline. Because both terms are
rank-based, the combined score clusters near 1.0 at the top — this reflects rank
normalization, not effect size.

## 7. Annotation

- **Known drug target** (`known_drug_target`): targeted by any drug with an
  `indication` or `off-label use` edge to an anchor disease. This is the label used
  by the enrichment self-check.
- **ADME/PK** (`likely_ADME_PK`): symbol matches drug-metabolism/transport families
  (CYP, UGT, SLCO, ABCB/ABCC/ABCG, GST) or a small exact set (ALB, SERPINA6, ORM1,
  ORM2, AHR). These can rank highly for pharmacokinetic rather than
  disease-mechanistic reasons and are flagged, not removed.

## 8. Face-validity self-check

With no held-out ground truth, the primary sanity check is whether known drug
targets concentrate at the top. `check_enrichment.py` computes known-target
fold-enrichment per rank bin and flags a failure if the top bin is not enriched
above the genome-wide background. A calibrated ranking shows a high top-bin
enrichment that **decreases** down the list. *IBD reference run: top-50 ≈ 63.9×,
then 9.1× → 2.9× → 1.1× → 0.8×; background rate ≈ 0.00438.*

## 9. Evidence paths

Every top hit is explained by enumerating four path templates through PrimeKG:
(A) direct disease→target (seed); (B) disease→seed→[PPI]→target;
(C) disease→seed→shared-concept→target; (D) drug→target where the drug is
indicated/off-label/TxGNN-predicted for an anchor. This makes each ranking
auditable rather than a black-box score.

## 10. Limitations (carry into the report)

- **Discovery, not inference** — a ranking, not a statistical test; no p-values/FDR.
- **Degree / hub bias** — highly connected genes can rank up for topological
  reasons; mitigated (concept down-weighting, concept-size cap, ADME flag) but not
  eliminated. Treat very high-degree hits cautiously (see the `degree` column).
- **TxGNN is repurposing, not target inference** — target support is derived
  indirectly through drug→target edges; some predicted "drugs" are early-stage or
  tool compounds.
- **Tunable defaults** — restart 0.30 and 0.70/0.30 weighting are reasonable
  defaults, not optimized; rankings shift under other settings (exposed as flags).
- **"Known target" is graph-derived** — from PrimeKG indication/off-label edges,
  which may be incomplete or lag current clinical practice.
- **KG coverage** — results inherit PrimeKG's gaps and biases; absence of an edge
  is not evidence of absence. Human targets only.
