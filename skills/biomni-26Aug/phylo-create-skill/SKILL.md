---
id: "skill_80991743e52842abb92207cd7ff8c29e"
name: "phylo-create-skill"
description: "Use to create, test, validate, package, or revise reusable Phylo Biomni skills across analysis, evidence synthesis, protocols, guidance, utilities, and platform workflows."
category: "general"
visibility: "public"
starting-prompt: "Create a reusable Biomni skill for multivariable differential expression on the Bioconductor airway dataset."
---

<!-- archetype: meta-tooling -->

# Phylo Create Skill

Everything a script can check has been moved into a script. Run it; do not restate its rules from memory. **Use the public authoring path before inspecting implementation:** read this `SKILL.md`, load
only references routed by the current step, write the structured interview record, then scaffold. Do
not read `scripts/*.py`, `templates/report_qc.py`, or eval-suite source before the first attempt. The
CLIs, generated contract, stable API fences, and specific validation errors are the public interface.
Inspect implementation only when an error is not specific enough; reverse-engineering validators
before scaffolding consumes the run budget without improving the generated skill.

```bash
SKILL_DIR=/mnt/skills/system/phylo-create-skill   # use the **Skill Directory** value printed on load
python3 "$SKILL_DIR/scripts/scaffold_skill.py" --help
python3 "$SKILL_DIR/scripts/check_skill.py" --help
```

Never use a bare `scripts/…` path. The working directory is `/workspace`, not this package, so a
relative path resolves to `/workspace/scripts/…` and does not exist.

---
## Step 0 — Which contract, which archetype

**Two destinations. Ask which before writing anything. Neither registers a Personal Skill by itself.**

| | **Local draft** — reviewed from the results drive | **Catalog candidate** — handed to the shared catalog owner |
|---|---|---|
| Written to | `/mnt/results/skills/<slug>/` | the same, then handed to whoever owns the catalog |
| Gate | evidence contract + local package gate | the platform's packaging validator, which is stricter |
| Frontmatter | only `name` + `description` are enforced | all six keys, in the canonical order below |
| Publishing | none — keep it on the results drive | a separate, explicit, human-approved step |

**Write full frontmatter either way.** After validation, offer a private Personal Skill preview/save;
never register without explicit confirmation. In a review-loop draft, defer the offer until expert
handoff. Keep approval outside the immutable package, keyed to its reviewed hash.

```yaml
---
id: "skill_<32 lowercase hex chars>"        # a fresh random token, never derived from the slug
name: "<slug>"                              # must equal the folder name exactly, lowercase-kebab
description: "<what it does and when to use it>"   # under 500 chars, routing verbs first
category: "<one of the 17 below>"
visibility: "internal"                      # internal | public | shared. never "private" here
starting-prompt: "<the short research question from starting_task.user_prompt>"   # required
---
```

**Order is not cosmetic — the validator rejects these six keys in any other sequence.** Single-line,
double-quoted values only; a YAML block scalar (`|` or `>`) parses inconsistently across the tooling.
The sample prompt is one short executable research question. Put report sections, filenames,
infographics, validation, and execution requirements in `starting_task.deliverables` and the body.
Categories: `data_analysis` `data_discovery` `drug_discovery` `epigenomics` `experimental_design`
`functional_analysis` `functional_genomics` `general` `genomics_genetics` `integration` `literature`
`molecular_design` `multi_omics` `pathway_analysis` `proteomics_metabolomics` `reporting`
`transcriptomics`

**Six archetypes. Say which out loud before writing a line.**

| Archetype | Shape | Typical audience |
|---|---|---|
| **analysis-workflow** | Takes data in, runs an analysis, writes deliverables out | user-facing |
| **evidence-synthesis** | Searches or synthesizes literature or other evidence | user-facing |
| **protocol-workflow** | Designs or adapts a laboratory/computational protocol | user-facing |
| **correctness-guidance** | Teaches how to read a result correctly | user-facing |
| **format-utility** | Renders a format on behalf of other skills | decide explicitly |
| **meta-tooling** | Operates on skills or on the platform itself | decide explicitly |

Set `deliverable_policy.audience`, require `report.required: true`, and decide
`infographic.required` explicitly. Require facts only for applicable evidence-bearing claims; never infer applicability from archetype.
Declare the archetype and evidence contract in the generated SKILL.md:

```markdown
<!-- archetype: analysis-workflow -->
<!-- contract: evidence-v1 -->
```

`check_skill.py` re-derives the archetype from the finished package and fails on a mismatch.

---
## The deliverable decision has no implicit exceptions

Every skill produces the report below. Decide infographic applicability before scaffolding; if it is
not applicable, contract, instructions, and receipt carry the same reason.

### The two paths point in opposite directions. That is deliberate.

| Object | Who writes it, when | Where | Never |
|---|---|---|---|
| The **skill package** you are authoring now | you, today | `/mnt/results/skills/<slug>/` | never `/mnt/results/<slug>/` |
| The **report** the finished skill produces | your skill, later | results **root**, named `report_<slug>.pdf` | never a subfolder |
| Data tables and intermediates | your skill, later | `data/`, `figures/`, `tables/` | not the root |

The package sits under `skills/`; the report sits at the results root. **Neither rule implies the other.**

**Exception you cannot avoid:** `GenerateImage` strips directory components silently and always writes
to the results root. Do not pass it a path and do not fight it — schematics land at the root.

In the generated skill's `## Outputs`, write the **filename only**. Outputs is visible in the Skills
Hub, and platform instructions prohibit surfacing internal paths such as `/mnt/results/` there.

### For every generated skill, copy these two blocks. Do not compose them.

```markdown
- `report_<slug>.pdf` — Generate the PDF report with `pdf-report-generation` by default. When the user explicitly selects a compatible report-styling skill, use that provider instead for presentation only; keep every report, evidence, artifact, infographic, and review requirement unchanged. Include a Biomni GenerateImage infographic when required, task context, methods or sources, results, conclusions, figures where applicable, references, and next steps
```

```markdown
### Step N — Final report (MANDATORY TERMINAL STEP)
**The run is not complete until this step has produced `report_<slug>.pdf` at the results root.**
Generate the PDF report with `pdf-report-generation` by default. When the user explicitly selects a compatible report-styling skill, use that provider instead for presentation only; keep every report, evidence, artifact, infographic, and review requirement unchanged. Include a Biomni GenerateImage infographic when required, task context, methods or sources, results, conclusions, figures where applicable, references, and next steps
```

Render to a fresh workspace file, then call `staged_copy(workspace_report_file,
"report_<slug>.pdf")`; never reopen a PDF directly on the object-backed results mount.

One unwrapped line each. **Do not reflow to fit a margin** — a wrapped copy cannot be found by a
single-line search, so nothing downstream can confirm the rule was followed. **Do not make it
conditional.** Phrasing like *"when the user requests a PDF report"* turns the deliverable into a
judgement call, and the run then finishes with nothing. The scaffolder writes both blocks for you;
composing them by hand is where this goes wrong.

**The sentence is authoring-time intent, not evidence of a correctly styled report.** The generated skill derives any override from an affirmative user message in the immutable execution transcript; a caller-supplied provider slug cannot authorize it. New explicit-only providers declare their user-facing selection aliases and PDF markers in `report_style.json` under their assets directory. Existing installed `*-styling` providers remain compatible through a conservative derivation from their own slug and bounded brand-palette section. Neither source is a theme recipe. With no validated override, the system-root contract default remains authoritative; no styling request is not ambiguity and must not trigger a styling clarification. Follow the resolved provider's full instructions and assets.
A missing or ambiguous installed provider source blocks the run—never recreate one from a workspace or sample receipt.
The finished PDF is checked against the provider's resolved, hashed source. The receipt records
`report_style_verified`; `check_skill.py` requires the style-aware receipt gate.
**Infographic honesty gate.** A plotted schematic is not a `GenerateImage` infographic. Join the exact
tool call to its result by immutable ID, match its filename, and bind its decoded pixels to image 1 on
page 1 of the combined PDF. Missing trace IDs or extractors fail closed. See
`references/report-and-integrity.md`.
### One figure per analysis, declared not hoped

An analysis step whose result has no figure is a number the reader has to take on trust. So **every
numbered analysis step names one figure that shows its result**, and the skill declares them in a
manifest rather than leaving it to the runtime agent to remember.

Give the generated skill a `## Figures` section, one row per analysis step:

```markdown
## Figures

| Step | File | What it must make visible |
|---|---|---|
| 2 | `figures/figure_2_qc_depth.png` | Per-sample depth before and after filtering, so a dropped sample is obvious |
| 3 | `figures/figure_3_volcano.png` | Effect size against significance, with the called hits labelled |
```

Rules that make this hold up:

- **Representative, not decorative** — it shows *this run's actual result*. A schematic or an
  illustrative example does not count. If a step cannot be plotted, put the reason in its row.
- **A caption states what the figure shows, not what it is called.** "Volcano plot" is a title;
  "3 of 412 genes pass at padj < 0.05, all in one direction" is a caption.
- **Figures live in `figures/`.** Only the report and `GenerateImage` schematics go to the root.
- **The report derives its figure list from `report_facts.json`, never from memory.** Write a `figures`
  array — file, caption, step — and let the report read it. A report that lists figures by reading the
  manifest cannot claim one it never produced, or silently drop one.
- **Check them before the facts file is written**, so a blank figure stops the run rather than reaching
  the report:

```python
from report_qc import assert_figures, write_facts
figures = assert_figures("figures/manifest.json")   # each exists, none blank, all captioned
write_facts("report_facts.json", {"summary": facts_summary, "figures": figures})
```

Soft by design: the gate warns when there are more analysis steps than declared figures and never blocks
on it, because some steps genuinely have nothing to plot. But the default is a figure, and the burden is
on the absence.

---

## The loop

Archetype → mine the conversation → **interview** → write → integrity bar → **run it once** → evals → gate.

Two are non-negotiable: **the interview cannot be silently skipped** (see auto-progress below), and
**the gate cannot be self-reported** — you run `check_skill.py`, you do not assert compliance.

---

## Step 1 — Mine the conversation, then scaffold

Extract before you ask: tools actually invoked, corrections the user made, real file paths and column
names, thresholds that were argued about. Every question the transcript already answered spends the
author's patience on nothing; every question you skip because you guessed produces a template.

Then check for reuse: load the three nearest existing skills with `Skill` and ask the author what is
different about theirs. Read a resource's real schema before depending on it — never invent a tool, a
package, an API or a result. Use `ToolSearch` only to load a deferred tool the active system prompt
already lists.

Then scaffold. **Do not copy an existing skill as your template — the scaffolder is the template.** A
skill you copy may carry a defect you cannot see from the outside, and you inherit it silently. The
scaffolder cannot.

What it gives you is a frame and an honest list of gaps, **not a shippable package**. It writes what
follows from the contract and refuses to guess the rest, so expect it to block on first check. There is
a second pass once the workflow has real steps:

```bash
python3 "$SKILL_DIR/scripts/scaffold_skill.py" --figures-from-steps <package-dir>
```

which derives one figure row per analysing step — steps that load, plot, export or write the report do
not get a figure of their own.

---
## Step 2 — The interview

**There are two interviews and the first produces the second.** This one is you ↔ the author, once. The
other is the `## Clarification Questions` the generated skill asks its users at runtime. Q1's answer
*is* runtime question #1.

Ask all seven in **one batched turn**, each with a **proposed default the author can simply accept**.
Record every answer with its `decision_source`: `author-confirmed` or `agent-default`.

| Q | Question | Proposed default | Becomes |
|---|---|---|---|
| **Q1** | **Name one concrete subject/input and write the short research question a user should click** — a real file/schema, dataset, exact literature question, material/scale, or object. Is there a verified demo? *(ASK FIRST)* | none — this one has no default | `## Inputs`; `starting_task`; `starting_task.user_prompt`; runtime question #1 |
| **Q2** | **A competent practitioner new to this problem starts the task. What do they do that you would reject in review?** | none — this one has no default | `## Why X, not Y (READ FIRST)` |
| **Q3** | **What result would look like a hit and be an artifact?** What makes you distrust a number here? Is there a control that should come out negative? | the archetype's generic artifact list, marked as generic | `## Scientific caveats`; the QC gates |
| **Q4** | **Where is the line between "validated" and "a hypothesis", and what number decides?** | cap the skill at *hypothesis-generating*; claim no validated tier | the tier gate; the overclaim guard |
| **Q5** | **Who reads the output and what do they do next?** Which single number or table do they need — and **for each analysis step, which figure would they need in order to believe its result?** | a bench scientist; results table + report; one figure per analysis step | `## Outputs`; `## Figures`; report content |
| **Q6** | **Every data source and package — where from, what terms?** | permissive-only; anything unclear becomes a blocker | `DATA_SOURCES.md`; the licence gate |
| **Q7** | **What have you already written for this?** | nothing; write fresh scripts | `## Existing materials`; reuse vs author-fresh. **Not the workflow steps** — it is an inventory of assets, not a procedure |

**Q1 and Q2 have no default and cannot be auto-filled.** Q1 fixes the actual subject; a guess there
produces a skill for data, evidence, or material that does not exist. Q2 is the one fact no script can synthesise and no
amount of reading the task will reveal — it lives only in the head of someone who has reviewed this
analysis and rejected a version of it.

**The yield rule.** Q2, Q3 and Q4 *are* the interview. A one-sentence answer to Q2 produces a generic
caveat, and a generic caveat is template-filling wearing the right heading. Probe once, specifically:
*"you said batch effects — which batch variable, in which dataset, and how would I see it in the
output?"*

**The stop rule.** If after two probes the author cannot answer Q2, the skill may not be ready to exist.
Say so. *"This workflow has no wrong default that I know of"* is a legitimate answer — it means you write
a shorter, more cautious skill. It is **not** permission to invent one.

### Auto-progress

If the author says *go ahead without me*, proceed — but produce a **cautious** skill, not a confident
one, and make every unconfirmed choice visible:

1. **Q1 and Q2 still block.** Ask for the concrete subject/input and wrong-default answer, and wait. Everything
   else can be defaulted.
2. Take the proposed default for Q3–Q7 and record each as `decision_source: agent-default`.
3. **Pin every parameter you chose.** Do not leave a threshold to the runtime agent's judgement. A
   result that swings on a parameter nobody committed to cannot be reproduced or defended, and this is
   the mode that produces those.
4. The generated SKILL.md carries a **`## Unconfirmed design choices`** section listing every
   `agent-default` with the question it answers and what would change if it is wrong.
5. **No validated tier.** With Q4 unconfirmed the skill may claim at most *hypothesis-generating*.
   `check_skill.py` fails a package that claims validation on an unconfirmed Q4.

An auto-progressed skill is a real skill with its assumptions on the label. It is not a draft, and it is
not to be presented as author-approved.

### Bio-correctness interrogation

Fold these into the Q3 probes — they are where generic caveats become real ones. Genome build (GRCh37
vs GRCh38), strand, symbol → Ensembl → UniProt mapping, deprecated symbols, hallucinated PMIDs and
accessions, multiple-testing correction, batch structure. Ask whether the skill checks its outputs.

**Never invent a tool, package, API, dataset, protocol fact, citation, or result.** Read its exact
schema or primary source. Resolve every emitted resource identifier and compare independent identity
fields such as title/year or name/version. A real PMID attached to the wrong DOI must fail. Add a
swapped-valid-identifier fixture that proves exclusion or `not_computable` before facts are written.
Prefer existing Biomni capabilities; use external packages when they are the better choice.
---
## Step 3 — Write the package

**A fresh scaffold does not pass the gate, and it is not meant to.** It is a frame plus a list of what
you still owe: `check_skill.py` blocks on the `FIGURES` and `OUTPUTS` markers and their three
corresponding rules (`FG001`, `OP001`, `TF001`), because these are things no
scaffolder can supply. Leave them until they are answered — that is the point.

**An interview answer is raw material, not a runtime instruction.** Some sections are the author's own
description and go in as written. Others are *executable*, and there the answer is input to be
transformed, not text to interpolate: a sentence that reads like a procedure and cannot be run passes
every structural rule. So the scaffolder derives what follows from the contract, and blocks the rest.

| Section | From | |
|---|---|---|
| `## When to Use This Skill` | Q5 | written as answered |
| `## Why X, not Y (READ FIRST)` | **Q2** | written as answered |
| `## Inputs` | Q1 | written as answered |
| `## Scientific caveats` | **Q3** | written as answered; CV001 checks each is bound to a field |
| `## Evidence Tier` | Q4 | written as answered; the tier gate checks the claim |
| `## Data Sources & Licenses` | Q6 | written as answered; **LC001** warns on a blanket claim naming nothing |
| `## Existing materials` | Q7 | provenance only — it decides reuse vs author-fresh |
| the root report line or explicit N/A reason | `deliverable_policy` | derived; strict only when applicable |
| facts/provenance, figures, inference | `skill_contract.json` applicability | required only where applicable |
| source/resource identity | `skill_contract.json:resource_identity` | identifier + independent metadata; mismatch fixture required |
| **numbered workflow steps** | *nobody* | **blocked** — write the procedure, the call it makes, the file it writes |
| **the machine-readable output** | *nobody* | **blocked** — name the table; **OP001** requires one |
| **`## Figures`** | the steps, once they exist | **blocked** — then `--figures-from-steps` derives a row each |

Q7 does **not** become the workflow. It asks what you have already written, which is an inventory of
assets: pasted into a step it produces *"Step 2 — Run the analysis. Nothing written yet."* Use it to
decide whether `scripts/` reuses existing code or is authored fresh, and write the step yourself.

**Freedom levels** decide where content goes: fragile and consistency-critical → `scripts/` with the
exact invocation; adapt-to-context → `references/`; several valid approaches → prose in SKILL.md.

**Paths and writes.** Relative for **reading** bundled files; absolute for **writing** deliverables —
the working directory is `/workspace`, so a relative output path puts the deliverable where the user
never sees it. Never `setwd()`. `/mnt/skills` is read-only: a skill can never write into its own
package. Write `.h5`/`.h5ad`/`.xlsx`/`.pptx`/`.db` to `/workspace` first, then shell-`cp` — and in R
never `file.copy()` onto the results mount, which yields 0-byte files.

**Description budget.** Under 500 characters, routing verbs in the first 200. Runtime catalogs may
truncate the end, and a description cut off mid-sentence may never trigger the skill at all.

**Stray files.** The entire package tree is published and mounted, including `assets/eval/`; never assume an eval is hidden or ship lockfiles, `pyproject.toml`, virtualenvs, or `node_modules/`.
**Runtime evals do not parse catalog frontmatter.** Validate `starting-prompt` statically before upload. Packaged tests read `skill_contract.json:starting_task.user_prompt` and call the real input router because Personal Skill mounts can canonicalize or omit valid catalog frontmatter.
Keep catalog-shape checks in local/CI package validation and behavior checks in the mounted runtime suite; reparsing mounted `SKILL.md` can fail without testing routing or science.

**Delegate assembly; do not restate styling.** PNG+SVG, `svglite`, ComplexHeatmap, `media_output_check`
per figure and the staging rule are already mandatory in the Biomni system prompt — do not copy them
into a generated skill. Describe what a figure must *communicate*, not its theme. PDF assembly uses
the contract default unless the user explicitly selects a compatible report-styling provider; Word
and PowerPoint continue through their format-specific generation or styling skills.

---

## Step 4 — The integrity bar

Four rules. Each exists because the alternative shipped.

1. **Every number in the deliverable is read from an artifact on disk at build time** — never from
   conversation memory or a session summary. This is the difference between fixing a wrong value and
   making it impossible: a report that prints its count by *reading the table* cannot disagree with the
   table. Mechanism: a facts artifact carrying the headline numbers **and pre-formatted sentences**,
   quoted verbatim by the renderer. This is rare, and it is the highest-value habit on this list.
2. **Hard gates run before the facts artifact is written**, so a failing run never produces numbers a
   report could quote.
3. **A gate's expectation comes from a source the run cannot write.** A threshold read from the run's
   own output is not a gate — one report silently dropped from five figures to one and passed with zero
   failures. A filter that excludes nothing is the same defect.
4. **A caveat is only real if the run computes whether it applies.** Each caveat = the claim + the
   number that makes it concrete + the artifact field that says whether it fired. An unbound caveat is
   prose; a bound caveat is a gate. `check_skill.py` enforces this.

**If a spec item does not match the code you find, report it rather than inventing a substitute.**

---

## Step 5 — Run it once before you ship it

A package can be perfectly well-formed — valid frontmatter, plausible prose, every section present —
and still instruct the agent to run a file that is not in the package. Structural checks pass it. The
only thing that catches it is running the skill.

Using the concrete example from `starting_task`, execute the generated skill's own numbered steps. The
run's terminal step then writes the receipt itself — **you do not author this file**:

```python
from report_qc import record_pdf_review, run_bundled, write_receipt
run_bundled(command_argv, "scripts/scaffold_skill.py", generated_output_paths, invocation_id="primary")
record_pdf_review(report_name, extracted_text_file, rendered_page_files, reviewed_page_numbers,
                  visual_review_notes, visual_review_verdict, visual_review_issues)
write_receipt(report_name="report_<slug>.pdf", figures=figures,
              bundled_files=["scripts/scaffold_skill.py"], outputs=["skill_contract.json"],
              infographics=["infographic.png"], qc_run_log="qc_run_log.json")
```

`write_receipt` runs the gates and records what each one returned, with the artifact it was decided
from — a resolved path and byte count, the selected provider markers read out of the PDF, and command/output
hashes captured by `report_qc` itself. It writes to the **results root** (once the skill is installed its own directory is
read-only) and raises *after* writing, so a failed run leaves the diagnostic behind. Copy that file
into the draft package as the creation receipt before you package it.

The evidence-v1 receipt derives execution and artifact hashes from a QC-owned subprocess log. It records:

- `execution_contract_satisfied` — each applicable bundled-file hash matches a successful
  `run_bundled` event, or the contract carries a checked non-applicability reason
- `outputs_appeared` — every promised output appeared beneath the results root; `execution.command_output_paths` also match successful command events
- `report_at_results_root` — the report landed at the results root under its declared name
- `figure_contract_satisfied` — every applicable figure was produced, is not blank, and carries a
  caption; otherwise the contract records `not_applicable` with a reason
- `report_style_verified` — the finished PDF carries the required and supporting markers declared by the resolved default or explicitly selected report-style provider; an override also records the source transcript and user-message hashes without copying the message text
- `text_extracted`, `pages_rendered`, `visual_review_attested` — separate outcomes; the final item is
  explicitly an author/agent attestation and is not presented as machine verification
- `report_sections_present` — extracted PDF text contains the five required headings once, in order
- `source_assertions_verified` — computation-critical assertions match their runtime witnesses
- `facts_artifact_verified` — `report_facts.json` matches the current payload and validated figures, then passes its semantic contract
- `infographic_lineage_verified` — same-ID `GenerateImage` pixels are image 1 on page 1 of the PDF

…plus one field that is **not** a boolean, because it has four answers:

- `figures_embedded` — `pass` | `fail` | `not_evaluable` | `not_applicable`.

**Why that is separate.** The two are checked to different strengths. That the figures exist and are
not blank is proved with the standard library, so it is always evaluable. Whether they reached the
report needs `pypdf`, which is pinned on Biomni but may be absent in a local authoring environment.
A raw PDF byte scan is diagnostic only and never counts as proof. One boolean used to answer for both, and returned `true`
when embedding had never been evaluated at all — the receipt claimed more than it had checked.
`fail` blocks; `not_evaluable` is reported and does not, because a receipt nobody can obtain is a
rule somebody deletes. See `references/report-and-integrity.md` for bounded repair stop rules.

And be precise about what `figures_embedded: pass` means: it counts ordinary result figures against
the declared figure count; it does not match their identities. The infographic is stronger and
separate: `infographic_lineage_verified` matches the exact generated pixels and page-one draw order.

The figure-contract outcome catches a skill that should plot a result but shows the reader nothing.
It comes back `false` with a `figure_contract_satisfied_reason`; when figures genuinely do not apply,
the separate embedding verdict is `not_applicable`, never a flattering `pass`.

**Why this is a function call and not a checklist.** The receipt used to be five booleans the agent
wrote about itself, printed as a copy-pasteable all-true block in the very step that asked for one —
so pasting it was the cheapest way to pass the shipping gate, and a run could record every outcome
green over an unbranded PDF that no gate had read. Legacy receipt v1 can verify only existence; a
pre-style evidence-v1 package uses receipt v2, and a provider-aware package uses receipt v3. Both match the bundled-file hash to a subprocess result
recorded by the QC helper. An agent that controls every in-band artifact can still forge one, so describe this as
traceability rather than proof against a hostile author.

`report_style_verified` is a claim about the artifact, not about the process. Neither "used
ReportLab" nor "loaded the styling skill" is evidence. The generic gate prefers a provider-owned
versioned `report_style.json` under its assets directory. When an existing installed provider has none, it derives a
primary marker and independent supporting markers only from that provider's bounded brand-palette
section in `SKILL.md`. For an explicit-only provider, the gate separately derives authorization from provider-owned or slug-derived aliases in immutable user messages; customer context and caller variables do not count. The QC helper contains no customer names or brand colors, and a future provider following the generic profile contract needs no creator change.

Before relying on a new provider, run `python3 scripts/validate_style_provider.py <provider-dir> --activation explicit_only` (or `--activation default`). Treat any ambiguity as a provider-authoring failure; do not add its customer name or colors to the creator.

`check_skill.py --require-run-receipt` fails if the receipt is missing, unreadable, not a JSON object,
omits any required receipt key, or gives an applicable outcome anything but boolean `true`. Only
contract-authorized report/infographic outcomes may be `not_applicable`, with evidence and a reason;
`"true"` and `1` are not proof. Any other boolean in the file must also be true. It also fails a receipt that is not
`write_receipt`'s output: no schema marker, or an outcome recorded true with nothing under
`evidence`. That is RR002, and it is what stops a pasted block from passing.
---
## Step 6 — Evals: one test per defect that actually happened

Guard a defect that shipped, not a demo that the code runs. Use the verbatim strings from the bad
output. Assert the correct value is **present** *and* the specific wrong value is **absent**. Exit 0
pass / 1 fail / **2 = all skipped, which is a failure**.

**The discrimination rule:** if your test passes both before and after the fix, it is not a test. Break
the code on purpose and watch it fail before you keep it.

Write root `eval.yaml` from `skill_contract.json`: its first prompt is the sample research question,
outputs are concrete filenames, and invariants reflect applicable deliverables. `SKILL.md`, `eval.yaml`,
and `DATA_SOURCES.md` are derived: repair the contract, regenerate all three, and rerun the full gate.

---

## Step 7 — The gate. No pass, no create.

```bash
python3 "$SKILL_DIR/scripts/check_skill.py" <package-dir> --contract A|B --require-run-receipt
# 0 pass · 1 blocking · 2 warnings only · 3 a check degraded (never a pass)
```

Fix every FAIL. Read every WARN and either fix it or say in the conversation why not. Re-run — any edit
invalidates the previous result. An exit 0 permits review; it does not register a Personal Skill.
Move through `generated`, `structurally_valid`, `evidence_validated`, and `user_validated` explicitly.
The creation task may advance only through `evidence_validated`. `user_validated` requires a separate child run in which the user—not auto mode, the authoring agent, or a plan approval—selected a meaningful clarification branch; record its task ID and selection source.
`installable` means quality-eligible, not user-approved or saved. After showing the package and run,
offer private preview/save and wait for explicit confirmation; store that decision outside the package.

On a pass the checker prints:

> **GATE PASSED — the package is well-formed. This says nothing about whether the science is right.**
> Not checked: whether the analysis suits this biology; whether the skill will trigger; whether a
> threshold is defensible; whether a caveat is true; whether the report's prose is honest. Those need
> the author and a run.

Show the author the finished package **and the run from Step 5** before offering an opinion on it.

---

## Reference map

This map lists only files that exist. If a reference is not here, it has not been written yet — do
not guess at its contents, and do not cite it.

| Load when | Where |
|---|---|
| Starting the interview, or an answer came back thin | `references/interview.md` |
| Writing the generated SKILL.md, section by section | `references/generated-skill-anatomy.md` |
| The deliverable makes numeric claims | `references/report-and-integrity.md` |
| A dependency, database or dataset is proposed | `references/licensing.md` |
| Writing tests for the generated skill | `references/evals.md` |
| Filling applicability, evidence, receipt, or maturity fields | `references/evidence-and-maturity.md` |
| A check fails and you don't understand the rule | `check_skill.py --explain <rule-id>` |
| Before packaging or uploading a generated skill | `check_skill.py <package> --contract A\|B` |

Each reference opens with **Load when / Skip if / What this will not tell you**, so you can abandon one
in ten lines instead of two hundred.
