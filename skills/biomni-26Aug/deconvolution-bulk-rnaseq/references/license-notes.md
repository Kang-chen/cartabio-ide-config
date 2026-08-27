# License notes — why the method panel is what it is

Biomni is a **commercial** platform, so every bundled tool must permit commercial
use. Deconvolution is unusual in that the best-known method (CIBERSORTx) is
**not** commercially licensed — so license, not just accuracy, gates the panel.

## ✅ Permitted (commercial-OK) — what this skill uses

| Method | License | Notes |
|--------|---------|-------|
| BayesPrism | GPL-3 | Primary; robust to reference↔bulk mismatch |
| DWLS | GPL-2 | Cross-check; handles collinear cell types |
| MuSiC | GPL-3 | Optional; multi-subject weighting |
| BisqueRNA | GPL-3 | Optional; fast marker-based |
| SCDC, Scaden, AutoGeneS, CPM, CDSeq, MOMF | GPL / MIT / BSD | Other commercially-usable options reachable via omnideconv |
| omnideconv (framework) | GPL-3 | Uniform interface + per-method normalization |

GPL/LGPL, MIT, BSD, Apache-2.0, CC-BY, CC0 all permit commercial use. (GPL imposes
copyleft on *distribution of modified source*, not on *use* to produce results —
fine for an analysis platform.)

## ❌ Excluded — do not use on Biomni

| Tool | License | Why excluded |
|------|---------|--------------|
| **CIBERSORTx** | Stanford **non-commercial** | Free only for non-profit/academic; industry needs a paid license. A frequent benchmark top-performer, but **off the table here**. |
| **EPIC** | **academic-only** | For-profit use requires a separate commercial license. |
| **BSeq-sc** | inherits CIBERSORT | Wraps the original CIBERSORT source → inherits its restriction. |

`run_deconvolution()` **hard-errors** if any of `cibersortx`, `cibersort`, `epic`,
or `bseqsc` is requested — this is intentional, not a bug.

## What to tell the user

If a user asks for CIBERSORTx (it is the method many reviewers know):
> CIBERSORTx is excluded because its Stanford license forbids commercial use, which
> applies here. BayesPrism is the closest commercially-licensed substitute (Bayesian,
> robust to reference/bulk mismatch); we run it alongside DWLS and report how well the
> two agree. If you separately hold a CIBERSORTx license, run it outside this skill and
> compare its proportions to the `consensus_proportions.csv` produced here.

## Reference

- BayesPrism: Chu T, et al. *Nat Cancer* 2022.
- DWLS: Tsoucas D, et al. *Nat Commun* 2019.
- MuSiC: Wang X, et al. *Nat Commun* 2019.
- Bisque: Jew B, et al. *Nat Commun* 2020.
- omnideconv: Dietrich A, et al. 2024 (GitHub: omnideconv/omnideconv).
