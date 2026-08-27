---
name: first-in-class-oncology-digest
version: 1.0.0
description: >
  On-demand digest of novel first-in-class oncology targets and mechanisms
  disclosed across peer-reviewed literature, conference proceedings,
  company pipeline news, and SEC/regulatory filings. Produces a structured
  Word report with summary table and full target briefs.
tags: [oncology, target-discovery, first-in-class, landscape, digest]
author: Biomni
---

# First-in-Class Oncology Target Digest

## Purpose

Generate a structured digest of newly disclosed first-in-class oncology targets and mechanisms. The digest covers two layers of novelty:

- **Novel Target**: A gene/protein that has never been drugged before in any indication.
- **Novel Mechanism**: A therapeutic approach or modality that is first-in-class for a known target/indication.

## Source Categories

| Source | Method | Key Queries |
|---|---|---|
| **Peer-reviewed literature** | `LiteratureSearch` | "first-in-class oncology target", "novel cancer drug target", "new druggable target tumor", "first-in-class cancer therapy" |
| **Conference proceedings** | `WebSearch` | ASCO + AACR + ESMO + SITC + ASH + "novel target" / "first-in-class" / "new mechanism" |
| **Company pipeline news** | `WebSearch` | "first-in-class oncology" + press release / pipeline update / investor presentation |
| **SEC/regulatory filings** | `WebSearch` | SEC 10-K / 10-Q / S-1 + "first-in-class" + oncology / cancer |

## Time Window

- Default: last 7 days from run date
- First run can be extended to 30 or 90 days for baseline coverage

## Classification Rules

1. **Novel Target**: No prior clinical-stage drug targeting this gene/protein in any indication. Cross-reference against Open Targets, ClinicalTrials.gov, and search results.
2. **Novel Mechanism**: Target may be known, but the therapeutic approach (modality, bispecific design, payload, delivery format) is first-in-class for that target/indication.
3. **Both**: Many entries qualify on both dimensions. Tag accordingly.
4. **Deduplication**: Same target disclosed in multiple sources = one entry with multiple source citations.

## Competitive Density Tags

- **No prior clinical**: No other clinical-stage program targeting this gene/protein.
- **Early-phase only**: Other programs in Phase 1/2 but none approved or in Phase 3.
- **Established target with new mechanism**: Target is well-known with approved drugs, but this approach is novel.

## Output Format

Word document (.docx) using the `docx-generation` skill with Phylo branding:

1. **Title block**: Digest date, time window, therapeutic area
2. **Executive summary**: Key figures (novel targets count, novel mechanisms count, clinical-stage count), callout box
3. **Summary table**: One row per target — Target | Drug | Company | Indication | Novelty | Phase
4. **Full target briefs** (one per target):
   - Key facts table (drug, company, indication, novelty, competitive density, phase, source)
   - Rationale (why first-in-class)
   - Supporting evidence
   - Mechanism of action
5. **Methodology**: Search strategy, classification rules, limitations

## Search Prompt Templates

### Literature
```
LiteratureSearch:
  query: "first-in-class oncology target novel cancer drug"
  year_min: <current_year>
  max_papers: 20

LiteratureSearch:
  query: "novel druggable target tumor new mechanism cancer therapy"
  year_min: <current_year>
  max_papers: 20
```

### Conferences
```
WebSearch: "ASCO <year> AACR <year> ESMO <year> novel target first-in-class oncology abstract"
WebSearch: "ASCO <year> annual meeting novel target first-in-class oncology abstract"
```

### Company Pipeline
```
WebSearch: "first-in-class oncology press release pipeline update <year> novel target"
WebSearch: "novel cancer drug target disclosed <month> <year> press release"
```

### SEC Filings
```
WebSearch: "SEC filing 10-K 10-Q S-1 first-in-class oncology cancer novel target <year>"
```

## Assumptions

- The digest is informational/scouting, not investment-grade due diligence.
- WebSearch covers recent indexed content but cannot guarantee exhaustive SEC filing coverage.
- Conference coverage depends on public abstract availability (major society meetings: ASCO, AACR, ESMO, SITC, ASH).
- "Novel target" classification is based on available evidence and may not reflect undisclosed programs.
- Competitive landscape context is based on search results and domain knowledge; direct API queries to Open Targets/ClinicalTrials.gov can supplement but are not required.

## Compute Estimate

- Search-heavy, not compute-heavy: ~10-15 min total
- No large datasets, no HPC needed
- Default machine sufficient
