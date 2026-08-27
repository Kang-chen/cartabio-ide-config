# Evals for this package

Nothing runs these automatically. They exist for whoever edits the package next.

| File | The defect it guards | Run it |
|---|---|---|
| `test_checks_fire.py` | **A gate that cannot fail.** Mutates the legacy Contract A/B and applicable report/receipt checks. | `python3 assets/eval/test_checks_fire.py` |
| `test_evidence_contract.py` | **A generic evidence contract that only works for one domain, or cannot reject its known-bad shapes.** Passes quantitative, literature, protocol and pure-utility fixtures; mutates EV001–EV017, including report applicability. | `python3 assets/eval/test_evidence_contract.py` |
| `test_runtime_wiring.py` | **A contract-applicable facts or figure phase that disappears outside analysis workflows, accepts an external path, or is not represented in the receipt.** Executes all six generated archetypes and mutates the new runtime checks. | `python3 assets/eval/test_runtime_wiring.py` |
| `test_style_selection.py` | **An enterprise report style that can be selected by a caller, tenant context, or non-user transcript entry instead of immutable user evidence; a future provider that needs a creator registry; or a legacy pre-style contract that cannot use receipt v2.** Exercises profile-first discovery, installed-SKILL fallback, transcript selection, revocation, conflicts, receipt provenance, and contract-shape routing. | `python3 assets/eval/test_style_selection.py` |
| `test_report_contract.py` | **A generated skill promises a report but emits loose pages, omits required sections, or substitutes filename coincidence for infographic lineage.** Exercises every archetype, same-ID traces, decoded pixels, and page-one placement. | Run with the pinned report environment: `python assets/eval/test_report_contract.py` |

`test_checks_support.py` holds shared fixtures and mutation helpers; it is imported by the executable
eval modules and is not a standalone suite.

**Dependencies:** the first four suites use only the standard library. `test_report_contract.py` uses
the platform's pinned Pillow, pypdf, and ReportLab environment. Fixtures are generated in a temp
directory and never shipped — `assets/` is published and readable by the agent running a skill, so a
broken `SKILL.md` sitting on disk is something it could copy.

**Exit codes:** `0` all passed · `1` a check did not fire · `2` everything skipped.

**Exit 2 is a failure, not a pass.** A suite that skipped every test because an import failed has told
you nothing, and treating it as green gives you a suite that stays green through any breakage.

## If you add a check to `check_skill.py`

Add a mutant for it here in the same commit, and **watch the new test fail before you keep it.** A test
that passes both before and after the fix is an assertion about your intentions, not about the code.
