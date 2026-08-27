# Artifact Schemas

All artifacts live under `$RUN = /mnt/results/target_direction_<slug>/`.

## `data/evidence_matrix.csv`
One row per **target × axis**.

| column | type | meaning |
|---|---|---|
| `target` | str | gene symbol |
| `ensembl_id` | str | resolved Ensembl gene ID |
| `indication` | str | disease/context this call is for |
| `axis` | str | `Human genetics` \| `Functional/CRISPR` \| `Drug MoA` \| `Mouse KO` \| custom |
| `raw_readout` | str | the biological readout in plain language (direction of effect) |
| `vote` | str | `INHIBIT` \| `ACTIVATE` \| `not_informative` |
| `informative` | int | 1 if the axis contributes to the denominator, else 0 |
| `source` | str | `opentargets` \| `depmap` \| `genebass` \| `gwas_catalog` \| `gnomad` \| `literature` |
| `cites` | str | comma-separated reference indices, e.g. `"12, 14, 37"` (map to references.jsonl) |
| `note` | str | caveat / mechanism note (e.g. "allele-specific to I148M"; "broad essentiality") |

## `data/consensus_calls.csv`
One row per **target**.

| column | type | meaning |
|---|---|---|
| `target` | str | gene symbol |
| `indication` | str | disease/context |
| `consensus` | str | `INHIBIT` \| `ACTIVATE` \| `CONTESTED` |
| `n_informative` | int | # informative axes (denominator) |
| `n_agree` | int | # informative axes agreeing with the consensus |
| `concordance` | str | e.g. `"4/4"`, `"3/4"` |
| `confidence` | str | `High` \| `High–Moderate` \| `Moderate` \| `Low–Contested` |
| `flagged` | str | list of `(axis, reason)` needing interpretation/opposing |
| `key_flag` | str | the single most important flag, with mechanism + citation(s) |

## `data/opentargets_raw.json`
```json
{
  "data_version": "2026.06",
  "targets": {
    "PCSK9": {
      "ensembl_id": "ENSG00000169174",
      "is_essential": false,
      "drugs": [{"name": "...", "drug_type": "...", "max_stage": "APPROVAL",
                  "moa": [{"mechanism": "...", "action_type": "INHIBITOR"}]}],
      "mouse_phenotype_classes": [{"id": "...", "label": "..."}],
      "associated_diseases": [{"efo": "...", "name": "...", "score": 0.85,
                                "datatype_scores": {"genetic_association": 0.86}}],
      "safety_liabilities": [{"event": "...", "datasource": "..."}]
    }
  }
}
```

## `data/depmap_summary.csv`
One row per target (summarizing across cell lines; optionally per lineage).

| column | meaning |
|---|---|
| `target` | gene symbol |
| `n_lines` | # cell lines with a gene-effect value |
| `mean_gene_effect` | mean CRISPRGeneEffect (negative = essential) |
| `frac_dependent` | fraction of lines with dependency probability > 0.5 |
| `pan_essential` | bool: broadly essential (e.g. mean_gene_effect < −0.5 across most lines) |
| `selective_lineage` | lineage(s) with strongest dependency, if any |
| `interpretation` | mapped functional readout in plain language |

## `data/genetics_summary.csv`
| column | meaning |
|---|---|
| `target` | gene symbol |
| `plof_direction` | direction of pLoF-burden effect (protective / risk / none) |
| `gwas_direction` | lead-variant direction of effect for the indication, if available |
| `constraint` | gnomAD LOEUF / pLI context (LoF-intolerant?) |
| `source` | genebass / gwas_catalog / gnomad |
| `cites` | reference indices |

## `data/citation_verification.json`
```json
{ "status": "clean", "doi_layer_status": "clean",
  "n_citations_used": 44, "missing": [], "flagged": [] }
```

## `synthesis.json` (agent-authored; the report builder's content input)
```json
{
  "title": "Direction-of-Effect Concordance: <targets>",
  "subtitle": "Integrating human genetics, functional/CRISPR, drug MoA, and mouse knockout",
  "slug": "target_direction",
  "data_version": "Open Targets 2026.06; DepMap 24Q...",
  "executive_summary": "…",
  "direction_rule_table": [["Axis","Raw readout → vote"], ["Human genetics","…"], ...],
  "methods": "…",
  "results_intro": "…",
  "per_target_sections": [
     {"target": "PCSK9", "verdict": "INHIBIT", "confidence": "High",
      "body": "…source-bound narrative with [n] citations…", "figure": null}
  ],
  "discussion": "…how conflicts were handled; regime/allele-conditional…",
  "limitations": "…",
  "next_steps": "…",
  "callouts": ["Bottom line: INHIBIT PCSK9 (High) | …"],
  "doi_layer_status": "clean",
  "n_citations_used": 44
}
```
`per_target_sections[i].figure` may name a file in `figures/` to embed after the section.
Every sentence must be source-bound and past the Step 5 gate.

## `references.json` (verbatim, verified)
```json
[ {"n": 1, "text": "Author A, Author B. Verbatim Title. Journal. Year. doi:10.xxxx/…"}, ... ]
```
Indices must match the `[n]` markers used in `synthesis.json` and the `cites` columns.

## `figures/fig_manifest.csv`
| column | meaning |
|---|---|
| `file` | figure filename (png) |
| `kind` | `evidence_matrix` \| `consensus_summary` \| `infographic` \| custom |
| `caption` | figure caption for the report |
