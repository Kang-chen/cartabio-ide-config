# Vendored full-text utilities

The claim-first runner retains only the reusable open-access and parsing pieces
from the former `literature-keyword-evidence` engine:

| File | Purpose |
|---|---|
| `keyword_evidence/search.py` | Europe PMC lookup, ID normalization, local-PDF records, deduplication |
| `keyword_evidence/acquire.py` | OA-only PDF/JATS acquisition waterfall with validated downloads |
| `keyword_evidence/parse_pdf.py` | PDF sentences, sections, figures, page/bbox provenance, parse cache |
| `keyword_evidence/parse_jats.py` | JATS XML to the same provenance block contract |
| `keyword_evidence/ocr_figures.py` | Optional cached OCR for selected figure images |

The former LangExtract `deep_dive` package, keyword-only report runner, and custom
control plane were removed. Semantic adjudication now runs only on retrieved
candidate blocks through `scripts/llm_adjudicator.py`; deterministic grounding is
implemented in `scripts/evidence_first.py` and `scripts/verify_review.py`.

When upstream acquisition or parser fixes are imported, preserve the OA-only
policy, permissive PDF stack, output schema, and cache-key behavior.
