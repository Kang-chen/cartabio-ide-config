# Licence and data-source constraints

**Load when** the author's answer to Q6 arrives, or any dependency, database or dataset is proposed.
**Skip if** the skill touches nothing but the user's own uploaded data and the standard scientific
Python or R stack.
**What this will not tell you** whether a specific new dependency is cleared. It gives you the bar, the
verified prohibitions, and the procedure. A dependency not listed here has not been checked — checking
it is the work, and an honest "I could not clear this" is a valid outcome.

Skills ship inside a commercial AI-agent product. That is a stricter bar than "open source": a licence
can be OSI-approved and still be unusable here.

---

## The two separate questions

These get conflated constantly, and they have different answers.

1. **Code licence** — may we link, vendor and redistribute this software inside a commercial product?
   Governed by the repository's own LICENSE file.
2. **Data terms of use** — may we ingest this dataset or catalogue into an automated system? Governed
   by the supplier's terms, which are **not** a software licence and are frequently far more
   restrictive. That a dataset is free to download says nothing about this.

A dependency must clear **both** questions where both apply.

---

## Code licences

**Permitted:** MIT · Apache-2.0 · BSD (2- and 3-Clause) · ISC · MPL-2.0 · PSF · Unlicense · CC0 ·
**GPL (v2, v3) · LGPL** · CC-BY

**Needs review before use — not on the permitted list:**

| Licence | Why it needs a decision |
|---|---|
| **AGPL** | Copyleft reaches across a network boundary, which is how skills are served. Materially different from GPL; do not assume the GPL allowance covers it |
| **No LICENSE file at all** | Absent a licence there is no grant to use, copy or redistribute. **This is worse than a copyleft licence, not better** — a permissive-sounding README is not a licence |
| Non-commercial / academic-only | CC BY-NC, "free for academic use", registration-gated redistribution |
| Anything unclear | Say so and stop rather than assuming |

**GPL and LGPL are permitted.** Sources outside this package sometimes state otherwise; this list is the
operative one. Where a copyleft dependency is *vendored or statically embedded* rather than invoked as an
independently installed tool, note that in the record so the distinction is visible to whoever reviews
redistribution.

---

## Prohibited — verified, do not use

These are prohibited on grounds **other than** a copyleft code licence.

| Dependency | Problem |
|---|---|
| **Enamine catalogue data** (building blocks, REAL, catalogue codes, stock, availability) | Their store terms prohibit incorporating Enamine data into computational or AI systems without written authorization. This is a **terms-of-use** restriction, not a code licence, and it covers *incorporation* — shipping the SMILES as a test fixture is very likely covered too. Use ZINC in-stock instead |
| **SCAM Detective** (`alvesvm/scam_detective`) | **No LICENSE file** — no grant at all. Method reference only; never vendor, import, clone or pip-install. Its training data (AmpC β-lactamase and cruzain counter-screens) is public in PubChem, so a model may be retrained from primary data under our own licence |

## Restricted — usable with a recorded obligation

| Source | Obligation |
|---|---|
| **ChEMBL** | CC BY-SA 3.0 — attribution **and** ShareAlike. The notice must appear in the generated report, not only in a metadata file. A missing ChEMBL notice is a concrete review failure, not a nitpick |
| **ZINC / ZINC20 in-stock** | Free to use and download. **Do not redistribute major portions** without written permission from the maintainers. Cite the ZINC paper |
| **cellHTS2** | Artistic-2.0 and deprecated upstream. Technique reference only; do not take it as a dependency |
| Any supplier catalogue | Treat it like Enamine until its terms have actually been read. Assume nothing from the existence of a download link |

**Copyleft packages are fine.** BayesPrism (GPL-3), DWLS (GPL-2), MuSiC (GPL-3), BisqueRNA (GPL-3),
SYBA (GPL-3), VirtualFlow (GPL-2) and similar are permitted under the list above. Record the licence in
`DATA_SOURCES.md` as you would any other.

---

## Company names: a citation is not a customer reference

A skill can ship to every tenant, so a sentence that reveals *who the work was for* is the one mistake
that cannot be undone after release. But that is a rule about **references to a customer**, not a ban on
company names, and conflating the two makes correct attribution look like a leak.

**Fine — factual citation of something public.** The name is part of a public identifier, and removing
it would make the citation wrong or unverifiable:

- a public repository, package or dataset whose name contains a company name
  (`IanAWatson/Lilly-Medchem-Rules`, `rdkit/mmpdb`)
- a copyright holder recorded in a LICENSE file (© Eli Lilly and Company, © Roche)
- a published method, tool or database named after its originating organisation
- a company named in a citation to public literature

**Not fine — anything that implies who we are working for.** These say nothing about the science and
everything about the engagement:

- "a `<Company>` statistician will ask about…", "the `<Company>` team prefers…"
- a customer's internal project or programme codename
- a customer's compound, target or asset identifiers
- example data, column names or file paths taken from a customer's own files
- a threshold or convention justified as "this is how `<Company>` does it"

**The test:** would this sentence still be written if the skill had no customers at all? A licence
attribution would. "A `<Company>` reviewer expects two decimal places" would not.

When in doubt keep the citation and drop the framing — cite the repository, not the relationship.

---

## What every skill records

A `DATA_SOURCES.md` at the package root, one row per dependency **and** per data source:

```markdown
| Source | Version | Licence | Commercial use | Evidence |
|---|---|---|---|---|
| mmpdb | 3.1.4 | BSD-3-Clause | yes (permissive) | read from LICENSE.txt; classifier reports NOASSERTION on a multi-party header |
| ChEMBL | 37 | CC BY-SA 3.0 | yes, with attribution + ShareAlike | notice rendered in the report body |
| BayesPrism | 2.2.2 | GPL-3.0 | yes (copyleft is permitted) | read from DESCRIPTION; invoked as an installed tool, not vendored |
| Enamine REAL | — | terms of use | **NO — EXCLUDED** | store terms forbid incorporation into automated systems |
```

**Excluded dependencies stay in the table with the reason.** A silent omission reads as an oversight; a
recorded exclusion is a decision. Obligations that must reach the reader — attribution, ShareAlike —
belong in the report itself, not only in this file.

---

## Adding a dependency — the procedure

1. Read the repository's own licence file. Not the sidebar, not the package index, not the paper.
2. If it is a dataset or catalogue, find and read the **terms of use** as well.
3. Check it against the prohibited list above.
4. Record the outcome in `DATA_SOURCES.md`, including exclusions.
5. If a required capability exists only under a prohibited licence, **say so and stop.** An honest
   blocker is a deliverable. Do not substitute silently, and do not proceed hoping nobody checks.

When you discover a new constraint, write it into this file. A constraint recorded only in a ticket or a
spreadsheet gets rediscovered the expensive way, by a review failure, more than once.
