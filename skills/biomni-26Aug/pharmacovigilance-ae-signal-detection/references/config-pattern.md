# Configuration & Agent-Tool Integration Pattern

How to drive the pipeline via `AnalysisConfig`, tune `SignalCriteria`, and wire in
the two agent-only tools (`LiteratureSearch`, `GenerateImage`).

## `AnalysisConfig`

```python
from scripts.run_analysis import AnalysisConfig
from scripts.compute_disproportionality import SignalCriteria

cfg = AnalysisConfig(
    query="upadacitinib",          # REQUIRED: str | list[str] | class str | target str
    out_dir="pv_results/upa",      # REQUIRED: artifact directory
    mode=None,                     # None = auto-detect; or "explicit"|"class"|"target"
    subject_label=None,            # human title override (else the resolved subject)
    comparator=None,               # None = whole FAERS; or ["drugA","drugB"] active comparator
    top_n_events=500,              # reaction terms per drug (OpenFDA cap = 500)
    criteria=SignalCriteria(),     # signal thresholds (see below)
    pool=True,                     # for multi-drug sets, also analyse the pooled union
    pool_label="combined",         # name of the pooled pseudo-drug
    api_key=None,                  # optional OpenFDA key (higher rate limits)
    references=[],                 # LiteratureSearch records (or pass later to finalize_report)
    infographic_path=None,         # GenerateImage output (or pass later)
    make_report=True,
    make_figures=True,
)
```

### Mode auto-detection (`detect_mode`)
- **explicit** — `query` is a list, or a plain drug name that isn't a class/target
- **target** — `query` matches `[A-Z0-9]{2,10}` (a gene symbol like `JAK1`, `EGFR`) and isn't purely digits
- **class** — `query` matches class hints (`inhibitor`, `agonist`, `blocker`, `anti-`, `statin`, `sartan`, `prazole`, `gliflozin`, `gliptin`, `biologic`, `class`)
- Ambiguous all-caps drug codes may misdetect as target — pass `mode="explicit"` to force.

### Subject & pooling
- **Single drug / explicit single** → that drug is the subject.
- **Multi-drug (explicit list, class, target)** with `pool=True` → a pooled
  pseudo-drug (`pool_label`, default `"combined"`) is added as the primary
  subject; per-drug rows remain for the heatmap and per-drug tables. The pooled
  rows are label-grounded using a **representative member** (the first resolved
  drug).
- 0-report drugs are dropped automatically; see `result["dropped"]`.

## `SignalCriteria`

```python
SignalCriteria(
    ror_ci_lower_min=1.0,   # ROR 95% CI lower bound must exceed this
    prr_min=2.0,            # PRR threshold
    chi2_min=4.0,           # chi-square threshold (~p<0.05, 1 df)
    min_cases=3,            # minimum co-reported count a to be a signal
    use_fdr=True,           # additionally require FDR q < fdr_q
    fdr_q=0.05,             # BH-FDR q threshold
    continuity_correction=0.5,  # added to all cells when any cell == 0
    # --- low-confidence flagging (marks signals, never removes them) ---
    min_cases_confident=10,     # a < this -> low_count flag (fragile count)
    extreme_ror_enable=True,    # enable the extreme-ROR outlier flag
    extreme_ror_iqr_k=3.0,      # Tukey far-out multiplier on ln(ROR)
    extreme_ror_abs_floor=25.0, # ROR must also exceed this to be flagged
)
```
`.describe()` renders the active signal rule; `.describe_confidence()` renders the
low-confidence rule (both strings appear in `table1_overview.csv` and the report).
Loosen (e.g. `min_cases=1`, `use_fdr=False`) only for exploratory scans; tighten
(`min_cases=10`) to focus on robust signals. The low-confidence flag **does not
drop** anything — it adds `low_count`, `extreme_ror`, `low_confidence`, and
`low_confidence_reason` columns so fragile/inflated signals (small counts;
notoriety-driven extreme ROR) are marked, not hidden. To disable the extreme-ROR
flag set `extreme_ror_enable=False`; to disable count-based flagging set
`min_cases_confident=0`.

## Agent-tool integration (the two steps a module can't do)

A plain Python module cannot call the `LiteratureSearch` or `GenerateImage`
**agent tools**. The orchestrator therefore produces the exact inputs and lets
the agent run them, then re-assembles the report:

```python
result = run_analysis(cfg)          # deterministic pipeline + draft PDF

# 1) LITERATURE — build query is done for you:
print(result["literature_query"])
#   -> call the LiteratureSearch tool with this string
#   -> collect records: [{"title","authors","year","journal","doi"/"url"}, ...]

# 2) INFOGRAPHIC — prompt is built for you:
print(result["infographic_prompt"])
#   -> call the GenerateImage tool with this string, save PNG to out_dir

# 3) REBUILD the report with both:
from scripts.run_analysis import finalize_report
final_pdf = finalize_report(
    result["context"], cfg.out_dir,
    references=lit_records,                       # from step 1
    infographic_path="pv_results/upa/infographic.png",  # from step 2
)
```

`finalize_report` mutates the `ReportContext` in place (sets `.references` /
`.infographic`) and rebuilds the PDF. Passing only one of the two is fine; both
are additive to the already-complete draft report.

### Optional: literature-grounded `lit_support` column
To flag which events have literature support in the full CSV, call
`annotate_signals.attach_literature(res, references, ...)` after fetching
records; it adds a boolean `lit_support` column by matching event terms against
titles/abstracts.

## Common recipes

```python
# Single drug, table only (no report/figures) — fast:
AnalysisConfig(query="semaglutide", out_dir="out", make_report=False, make_figures=False)

# Drug class, whole-FAERS background:
AnalysisConfig(query="SGLT2 inhibitors", out_dir="out")

# Target, pooled, active comparator within the class:
AnalysisConfig(query="JAK1", out_dir="out",
               comparator=["adalimumab","etanercept"])   # anti-TNF comparator

# Exploratory (loose thresholds):
AnalysisConfig(query="drugX", out_dir="out",
               criteria=SignalCriteria(min_cases=1, use_fdr=False))
```

## S3 FUSE gotchas (when editing these scripts)
- Build PDFs / random-access files in `/workspace`, then `cp` to `/mnt` (the
  report helpers already do this).
- Never `sed -i` or `cat >>`-append files under `/mnt` — use the `Write`/`Edit`
  tools. S3 FUSE returns "Function not implemented" or silently no-ops.
