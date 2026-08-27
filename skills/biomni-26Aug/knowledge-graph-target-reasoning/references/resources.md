# Biomni resources used by this skill

PrimeKG is required and TxGNN is optional. Verify Python functions with direct
imports.

## Primary inputs

| Resource | Role |
|---|---|
| **PrimeKG** | The knowledge graph. Columns include `relation, x_type, x_id, x_name, y_type, y_id, y_name`. Streamed in 1M-row chunks. |
| **TxGNN predictions** | Pickle → `dict` keyed by disease **name**; value is `{drug_id: score}`. Optional drug-target layer. |
| **TxGNN name mapping** | Bridges disease/drug names ↔ ids; use to find the right TxGNN dict key for an anchor. |

## Compute footprint (reference IBD run)

- PrimeKG parse: streamed, bounded memory (chunked). Gene universe ~21k.
- Adjacency: sparse, ~6.6M non-zeros. RWR: ~31 iterations.
- End-to-end `rank_kg_targets.py`: **~17 s** on the default `worker-0` sandbox.
- TxGNN layer is a pickle load (no model inference).
- **No GPU, no HPC, no large machine required.** The default machine suffices.

## Packages (preinstalled)

`numpy`, `pandas`, `scipy` (sparse + `scipy.stats.rankdata`), `matplotlib`.
`matplotlib-venn` is only needed for the optional seed-intersection figure
(`uv pip install matplotlib-venn` if used). `reportlab` + `pypdf` are used by the
`pdf-report-generation` skill for the report.

## Biomni tools invoked by the workflow

- **`LiteratureSearch`** — validate the top-N ranked targets: query each target
  with the disease, attach a supporting sentence + citation(s). Findings must be
  grounded in returned sources; never fabricate citations.
- **Direct checks** — import related tools.
- **`pdf-report-generation` skill** — render the final Phylo-branded PDF (branding,
  fonts, tables, validation). This skill specifies report *structure*; that skill
  owns *rendering*.
- **`predict_admet_properties`** — optional downstream triage of a hit's
  druggability from SMILES (pharmacology tool).

## Orthogonal follow-up resources (NOT ranking inputs)

These are deliberately **not** folded into the ranking (the method is faithful to
PrimeKG + TxGNN). Recommend them as next-step validation of top hits:

- **Open Targets** (skill / GraphQL) — independent target–disease association
  scores and evidence; strong cross-check for the top hits.
- **ChEMBL**, **STRING**, **Reactome**, **KEGG** — bioactivity, interaction, and
  pathway context.
- **GWAS Catalog / genetics** — human-genetic support for a target–disease link.
- **DepMap** — dependency/essentiality of a target in relevant cell models.

## Related skills

- `pdf-report-generation` — required, for the report.
- `open-targets` — recommended orthogonal validation of top hits.
- `literature-review` / `literature-preclinical` — deeper literature synthesis
  beyond the per-target `LiteratureSearch` step.
