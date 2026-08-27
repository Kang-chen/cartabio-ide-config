# Data Schemas

All intermediate artifacts use these schemas so the scripts interoperate. CSV and JSON
versions carry identical fields. Generalized from a working benchmark-synthesis run.

---

## `corpus.csv` — consolidated retrieved records
One row per unique paper (deduped by normalized DOI, then normalized title).

| column | type | notes |
|---|---|---|
| `source` | str | provenance, e.g. `literature_search`, `literature_curated`, `europepmc` |
| `pmid` | str | may be empty |
| `doi` | str | normalized lowercase; primary dedup key |
| `doi_source` | str | how the DOI was obtained (e.g. `record`, `crossref`) |
| `title` | str | **verbatim** from the record — never paraphrase |
| `authors` | str | as returned |
| `journal` | str | |
| `publication_date` | str | ISO or year |
| `publication_type` | str | e.g. `research-article`, `review`, `preprint` |
| `keywords` | str | ; -separated |
| `abstract` | str | full abstract when available |
| `is_open_access` | bool | |
| `is_preprint` | bool | true if DOI contains arxiv/biorxiv/10.48550/10.1101 |
| `url` | str | resolvable link (prefer doi.org) |
| `citation_count` | int | when available |

## `screening_log.csv` — PRISMA-style screening
| column | type | notes |
|---|---|---|
| `doi` | str | key into corpus |
| `title` | str | verbatim |
| `decision` | str | `include` / `exclude` |
| `label` | str | include category: `method` / `benchmark` / `comparison` / `review` / `other` |
| `exclude_reason` | str | when excluded (draft heuristic): `off_topic` / `clinical` / `preclinical` (agent may add reasons such as `non_methodological` on review) |

---

## Comparison-mode artifacts

### `comparison_matrix.csv`
Wide table. First column `Dimension`; remaining columns are the method names (one per method).
Rows are comparison dimensions (algorithmic model, statistic, normalization, assumptions,
speed, maturity, best-fit regime, …). Values are short cell strings.

```
Dimension,<Method A>,<Method B>,<Method C>
Count model,...,...,...
Normalization,...,...,...
Best-fit regime,...,...,...
```

### `benchmark_catalog.json` / `.csv` — the studies that anchor the claims
List of objects:
| field | notes |
|---|---|
| `benchmark_name` | short name of the benchmark study/design |
| `benchmark_type` | e.g. `real reference + spike-in`, `simulated + real`, `permutation-null` |
| `organism` | e.g. `Human`, `S. cerevisiae`, `synthetic` |
| `truth_basis` | what defines ground truth (verbatim-ish, source-bound) |
| `key_metric` | the headline result/metric of the study |
| `defining_paper` | short citation, e.g. `Schurch et al. 2016` |
| `doi` | DOI of the defining paper (must be verifiable) |

### `performance_claims.json` / `.csv` — source-bound claims
List of objects:
| field | notes |
|---|---|
| `method` | which method the claim is about |
| `dimension` | axis of comparison, e.g. `low-replicate FDR`, `outlier robustness`, `large-sample FDR` |
| `finding` | the specific claim (concise, source-bound) |
| `benchmark` | which benchmark it came from |
| `source` | short citation |
| `doi` | DOI (must be verifiable) |
| `evidence_thickness` | `head_to_head` \| `multi_benchmark` \| `single_benchmark` (weight from strong→weak) |

---

## Topic-mode artifact

### `evidence_table.csv`
| column | notes |
|---|---|
| `theme` | grouping/topic bucket |
| `finding` | the claim/result (source-bound) |
| `study_type` | e.g. RCT, cohort, simulation, method paper, review |
| `effect_or_metric` | quantitative detail when present |
| `source` | short citation |
| `doi` | DOI (must be verifiable) |

---

## Figure & report artifacts

### `theme_table.csv` — topic-mode **figure** input (derived)
`make_figures` plots topic-mode figures from this aggregated table, which you derive from the
populated `evidence_table.csv` (one row per theme):

| column | notes |
|---|---|
| `theme` | theme name (matches `evidence_table.theme`) |
| `n_papers` | count of papers/rows supporting the theme |
| `consensus_level` | optional: `strong` \| `moderate` \| `weak` \| `contested` |
| `evidence_quality` | optional: `high` \| `moderate` \| `low` |

### `fig_manifest.csv` — written by `make_figures.{R,py}`
| column | notes |
|---|---|
| `file` | figure filename (PNG) in the run dir |
| `mode` | `comparison` \| `topic` |
| `caption` | default caption; the report builder uses it unless a section overrides it |

### `synthesis.json` + `references.json` — report builder inputs (agent-authored)
Authored **after** the verify gate passes; full field-by-field schema is in
`reporting_notes.md`. `synthesis.json` holds ALL narrative text (title, sections, callouts,
next_steps); `references.json` is the ordered, verbatim, verified reference list. The builder
lays these out — it never writes prose itself.

---

## `citation_verification.json` — output of the verify gate
| field | notes |
|---|---|
| `doi_layer_status` | `clean` \| `partial` \| `failed` |
| `citations_checked` | int |
| `citations_verified` | int |
| `numbers_checked` | int |
| `numbers_verified` | int |
| `flagged_or_dropped` | list of `{item, reason}` for anything unverifiable |
| `provenance_note` | how verification was performed (records / transcript re-check) |
| `known_caveats` | list of integrity caveats to carry into the report |
