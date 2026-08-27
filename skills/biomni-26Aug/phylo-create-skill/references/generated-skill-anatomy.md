# Anatomy of a generated skill

**Load when** you are writing the generated SKILL.md and want to know what belongs in each section.
**Skip if** the scaffolder already produced the section and the interview answer filled it — this file
adds nothing over an answered question.
**What this will not tell you** what to write. There is deliberately no copy-paste skeleton here: a
skeleton in reach is the single most reliable way to produce a skill that looks authored and is not.
Every section below is a spec plus the failure it prevents.

---

## The rule that governs all of it

**A section with no interview answer behind it does not get written.** Leave the scaffolder's marker in
place; the gate fails on it. That failure is the feature — it makes an unanswered question visible from
outside, which is the one thing a plausible paragraph never is.

Deleting the marker and writing something reasonable is the failure mode this whole package exists to
prevent. It is undetectable from the artifact. Only you know.

---

## Sections, in order

### `## When to Use This Skill`
**From** Q5. **Purpose** routing for a human reading the catalog, and scope for the agent.
State what it does and, explicitly, what it does **not** do. The "does not" line prevents the
commonest scope failure: a skill quietly attempting an adjacent analysis it was never designed for and
reporting the result with the same confidence.

### `## Why X, not Y (READ FIRST)`
**From** Q2. **Purpose** the skill's reason to exist.
The wrong-but-obvious approach, why a competent person reaches for it, and what it does to the result.
Name both sides in the heading so it survives skimming. This is the rarest section in any skill and the
one that most often makes the difference between a correct answer and a plausible one — if the interview
produced nothing here, write a shorter skill rather than a vaguer section.

### `## Inputs`
**From** Q1. **Purpose** so the agent recognises the right file and rejects the wrong one.
Formats, the actual column or field names, expected size range, and what "missing" means. Include the
misreadable details the author named — units, coordinate base, whether a value is already logged.
State what the skill should do when the input does not match, rather than leaving it to improvise.

### `## Outputs`
**From** Q5 plus the two mandated report rules. **Purpose** the contract the run is judged against.
Every file the skill produces, by name, with one line on what it is for. Filenames only — not internal
paths. Whenever they apply, this includes the report, machine-readable result, and canonical facts
artifact regardless of archetype. Runtime intermediates such as the figure manifest and facts payload
are named in their workflow phases. Anything promised here and not produced is a defect the run
receipt will catch.

### `## Figures`
**From** Q5 probe 3. **Purpose** so the reader can see each result rather than trust it.
One row per result-producing step: step, file under `figures/`, and what the figure must make visible.
A caption states what the figure *shows*, not what it is called — "3 of 412 genes pass at padj < 0.05,
all in one direction" rather than "volcano plot". If a step has nothing to plot, put the reason in its
row. This is a soft rule at authoring time and a real check at run time.

### `## Clarification Questions`
**From** Q1. **Purpose** the runtime interview.
Question 1 is always the concrete subject/input, marked so the agent asks it first, with an explicit
offer of the verified demo, question, material/scale, or example object.

Then the branch that matters: **if the user chose demo data, every remaining question must be
multiple-choice, yes/no, or pick-from-a-list.** Someone trying a skill for the first time cannot answer
open questions about a dataset they have never seen, and cannot be asked to supply properties the demo
data already fixes.

> **Good, for demo data:** "Which comparison? (a) treated vs control — recommended for this dataset,
> (b) all pairwise."
>
> **Bad, for demo data:** "What organism is your data from?" — already known. "Describe your research
> question." — unanswerable by someone exploring. "Which genes are you interested in?" — they have not
> seen the data yet.

When the user brings their own data, open questions are fine; they know their data.

### `## Standard Workflow`
**From** Q7, with freedom levels. **Purpose** the actual procedure. **Every archetype gets one** — a
format-utility has an order of operations too, and an implicit order is one a runtime agent may reorder.
One to three lines of invocation per step, not a wall of inline code. Fragile or consistency-critical
work goes into a script and the step calls it; a step that inlines the logic invites the runtime agent
to rewrite it from memory. Each step states what success looks like, so a silent failure is visible.
Keep the scaffolder's `**Step N — title**` form: the figure-per-step check finds steps by matching it,
so a step written any other way is invisible to it.

Packaged runtime evals should read the canonical sample request from
`skill_contract.json:starting_task.user_prompt` and exercise the real request router. Do not make a
runtime eval reparse `SKILL.md` frontmatter: catalog metadata is checked locally before upload and may
be canonicalized or omitted in a Personal Skill mount. A mounted eval must test executable behavior,
not whether the platform preserved an authoring-time metadata representation.

Every archetype carries terminal deliverable/QC/receipt instructions. Report-producing skills carry
the five report sections defined in `report-and-integrity.md`; a non-report helper records why those
checks are not applicable. Figure and facts phases are controlled by their contract applicability,
not by archetype. Gates run **before** an applicable facts artifact is written, so a failing run never
produces numbers a report could quote.

### `## Scientific caveats`
**From** Q3. **Purpose** to stop a true-but-misleading result being read as a finding.
Each caveat: the claim, the number that makes it concrete, and the artifact field recording whether it
fired. Bound caveats are gates; unbound caveats are decoration that makes a report look careful.

### `## Evidence Tier`
**From** Q4. **Purpose** to constrain the strongest sentence the skill may write.
State the tier, the number that decides it, and what language each tier permits. The skill computes its
tier from the run rather than asserting one, so the report's confidence is derived rather than chosen.
If Q4 was never confirmed, the tier is hypothesis-generating and the skill says so.

### `## Data Sources & Licenses`
**From** Q6. **Purpose** so a licence problem surfaces before the work, not after.
Per source: name, type, URI, version, license, commercial status and evidence, inclusion state,
verification reference, and notes. Included sources cannot be unchecked or prohibited. Generate root
`DATA_SOURCES.md` from the same contract; report obligations belong in an applicable report too.

### `## Common Issues`
**From** Q3 and real failures seen while testing. **Purpose** to stop the agent inventing a fix.
A table: symptom, cause, what to do. Only entries you actually hit. An invented troubleshooting table
sends the agent down a path that does not exist.

### `## Suggested Next Steps` and `## Related Skills`
**Purpose** composability.
Only name a skill you have confirmed exists — a pointer to something absent wastes a run and teaches
the agent that the map is unreliable. If the skill hands off an artifact, the producing skill names the
filename *and* the directory, and the consuming skill takes a path parameter rather than assuming one.

### `## Unconfirmed design choices`
**Only when auto-progress was used.** **Purpose** honesty.
Every defaulted answer, the question it answers, and what changes if it is wrong. This section is what
separates a fast skill from a skill pretending to be reviewed.

---

## Per-archetype: what does not apply

**All six get a `## Standard Workflow` and a trace-derived receipt.** Set audience, report,
infographic, facts, figures, and inference applicability explicitly; do not derive an exemption from
archetype alone. Any archetype may omit a control only with the contract's reason.

| Archetype | Omit |
|---|---|
| **analysis-workflow** | nothing when it performs inference; declare inference not applicable for descriptive transformations |
| **evidence-synthesis** | quantitative Figures and inference readiness unless it performs a meta-analysis |
| **protocol-workflow** | quantitative Figures and inference readiness unless it computes an inferential design |
| **correctness-guidance** | quantitative Figures and inference readiness |
| **format-utility** | quantitative Figures, inference readiness, facts, report, or infographic only with machine-readable reasons tied to its composable role |
| **meta-tooling** | quantitative Figures and inference readiness unless it evaluates measured outcomes; report/infographic follow audience, not archetype |

Facts/provenance remain required for evidence-bearing claims; a truly non-evidence-bearing utility
records why they and any omitted user-facing deliverables are not applicable in `skill_contract.json`.

Adding a section because the template had one is the same defect as filling one in from a template.
Absence is a design decision; make it deliberately and the gate will not argue.

---

## Freedom levels

The routing rule for everything not covered above:

| Freedom | When | Where it goes | Signal |
|---|---|---|---|
| **Low** | fragile; must run exactly | `scripts/`, with the exact invocation in the step | "run this, do not rewrite it" |
| **Medium** | adapt to context | `references/`, read on demand | "read this and adapt" |
| **High** | several valid approaches | prose in SKILL.md | "analyse", "check", "consider" |

Getting this wrong in either direction costs: low-freedom work left as prose gets reimplemented from
memory every run, and high-freedom judgement frozen into a script produces confidently wrong output on
the first input that does not fit.

## When a script fails at run time

In descending order of preference: fix the cause and re-run it; edit the script and say what changed;
read it, adapt the approach, and cite what you took; write from scratch only when it is genuinely
impossible, and say why. Never skip straight to writing inline code — the script encodes decisions that
are not visible in its output.
