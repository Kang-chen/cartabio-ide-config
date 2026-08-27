# Evals

**Load when** you are writing tests for a generated skill, or repairing a skill that broke twice.
**Skip if** the skill has no scripts and no computed output — there is nothing to pin.
**What this will not tell you** whether the skill is scientifically correct. These tests pin behaviour
you have already decided is right. They cannot discover that a whole approach was wrong.

**Nothing runs these automatically.** No pipeline invokes a skill's `assets/eval/`. They exist for the
next person to edit the skill — often you, months later, with no memory of why a threshold sits where it
does. That makes them worth writing and worth keeping short.

---

## One test per defect that actually happened

Not one test per function. A test that guards a defect nobody has ever hit is speculative work that
still has to be maintained.

| Write a test when | Do not write one when |
|---|---|
| A run produced a wrong number and you fixed it | You want to show the code executes |
| A figure came out blank and nobody noticed | The function is "important" |
| The report and the table disagreed | You are aiming at a coverage figure |
| A path was wrong and the step silently skipped | The behaviour has never failed |

Use the **verbatim strings from the bad output**. If the broken report said `34 significant reversers`,
that exact string is what the test asserts is absent.

---

## The discrimination rule

**If your test passes both before and after the fix, it is not a test.**

Break the code on purpose and watch the test fail before you keep it. A test that has never been
observed to fail is an assertion about your intentions, not about the code. This is the single most
common defect in eval suites and it is invisible — a green suite full of tests that cannot fail looks
exactly like a green suite that works.

---

## Assert both directions

Checking that the right answer is present is half a test. The other half is that the *specific wrong*
answer is absent.

```python
text = build_report_text(facts)
assert "33 of 14,208" in text          # the right value is present
assert "34" not in extract_counts(text)  # the known-wrong value cannot come back
```

The engineered-exact-wrong-value pattern is what stops a regression reintroducing the original bug with
a plausible-looking near-miss.

---

## Fixtures

**Generate them in code, in a temp directory. Do not ship broken artifacts.**

A skill's `assets/` is published and readable by the agent running the skill, so a deliberately
malformed `SKILL.md` or corrupt input sitting on disk is something an agent can find and copy. Build
what you need at test time and throw it away:

```python
with tempfile.TemporaryDirectory() as td:
    bad = pathlib.Path(td) / "input.csv"
    bad.write_text("gene_id,padj\nENSG1,not_a_number\n")
    ...
```

And **check your fixture is what you think it is.** A hand-written binary fixture with a bad checksum
tests your error handling rather than the thing you meant to test, and it will pass for the wrong
reason. Assert the fixture's validity before asserting anything about the code.

One fixture carrying every defect you guard against is easier to maintain than one file per test. Add a
separate file for the cases that must *not* fire, so a false-positive regression is visible.

---

## Exit codes

```
0  all tests passed
1  a test failed
2  everything was skipped
```

**Exit 2 is a failure, not a pass.** A suite that skipped every test because an import failed, a
fixture was missing, or a dependency was absent has told you nothing — and if you treat it as green you
have a suite that will stay green through any breakage. Report skipped and passed over the same
denominator: `3/9 passed (6 skipped)` can never be misread as complete.

Tests that need the network should skip cleanly and say so. Tests that need a package the skill declares
should fail, not skip — a missing declared dependency is a real defect.

---

## What a good suite looks like

```
assets/eval/
├── README.md              file → the defect it guards → how to run it → what the exit codes mean
├── test_report_numbers.py the report/table disagreement that shipped once
├── test_figure_guard.py   the blank-figure case, with an engineered exact wrong value
└── fixtures/build.py      every fixture generated in code, with a validity assertion
```

The README is what makes the suite survive its author. One line per file: which defect it exists for.
Without it, the next person cannot tell a load-bearing test from a speculative one and will delete the
wrong one.

Keep them runnable with nothing installed beyond what the skill already declares — a plain
`python3 assets/eval/test_x.py` with a `__main__` block beats a runner that needs a framework the
sandbox may not have.
