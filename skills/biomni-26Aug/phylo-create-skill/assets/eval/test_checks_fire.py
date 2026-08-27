#!/usr/bin/env python3
"""Every check must FAIL on a package that violates it, and PASS on one that does not.

A gate that cannot fail is worse than no gate: it manufactures confidence. This suite mutates a
known-good package one edit at a time and asserts the corresponding rule fires.

    python3 assets/eval/test_checks_fire.py

Exit 0 all passed · 1 a check did not fire · 2 everything skipped · 3 a test could not be evaluated
in this environment. None of 1, 2 and 3 is a pass: a check nobody could run proves nothing.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib

sys.dont_write_bytecode = True  # this suite imports package modules; a stray __pycache__ is PK001

from test_checks_support import (
    CHECK, DESC, FENCE_RE, FIXTURE_REQUIRED, FIXTURE_SUPPORTING, FM_DQ_RE,
    GOOD_RECORD, HERE, IMPORT_RE, MARK, NASTY_DESC, PKG_ROOT, SCAFFOLD, STDLIB, TODO_RE,
    caches, complete, edit,
    expect_degrade, expect_fail, expect_pass, expect_quiet, expect_receipt_fail,
    gate_copy, pdf_bytes, png_bytes, qc_variant, results, rg, run_check, run_gate,
    scaffold, set_fm, stream_obj, under, write_pdf, yaml_unescape,
)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)

        # report_qc reads BIOMNI_RESULTS at import time. Point the default at a directory that is
        # never created, so no test can depend on a real /mnt/results or pass by falling back to it.
        os.environ["BIOMNI_RESULTS"] = str(tmp / "unused_mount_default")

        # --- the control -------------------------------------------------------------------------
        # This used to assert that a scaffolded package passed untouched. It did, and that was the
        # finding: the scaffolder filled the workflow step from the wrong interview question, invented
        # an output filename, and wrote two generic figure rows — so a package could pass all 33 rules
        # while being unrunnable. A fresh scaffold now blocks on exactly the things only the author can
        # supply. Asserted as an exact set rather than "it blocks", because "it blocks" would be
        # satisfied by any new rule firing for any reason.
        base_fresh = scaffold(tmp / "fresh", "de-hit-fresh")
        code, out = run_check(base_fresh)
        markers = sorted(set(TODO_RE.findall((base_fresh / "SKILL.md").read_text(encoding="utf-8"))))
        rules = sorted({ln.split()[1] for ln in out.splitlines()
                        if ln.strip().startswith(("FAIL", "WARN")) and len(ln.split()) > 1})
        want_markers = ["FIGURES", "OUTPUTS"]
        want_rules = ["FG001", "OP001", "TF001"]
        ok = code == 1 and markers == want_markers and rules == want_rules
        results.append(("control: a fresh scaffold blocks on exactly two markers and three rules",
                        "PASS" if ok else "FAIL",
                        f"exit {code}, markers {markers}, rules {rules}"))

        # ...and once those three are answered the package is clean. Both halves matter: the first
        # says the scaffolder does not bless its own guesses, the second that the bar is reachable.
        base = complete(tmp, "de-hit-triage")
        expect_pass("control: the same package is clean once the author answers", base)

        sentence = (PKG_ROOT / "assets" / "contract" / "delegation_sentence.txt") \
            .read_text(encoding="utf-8").strip()

        authoring_guidance = (PKG_ROOT / "SKILL.md").read_text(encoding="utf-8")
        runtime_eval_rule = (
            "Runtime evals do not parse catalog frontmatter",
            "skill_contract.json:starting_task.user_prompt",
            "Keep catalog-shape checks in local/CI package validation",
        )
        missing_runtime_eval_guidance = [
            phrase for phrase in runtime_eval_rule if phrase not in authoring_guidance
        ]
        results.append((
            "authoring guidance separates local frontmatter checks from mounted routing evals",
            "PASS" if not missing_runtime_eval_guidance else "FAIL",
            "rule present" if not missing_runtime_eval_guidance
            else "missing: " + ", ".join(missing_runtime_eval_guidance),
        ))
        generator_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                PKG_ROOT / "scripts" / "check_skill.py",
                PKG_ROOT / "scripts" / "evidence_contract.py",
                PKG_ROOT / "scripts" / "scaffold_skill.py",
                PKG_ROOT / "templates" / "report_qc.py",
            )
        )
        results.append((
            "generator code has no fixed _DELEGATE that can override provider selection",
            "PASS" if "_DELEGATE" not in generator_sources else "FAIL",
            "provider selection remains contract-driven" if "_DELEGATE" not in generator_sources
            else "fixed delegate found",
        ))
        normalized_guidance = " ".join(authoring_guidance.split())
        fast_path_rules = (
            "not read `scripts/*.py`, `templates/report_qc.py`, or eval-suite source before the first attempt",
            "Inspect implementation only when an error is not specific enough",
            "reverse-engineering validators before scaffolding consumes the run budget",
        )
        missing_fast_path = [rule for rule in fast_path_rules if rule not in normalized_guidance]
        results.append((
            "authoring fast path blocks pre-scaffold validator reverse-engineering",
            "PASS" if not missing_fast_path else "FAIL",
            "public CLI first" if not missing_fast_path else "missing fast-path rule",
        ))

        # --- RC001: the sentence must appear exactly twice ---------------------------------------
        m = scaffold(tmp / "m1", "rc001-drop")
        edit(m, f"- `report_rc001_drop.pdf` — {sentence}\n", "- `report_rc001_drop.pdf` — report\n")
        expect_fail("RC001 fires when one occurrence is deleted", m, "RC001")

        m = scaffold(tmp / "m1b", "rc001-extra")
        edit(m, "## Common Issues", f"{sentence}\n\n## Common Issues")
        expect_fail("RC001 fires on a third occurrence", m, "RC001")

        # --- RC001 must NOT fire on a line-wrapped sentence --------------------------------------
        # Normalisation is the only reason the rule is satisfiable in real markdown.
        m = complete(tmp / "m2", "rc001-wrapped")
        words = sentence.split()
        wrapped = " ".join(words[:12]) + "\n" + " ".join(words[12:])
        edit(m, sentence, wrapped)
        expect_pass("RC001 tolerates a line-wrapped sentence", m)

        # --- RC005: no subfolder report path ------------------------------------------------------
        m = scaffold(tmp / "m3", "rc005-subfolder")
        edit(m, "## Common Issues", "Write it to /mnt/results/deliverables/report.pdf\n\n## Common Issues")
        expect_fail("RC005 fires on a subfolder report path", m, "RC005")

        # --- RC007: conditional phrasing ----------------------------------------------------------
        m = scaffold(tmp / "m4", "rc007-conditional")
        edit(m, "## Common Issues", "When the user requests a PDF report, build one.\n\n## Common Issues")
        expect_fail("RC007 fires on conditional report phrasing", m, "RC007")

        # --- TF001: unresolved scaffolder markers ------------------------------------------------
        thin = dict(GOOD_RECORD)
        thin["q2"] = ""
        m = scaffold(tmp / "m5", "tf001-todo", record=thin)
        expect_fail("TF001 fires on an unanswered interview question", m, "TF001")

        # --- ST00x: pasted boilerplate ------------------------------------------------------------
        m = scaffold(tmp / "m6", "st001-boilerplate")
        edit(m, "## Common Issues", "4. Write from Scratch (1%) - only if impossible\n\n## Common Issues")
        expect_fail("ST001 fires on copied boilerplate", m, "ST001")

        # --- BF001: a bundled file that does not exist -------------------------------------------
        m = scaffold(tmp / "m7", "bf001-dangling")
        edit(m, "## Common Issues", "Run `scripts/does_not_exist.py` first.\n\n## Common Issues")
        expect_fail("BF001 fires on a dangling bundled-file reference", m, "BF001")

        # --- FM004: key order --------------------------------------------------------------------
        m = scaffold(tmp / "m8", "fm004-order")
        md = m / "SKILL.md"
        t = md.read_text(encoding="utf-8").splitlines()
        i = next(k for k, ln in enumerate(t) if ln.startswith("name:"))
        j = next(k for k, ln in enumerate(t) if ln.startswith("description:"))
        t[i], t[j] = t[j], t[i]
        md.write_text("\n".join(t), encoding="utf-8")
        expect_fail("FM004 fires on non-canonical key order", m, "FM004")

        # --- FM001: a bad id ---------------------------------------------------------------------
        m = scaffold(tmp / "m9", "fm001-id")
        md = m / "SKILL.md"
        t = md.read_text(encoding="utf-8")
        old_id = next(ln for ln in t.splitlines() if ln.startswith("id:"))
        md.write_text(t.replace(old_id, 'id: "skill_fm001_id"', 1), encoding="utf-8")
        expect_fail("FM001 fires on a malformed id", m, "FM001")

        # --- PK001: a cache artifact in the tree -------------------------------------------------
        m = scaffold(tmp / "m10", "pk001-cache")
        (m / "__pycache__").mkdir(exist_ok=True)
        (m / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        expect_fail("PK001 fires on a cache artifact", m, "PK001")

        # The four names PK001 grew. A local lint or test run drops one of these beside the package,
        # the whole tree is rglobbed and staged to S3, and no platform validator rejects any of them —
        # a real .ruff_cache/ was found in this package during review, which is the only reason the
        # set moved. Written out rather than looped over CACHE_NAMES: a loop shrinks with the constant,
        # so deleting a name would delete the assertion too and leave this row green over the hole.
        # One line per name for the same reason — a single PK001 anywhere in the output would let three
        # of the four be dropped silently. Matched on the reported path exactly, not as a substring,
        # so a nested file could never stand in for the directory that is supposed to fire.
        m = scaffold(tmp / "m10b", "pk001-toolcache")
        left_behind = (".ruff_cache", ".pytest_cache", ".mypy_cache", ".ipynb_checkpoints")
        for name in left_behind:
            (m / name).mkdir(exist_ok=True)
            (m / name / "leftover").write_text("x", encoding="utf-8")
        code, out = run_check(m)
        blocked = {ln.split("cache artifact in tree:")[-1].strip() for ln in out.splitlines()
                   if "PK001" in ln and "cache artifact in tree:" in ln}
        unblocked = [n for n in left_behind if n not in blocked]
        results.append(("PK001 fires on every tool cache a local lint or test run leaves",
                        "PASS" if code == 1 and not unblocked else "FAIL",
                        f"not blocked: {', '.join(unblocked)}" if unblocked
                        else f"all {len(left_behind)} named" if code == 1 else f"exit {code}, not 1"))

        # The scaffolder loads the gate to read RECEIPT_KEYS, and that import writes precisely the
        # artifact PK001 just fired on. `sys.dont_write_bytecode` at the top of this file does not
        # reach a child process, so only the flag scaffold_skill sets before the import protects
        # either tree — this one, and the package it is writing.
        # Apple's system python3 forces sys.pycache_prefix into ~/Library/Caches, where no in-tree
        # cache can appear whatever the code does, and PYTHONPYCACHEPREFIX cannot clear it. So the
        # child gets -X pycache_prefix=, and a control proves a child configured that way really does
        # cache. Without the control this row passes on any code at all, which is the failure mode
        # the whole suite is against.
        cachefree = ["-X", "pycache_prefix="]
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")}
        probe = tmp / "probe"
        probe.mkdir(parents=True, exist_ok=True)
        (probe / "cached.py").write_text("X = 1\n", encoding="utf-8")
        (probe / "importer.py").write_text("import cached\n", encoding="utf-8")
        subprocess.run([sys.executable, *cachefree, str(probe / "importer.py")],
                       capture_output=True, text=True, env=env)

        rec = tmp / "nocache.json"
        rec.write_text(json.dumps(GOOD_RECORD), encoding="utf-8")
        dest = tmp / "nocache"
        wrote = subprocess.run([sys.executable, *cachefree, str(SCAFFOLD), "--slug", "no-cache",
                                "--archetype", "analysis-workflow", "--category", "transcriptomics",
                                "--record", str(rec), "--dest", str(dest), "--description", DESC,
                                "--facts-requirement", "required"],
                               capture_output=True, text=True, env=env)
        left = caches(PKG_ROOT) + caches(dest)
        label = "scaffolding leaves no bytecode cache in either tree"
        if wrote.returncode != 0:
            # A scaffolder that never got as far as loading the gate writes no cache either.
            results.append((label, "FAIL", f"scaffold exited {wrote.returncode}"))
        elif not caches(probe):
            results.append((label, "DEGRADE", "no in-tree cache is possible here — not evaluated"))
        elif left:
            results.append((label, "FAIL", under(tmp, left[0])))
        else:
            results.append((label, "PASS", "nothing written, control cached"))
        for p in left:                    # a FAIL here has written the artifact PK001 blocks on
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()

        # --- FG001: figures are a soft rule, so assert WARN and NOT a block ----------------------
        m = complete(tmp / "m11", "fg001-nofigures")
        md = m / "SKILL.md"
        t = md.read_text(encoding="utf-8")
        start = t.index("## Figures")
        end = t.index("**Step", start)
        md.write_text(t[:start] + t[end:], encoding="utf-8")
        code, out = run_check(m)
        if "FG001" in out and code != 1:
            results.append(("FG001 warns on a missing Figures section, without blocking",
                            "PASS", f"WARN only (exit {code})"))
        elif "FG001" in out:
            results.append(("FG001 warns on a missing Figures section, without blocking",
                            "FAIL", "it blocked — the figure rule is meant to be soft"))
        else:
            results.append(("FG001 warns on a missing Figures section, without blocking",
                            "FAIL", "FG001 did not fire at all"))

        # A figure at the results root instead of figures/ ----------------------------------------
        # The row is written in, not mutated from a scaffolded one: the scaffolder no longer emits
        # figure rows at all — it emits a blocking marker, because at scaffold time there are no steps
        # to derive rows from.
        m = scaffold(tmp / "m12", "fg003-rootfig")
        edit(m, "|---|---|---|", "|---|---|---|\n| 2 | `figure_2_analysis.png` | the QC result |")
        code, out = run_check(m)
        results.append(("FG003 warns on a figure outside figures/",
                        "PASS" if "FG003" in out else "FAIL",
                        "warned" if "FG003" in out else "did not fire"))

        # --- the figure contract is derived from real steps, not guessed at scaffold time ---------
        # This row used to assert the opposite: that a scaffolded package satisfied the figure rules
        # "by construction". It did — with two fixed rows, figure_2_analysis and figure_3_results, for
        # every skill whatever it analysed, and a non-blocking italic placeholder where the figure
        # contract should have been. Raised in review. A fresh scaffold now declares no figures and
        # says so; the second pass derives them once the steps exist.
        # The default workflow has exactly ONE analysing step: load, generate-figures, export and the
        # terminal report all render or move data rather than analyse. So the second pass derives one
        # row — where the old scaffolder hardcoded two, and only passed FG001 because the figure step
        # was miscounted as an analysis step. This row is the arithmetic of that finding.
        one = scaffold(tmp / "m12a", "figures-one-step")
        drv1 = subprocess.run([sys.executable, str(SCAFFOLD), "--figures-from-steps", str(one)],
                              capture_output=True, text=True)
        got1 = re.findall(r"^\| (\d+) \| `figures/figure_",
                          (one / "SKILL.md").read_text(encoding="utf-8"), re.M)
        results.append(("the default workflow has one analysing step, so one figure is derived",
                        "PASS" if drv1.returncode == 0 and got1 == ["2"] else "FAIL",
                        f"exit {drv1.returncode}, rows for step(s) {','.join(got1) or 'none'}"))

        # Two analysing steps derive two rows. Step 3 is retitled rather than a step being inserted:
        # the scan keys on the step NUMBER, so a "Step 2b" is invisible to it and would make this row
        # pass for the wrong reason.
        fs = scaffold(tmp / "m12b", "figures-derived")
        edit(fs, "**Step 3 — Generate and validate figures.**",
             "**Step 3 — Score per-sample residuals.**")
        drv = subprocess.run([sys.executable, str(SCAFFOLD), "--figures-from-steps", str(fs)],
                             capture_output=True, text=True)
        rows = re.findall(r"^\| (\d+) \| `figures/figure_", (fs / "SKILL.md").read_text(
            encoding="utf-8"), re.M)
        results.append(("--figures-from-steps derives one row per analysing step",
                        "PASS" if drv.returncode == 0 and rows == ["2", "3"] else "FAIL",
                        f"exit {drv.returncode}, {len(rows)} row(s): {','.join(rows)}"))

        # And the derived table satisfies the rule that asked for it.
        md_fs = fs / "SKILL.md"
        md_fs.write_text(TODO_RE.sub(
            lambda m: ("what the step makes visible" if m.group(1).startswith("FIGURE")
                       else m.group(0)), md_fs.read_text(encoding="utf-8")), encoding="utf-8")
        code, out = run_check(fs)
        results.append(("the derived table satisfies FG001",
                        "PASS" if "FG001" not in out else "FAIL",
                        "no figure count finding" if "FG001" not in out else "FG001 still fires"))

        # --- OP001: an analysis workflow must name the table it writes ----------------------------
        # The scaffolder used to promise results_<slug>.csv for every package whatever its run
        # produced. The requirement is real, so it is checked; the filename is the author's, because
        # only the run knows its shape. Measured at 4/90 on the shipped fleet before shipping as FAIL.
        m = complete(tmp / "op001a", "op001-notable")
        edit(m, "`results_demo.csv` — one row per sample, with its fitted proportion",
             "`summary.pdf` — a human-readable summary")
        expect_fail("OP001 fires when Outputs names no machine-readable file", m, "OP001")

        # report_facts.json is in every workflow's Outputs by construction, so if it counted, the rule
        # would be satisfied by the contract alone and could never fire.
        m = complete(tmp / "op001b", "op001-factsonly")
        edit(m, "`results_demo.csv` — one row per sample, with its fitted proportion",
             "`notes.md` — what the run did")
        code, out = run_check(m)
        results.append(("OP001 is not satisfied by report_facts.json alone",
                        "PASS" if "OP001" in out else "FAIL",
                        "fired" if "OP001" in out else "report_facts.json counted as the result"))

        for ext in ("tsv", "parquet", "rds"):
            m = complete(tmp / f"op001-{ext}", f"op001-{ext}")
            edit(m, "`results_demo.csv` — one row per sample, with its fitted proportion",
                 f"`results_demo.{ext}` — one row per sample")
            expect_quiet(f"OP001 accepts a .{ext} result table", m, "OP001")

        # --- LC001: a blanket licence claim naming nothing is the assertion, not the check ---------
        # SKILL.md advertised "the licence gate" and no licence rule existed. Review also raised that
        # Q6 becomes a broad claim with nothing verified per dependency. WARN, and 0/90 on the fleet:
        # it is narrow by design, so both directions are pinned here or it could rot unnoticed.
        m = complete(tmp / "lc001a", "lc001-blanket")
        edit(m, "- Ensembl BioMart (current at retrieval): https://www.ensembl.org/biomart/ — license Ensembl terms; commercial use `no_prohibition_found` (Terms URL reviewed at retrieval time); included `true`; verify with `input_manifest.json:annotation`.",
             "Permissive-licensed sources only. Anything with unclear terms is a blocker.")
        code, out = run_check(m)
        results.append(("LC001 warns on a blanket licence claim that names no source",
                        "PASS" if "LC001" in out and code != 1 else "FAIL",
                        f"exit {code}, LC001 {'fired' if 'LC001' in out else 'silent'}"))

        m = complete(tmp / "lc001b", "lc001-named")
        edit(m, "- Ensembl BioMart (current at retrieval): https://www.ensembl.org/biomart/ — license Ensembl terms; commercial use `no_prohibition_found` (Terms URL reviewed at retrieval time); included `true`; verify with `input_manifest.json:annotation`.",
             "Permissive-licensed sources only: GTEx v8 under its open terms, scipy under BSD-3.")
        expect_quiet("LC001 is quiet once the claim names its sources", m, "LC001")

        # The scaffolder's own Q6 default is a blanket claim, so an auto-progressed package trips this
        # — deliberately. An unconfirmed licence assertion is exactly what the rule is for, and it
        # warns rather than blocks, so auto-progress still produces a shippable package.
        auto = complete(tmp / "lc001c", "lc001-auto",
                        record={**{k: GOOD_RECORD[k] for k in ("q1", "q2")},
                                **{k: None for k in ("q3", "q4", "q5", "q6", "q7")}},
                        extra=["--auto-progress"])
        code, out = run_check(auto)
        results.append(("authoring Q6 defaults do not leak into runtime licence claims",
                        "PASS" if "LC001" not in out and code != 1 else "FAIL",
                        f"exit {code}, LC001 {'fired' if 'LC001' in out else 'silent'}"))

        # assert_figures must reject a blank figure and a missing caption -------------------------
        sys.path.insert(0, str(PKG_ROOT / "templates"))
        try:
            import report_qc  # type: ignore

            figure_results = tmp / "figure-results"
            figure_results.mkdir()
            report_qc.RESULTS = figure_results
            figdir = figure_results / "figs"
            figdir.mkdir(parents=True, exist_ok=True)
            blank = figdir / "blank.png"
            blank.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)     # tiny => blank
            good = figdir / "good.png"
            good.write_bytes(png_bytes())

            def expect_raise(label: str, manifest: list) -> None:
                try:
                    report_qc.assert_figures(manifest)
                except report_qc.GateFailure:
                    results.append((label, "PASS", "raised"))
                except Exception as exc:  # noqa: BLE001
                    results.append((label, "FAIL", f"wrong error: {type(exc).__name__}"))
                else:
                    results.append((label, "FAIL", "did not raise"))

            expect_raise("assert_figures rejects a blank figure",
                         [{"step": 2, "file": str(blank), "caption": "x"}])
            expect_raise("assert_figures rejects a missing caption",
                         [{"step": 2, "file": str(good), "caption": "  "}])
            expect_raise("assert_figures rejects an empty manifest", [])
            expect_raise("assert_figures rejects an unexplained absent figure",
                         [{"step": 2, "file": None}])
            try:
                ok = report_qc.assert_figures(
                    [{"step": 2, "file": str(good), "caption": "3 of 412 genes pass"},
                     {"step": 3, "file": None, "reason": "this step emits a table, not a plot"}])
                results.append(("assert_figures accepts a good manifest with a reasoned absence",
                                "PASS" if len(ok) == 2 else "FAIL", f"{len(ok)} entries"))
            except Exception as exc:  # noqa: BLE001
                results.append(("assert_figures accepts a good manifest with a reasoned absence",
                                "FAIL", f"raised {type(exc).__name__}"))

            ok, detail = report_qc.report_embeds_figures(
                tmp / "nonexistent.pdf", [{"file": str(good), "caption": "c"}])
            results.append(("report_embeds_figures degrades to not-evaluable, never a silent pass",
                            "PASS" if not ok else "FAIL", detail[:44]))

            # A missing report must raise, not warn: the whole point is that a run cannot finish
            # successfully with no deliverable.
            resdir = tmp / "fake_results"
            resdir.mkdir(parents=True, exist_ok=True)
            report_qc.RESULTS = resdir
            try:
                report_qc.assert_report_exists("report_absent.pdf")
            except report_qc.GateFailure:
                results.append(("assert_report_exists raises when the report is missing",
                                "PASS", "raised"))
            else:
                results.append(("assert_report_exists raises when the report is missing",
                                "FAIL", "did not raise"))

            (resdir / "report_tiny.pdf").write_bytes(b"%PDF-1.4\n" + b"0" * 200)
            try:
                report_qc.assert_report_exists("report_tiny.pdf")
            except report_qc.GateFailure:
                results.append(("assert_report_exists raises on an implausibly small report",
                                "PASS", "raised"))
            else:
                results.append(("assert_report_exists raises on an implausibly small report",
                                "FAIL", "did not raise"))

            (resdir / "report_ok.pdf").write_bytes(b"%PDF-1.4\n" + b"0" * 30_000)
            try:
                got = report_qc.assert_report_exists("report_ok.pdf")
                ok2 = got.name == "report_ok.pdf"
                results.append(("assert_report_exists accepts a real report",
                                "PASS" if ok2 else "FAIL", got.name))
            except Exception as exc:  # noqa: BLE001
                results.append(("assert_report_exists accepts a real report",
                                "FAIL", f"raised {type(exc).__name__}"))
        finally:
            sys.path.pop(0)

        # RECEIPT_KEYS, ARCHETYPES and yaml_dq are imported, never restated: a test that hardcodes a
        # constant stops tracking the one the code uses, which is how spec and gate drift apart.
        sys.path.insert(0, str(PKG_ROOT / "scripts"))
        try:
            from check_skill import (  # type: ignore
                CACHE_NAMES,
                EMBED_STATES,
                EVIDENCE_RECEIPT_KEYS,
                RECEIPT_KEYS,
                RECEIPT_SCHEMA,
                RECEIPT_SCHEMA_V2,
                RECEIPT_SCHEMA_V3,
            )
            from scaffold_skill import ARCHETYPES, yaml_dq     # type: ignore
        finally:
            sys.path.pop(0)

        def receipt_dict(keys=EVIDENCE_RECEIPT_KEYS, evidence=True,
                         schema=RECEIPT_SCHEMA_V3, **over) -> dict:
            """A receipt shaped the way report_qc.write_receipt writes one.

            Derived from the constants, never spelled out: a sixth outcome or a schema bump must not
            need an edit here. `evidence=False` produces the older hand-written shape — all-true
            booleans and nothing behind them — which is what RR002 now has to reject. Pass a dict to
            control the evidence map itself; an empty entry per key is a claim in the right shape.
            """
            body: dict = {k: True for k in keys}
            body["figures_embedded"] = "pass"     # tri-state, not a boolean — see RR003
            if schema is not None:
                body["schema"] = schema
                body["generated_by"] = "report_qc.write_receipt"
            if isinstance(evidence, dict):
                body["evidence"] = evidence
            elif evidence:
                body["evidence"] = {
                    k: ({"provider": "pdf-report-generation", "activation": "default",
                         "style_source": {
                             "kind": "installed_skill_markdown",
                             "path": "/mnt/skills/system/pdf-report-generation/SKILL.md",
                             "bytes": 512, "sha256": "a" * 64,
                             "derivation_schema": "biomni-report-style-derivation/1",
                             "marker_set_sha256": "b" * 64,
                         },
                         "selection": "contract_default",
                         "contract_default_provider": "pdf-report-generation"}
                        if k == "report_style_verified"
                        else {"path": f"/mnt/results/artifact_{k}", "bytes": 31_337}) for k in keys}
            body.update(over)
            return body
        def receipt_json(**kw) -> str:
            return json.dumps(receipt_dict(**kw))

        expl = subprocess.run([sys.executable, str(CHECK), "--explain", "PK001"],
                              capture_output=True, text=True)
        undocumented = sorted(n for n in CACHE_NAMES if n not in expl.stdout)
        results.append(("PK001's --explain names every cache the rule blocks",
                        "PASS" if expl.returncode == 0 and not undocumented else "FAIL",
                        ", ".join(undocumented) or f"all {len(CACHE_NAMES)} named"))

        # Every id the gate can REPORT must be an id the gate can EXPLAIN. RC007a/RC007b fired for
        # real while `--explain RC007a` answered "no such rule" — the author reads the rule id off a
        # failure line, so an id without an entry is a dead end at exactly the wrong moment. Scanned
        # out of the source because that is where an id is born; a list maintained here would drift
        # the same way the EXPLAIN dict did.
        gate_src = CHECK.read_text(encoding="utf-8")
        emitted = set(re.findall(r'Finding\(\s*"([A-Z]{2}\d{3}[a-z]?)"', gate_src))
        emitted |= set(re.findall(r'^\s*\(\s*"([A-Z]{2}\d{3}[a-z]?)"\s*,', gate_src, re.M))
        # Assert on the text, not the exit status: `--explain` answers "no such rule: X" and still
        # exits 0, so a returncode check here passes while the author reads a dead end.
        unexplained = sorted(
            r for r in emitted
            if "no such rule" in subprocess.run([sys.executable, str(CHECK), "--explain", r],
                                                capture_output=True, text=True).stdout)
        results.append((f"every rule id the gate emits has an --explain entry ({len(emitted)} ids)",
                        "PASS" if emitted and not unexplained else "FAIL",
                        ", ".join(unexplained) or f"all {len(emitted)} explained"))

        # --- RR001: the run receipt must be validated, not merely present ------------------------
        import json as _json
        r = complete(tmp / "rr", "rr-receipt")

        code, out = run_check(r, ["--require-run-receipt"])
        results.append(("RR001 fires when the run receipt is absent",
                        "PASS" if code == 1 and "RR001" in out else "FAIL", f"exit {code}"))

        # a receipt recording a FAILED run must not pass — existence is not evidence
        (r / "run_receipt.json").write_text(_json.dumps({
            "bundled_files_ran": True, "outputs_appeared": True,
            "report_at_results_root": False, "report_at_results_root_reason": "no PDF produced",
            "figures_present_and_nonblank": True}), encoding="utf-8")
        code, out = run_check(r, ["--require-run-receipt"])
        ok = code == 1 and "report_at_results_root is false" in out
        results.append(("RR001 fires on a receipt whose boolean is false",
                        "PASS" if ok else "FAIL", f"exit {code}"))

        # a receipt with no booleans proves nothing
        (r / "run_receipt.json").write_text(_json.dumps({"note": "ran it, seemed fine"}), encoding="utf-8")
        code, out = run_check(r, ["--require-run-receipt"])
        results.append(("RR001 fires on a receipt with no boolean outcomes",
                        "PASS" if code == 1 and "RR001" in out else "FAIL", f"exit {code}"))

        # A receipt write_receipt() would have written passes — built from the constants, because a
        # literal here is the very drift this suite exists to catch: the fifth key blocked this row
        # while the gate was right.
        (r / "run_receipt.json").write_text(receipt_json(), encoding="utf-8")
        code, out = run_check(r, ["--require-run-receipt"])
        results.append(("a complete run receipt passes",
                        "PASS" if code in (0, 2) else "FAIL", f"exit {code}"))

        # RR002: the same five booleans, all true, with nothing behind them. This is what the package
        # used to print as a copy-pasteable block, and it passed — the receipt recorded the run's own
        # opinion of itself. Both halves of the shape are checked separately, because a receipt could
        # plausibly carry one and not the other.
        (r / "run_receipt.json").write_text(receipt_json(evidence=False, schema=None),
                                            encoding="utf-8")
        code, out = run_check(r, ["--require-run-receipt"])
        results.append(("RR002 fires on a hand-written all-true receipt",
                        "PASS" if code == 1 and "RR002" in out else "FAIL", f"exit {code}"))

        (r / "run_receipt.json").write_text(receipt_json(evidence=False), encoding="utf-8")
        code, out = run_check(r, ["--require-run-receipt"])
        results.append(("RR002 fires on a receipt with the marker but no evidence",
                        "PASS" if code == 1 and "no `evidence` map" in out else "FAIL", f"exit {code}"))

        (r / "run_receipt.json").write_text(receipt_json(schema="something-else/9"), encoding="utf-8")
        code, out = run_check(r, ["--require-run-receipt"])
        results.append(("RR002 fires on a foreign schema marker",
                        "PASS" if code == 1 and "RR002" in out else "FAIL", f"exit {code}"))

        # An outcome true with an EMPTY evidence entry is the same claim wearing the right shape.
        (r / "run_receipt.json").write_text(
            receipt_json(evidence={k: {} for k in RECEIPT_KEYS}), encoding="utf-8")
        code, out = run_check(r, ["--require-run-receipt"])
        results.append(("RR002 fires when an outcome's evidence entry is empty",
                        "PASS" if code == 1 and "no evidence under" in out else "FAIL", f"exit {code}"))

        # --- RR003: the embedding claim is separate, and graded ------------------------------------
        # Raised in review: figures_present_and_nonblank answered for both "the figures exist" and
        # "they reached the report", and came back true while embedding had not been evaluated at all.
        # The tri-state says which happened; the three rows below pin each verdict's consequence.
        (r / "run_receipt.json").write_text(receipt_json(figures_embedded="fail"), encoding="utf-8")
        code, out = run_check(r, ["--require-run-receipt"])
        results.append(("RR003 blocks when embedding was checked and disagreed",
                        "PASS" if code == 1 and "RR003" in out else "FAIL", f"exit {code}"))

        # not_evaluable must NOT block: pypdf is absent in plenty of runtimes, and a receipt nobody
        # can obtain is a rule somebody deletes. It still has to be said out loud.
        (r / "run_receipt.json").write_text(receipt_json(figures_embedded="not_evaluable"),
                                            encoding="utf-8")
        code, out = run_check(r, ["--require-run-receipt"])
        ok = code in (0, 2) and "RR003" in out and "not_evaluable" in out
        results.append(("RR003 reports not_evaluable without blocking",
                        "PASS" if ok else "FAIL", f"exit {code}, RR003 {'seen' if 'RR003' in out else 'silent'}"))

        for bad in ("true", True, "maybe", None):
            (r / "run_receipt.json").write_text(receipt_json(figures_embedded=bad), encoding="utf-8")
            code, out = run_check(r, ["--require-run-receipt"])
            if not (code == 1 and "RR003" in out):
                results.append((f"RR003 rejects figures_embedded={bad!r}", "FAIL", f"exit {code}"))
                break
        else:
            results.append(("RR003 rejects a value outside the three states", "PASS", "4/4 blocked"))

        # A receipt with no embedding verdict at all cannot say whether the figures reached the
        # report — which is the state the finding was about, so absence must not read as fine.
        body = receipt_dict()
        body.pop("figures_embedded")
        (r / "run_receipt.json").write_text(json.dumps(body), encoding="utf-8")
        code, out = run_check(r, ["--require-run-receipt"])
        results.append(("RR003 fires when the embedding verdict is absent entirely",
                        "PASS" if code == 1 and "RR003" in out else "FAIL", f"exit {code}"))

        # --- universal report core, without quantitative-only figure rules -----------------------
        g = complete(tmp / "g1", "guide-only", archetype="correctness-guidance")
        expect_pass("guidance archetype is exempt from the report rules", g)
        gt = (g / "SKILL.md").read_text(encoding="utf-8")
        universal = sentence in gt and "MANDATORY TERMINAL STEP" in gt and "## Figures" not in gt
        results.append(("guidance archetype has a PDF but no quantitative figure block",
                        "PASS" if universal else "FAIL",
                        "universal PDF, no figures" if universal else "contract mismatch"))

        # --- auto-progress must disclose every default ------------------------------------------
        bare = {"q1": GOOD_RECORD["q1"], "q2": GOOD_RECORD["q2"],
                **{k: None for k in ("q3", "q4", "q5", "q6", "q7")}}
        a = complete(tmp / "a1", "auto-disclosed", record=bare, extra=["--auto-progress"])
        at = (a / "SKILL.md").read_text(encoding="utf-8")
        ok = "Unconfirmed design choices" in at and "hypothesis-generating" in at
        results.append(("auto-progress discloses defaults and caps the tier",
                        "PASS" if ok else "FAIL", "disclosure present" if ok else "not disclosed"))
        expect_pass("auto-progressed package still passes the gate", a)

        # --- auto-progress must refuse when Q1 or Q2 is missing ----------------------------------
        rec = tmp / "empty.json"
        rec.write_text(json.dumps({**GOOD_RECORD, "q1": "", "q2": ""}), encoding="utf-8")
        p = subprocess.run([sys.executable, str(SCAFFOLD), "--slug", "refuse-me",
                            "--archetype", "analysis-workflow", "--category", "general",
                            "--record", str(rec), "--auto-progress", "--dry-run",
                            "--facts-requirement", "required"],
                           capture_output=True, text=True)
        ok = p.returncode != 0 and "cannot be defaulted" in (p.stdout + p.stderr)
        results.append(("auto-progress refuses to invent Q1/Q2",
                        "PASS" if ok else "FAIL", f"exit {p.returncode}"))

        # --- every module a generated SKILL.md imports must be a file the package ships ----------
        dangling: list[str] = []
        for arch in ("analysis-workflow", "correctness-guidance", "format-utility", "meta-tooling"):
            gen = scaffold(tmp / f"imp-{arch}", f"imports-{arch}", archetype=arch)
            for block in FENCE_RE.findall((gen / "SKILL.md").read_text(encoding="utf-8")):
                for mod in IMPORT_RE.findall(block):
                    if mod not in STDLIB and not (gen / "scripts" / f"{mod}.py").exists():
                        dangling.append(f"{arch}:{mod}")
        results.append(("no generated SKILL.md imports a module the package does not ship",
                        "PASS" if not dangling else "FAIL",
                        "every import resolves" if not dangling
                        else ", ".join(sorted(set(dangling)))))

        # --- BF002: the same scan, now inside the gate -------------------------------------------
        # The suite above only sees what the scaffolder writes. A hand-edited package is what BF002
        # is for, and a guidance package carrying a borrowed report block is the shape that reported
        # GATE PASSED: BF001 matches prefixed paths, and `from report_qc import ...` is not one.
        m = complete(tmp / "bf002a", "bf002-borrowed", archetype="correctness-guidance")
        (m / "scripts" / "report_qc.py").unlink()
        edit(m, "## Common Issues",
             "```python\nfrom report_qc import assert_figures, write_facts\n```\n\n## Common Issues")
        expect_fail("BF002 fires on the exact defect-2 shape", m, "BF002")

        m = complete(tmp / "bf002b", "bf002-plain-import", archetype="format-utility")
        (m / "scripts" / "report_qc.py").unlink()
        edit(m, "## Common Issues", "```python\nimport report_qc\n```\n\n## Common Issues")
        expect_fail("BF002 fires on a plain `import report_qc`, not only the from-form", m, "BF002")

        # Narrow scope is what keeps the rule alive: one that fires on `import json` is deleted
        # within the week, taking the borrowed-block catch with it.
        m = complete(tmp / "bf002c", "bf002-noise", archetype="meta-tooling")
        edit(m, "## Common Issues",
             "```python\nimport json, os\nfrom pathlib import Path\n"
             "import scanpy as sc\nimport pandas as pd\n```\n\n## Common Issues")
        expect_quiet("BF002 stays silent on stdlib and platform packages", m, "BF002")

        # Asserting silence proves nothing unless the same package can be made to speak.
        m = complete(tmp / "bf002d", "bf002-shipped")
        expect_quiet("BF002 stays silent when the module is genuinely shipped", m, "BF002")
        (m / "scripts" / "report_qc.py").unlink()
        expect_fail("BF002 fires once that shipped module is deleted", m, "BF002")

        # This package prescribes the import and ships templates/report_qc.py, never scripts/. A
        # scripts-only existence test would block one of the 90 shipped packages — its own.
        code, out = run_check(PKG_ROOT)
        miss = ("BF002 fired on the authoring package" if "BF002" in out else
                "no longer clean" if code != 0 or "no findings" not in out else "")
        results.append(("BF002 stays silent on phylo-create-skill itself",
                        "PASS" if not miss else "FAIL", miss or "no findings, exit 0"))

        # A bare fence is not evidence an agent will run it as python, and an r block never will.
        m = complete(tmp / "bf002f", "bf002-other-fences", archetype="format-utility")
        edit(m, "## Common Issues",
             "```r\nlibrary(report_qc)\n```\n\n```\nfrom report_qc import x\n```\n\n## Common Issues")
        expect_quiet("BF002 ignores non-python fences", m, "BF002")

        # Figure applicability is explicit. These default non-analysis fixtures declare it false,
        # so they must not borrow the quantitative fixture's figure workflow.
        borrowed: list[str] = []
        for arch in ("correctness-guidance", "evidence-synthesis", "protocol-workflow",
                     "format-utility", "meta-tooling"):
            gen = scaffold(tmp / f"sw-{arch}", f"workflow-{arch}", archetype=arch)
            wf = (gen / "SKILL.md").read_text(encoding="utf-8")
            if "## Standard Workflow" not in wf:
                borrowed.append(f"{arch}: no workflow at all")
            borrowed += [f"{arch}: {b[:26]!r}" for b in
                         ("Generate and validate figures", "## Figures", "assert_figures") if b in wf]
        results.append(("non-analysis Standard Workflow has no quantitative figure machinery",
                        "PASS" if not borrowed else "FAIL",
                        "universal report only" if not borrowed else "; ".join(borrowed)))

        # The non-workflow branch still has to carry Q7, or an unanswered interview passes the gate.
        m = scaffold(tmp / "m13", "tf001-noq7", archetype="format-utility",
                     record={k: v for k, v in GOOD_RECORD.items() if k != "q7"})
        expect_fail("TF001 still fires on an unanswered Q7 in a non-workflow archetype", m, "TF001")

        # --- the reference's per-archetype claims must be claims about the scaffolder -------------
        # Nothing made the doc and the code read each other, so each was free to move alone. These
        # read both: ARCHETYPES comes from the scaffolder, the cells from the reference.
        emitted = {a: (scaffold(tmp / f"anat-{a}", f"anatomy-{a}", archetype=a)
                       / "SKILL.md").read_text(encoding="utf-8") for a in ARCHETYPES}
        noworkflow = sorted(a for a, t in emitted.items() if "## Standard Workflow" not in t)
        results.append(("every archetype's scaffold emits '## Standard Workflow'",
                        "PASS" if not noworkflow else "FAIL",
                        f"all {len(emitted)}" if not noworkflow else ", ".join(noworkflow)))

        # Report QC and receipts are universal; figures remain analysis-specific.
        cluster = ("assert_figures", "## Figures")
        misplaced = [f"{a}: {b!r} {'missing' if a == 'analysis-workflow' else 'borrowed'}"
                     for a, t in sorted(emitted.items()) for b in cluster
                     if (b in t) != (a == "analysis-workflow")]
        results.append(("the quantitative figure cluster is analysis-workflow only",
                        "PASS" if not misplaced else "FAIL",
                        "confined to the workflow" if not misplaced else "; ".join(misplaced)))

        # The receipt block belongs to every archetype because the PDF is universal.
        rcpt = ("run_receipt.json", *EVIDENCE_RECEIPT_KEYS)
        stray = [f"{a}: {b!r} missing"
                 for a, t in sorted(emitted.items()) for b in rcpt
                 if b not in t]
        results.append(("the run-receipt instructions are universal",
                        "PASS" if not stray else "FAIL",
                        "present in every terminal step" if not stray else "; ".join(stray)))

        # RC008 proves the rule fires; this proves there is anything for it to see. It must be a CALL
        # in a python fence — prose about delegating the report is what the unbranded run already had
        # — and on the package's own report name: a hardcoded report_<slug>.pdf would check a file the
        # skill never writes, passing the gate at authoring time and failing at run time.
        # The call is write_receipt(), which runs the brand gate as one of the outcomes it records —
        # so the terminal step names the gate once, through the function that writes the evidence,
        # rather than asserting and then hand-recording the result it got.
        bc = scaffold(tmp / "brandcall", "brand-call", extra=["--report-name", "report_custom.pdf"])
        wired = [b for b in FENCE_RE.findall((bc / "SKILL.md").read_text(encoding="utf-8"))
                 if "write_receipt(" in b and 'report_name="report_custom.pdf"' in b]
        results.append(("the generated terminal step wires the brand gate to its own report name",
                        "PASS" if wired else "FAIL", f"{len(wired)} python fence(s) call it"))

        # Every archetype produces a report, so every terminal step wires the brand gate.
        stray_brand = sorted(a for a, t in emitted.items()
                             if "write_receipt(" not in t)
        results.append(("the brand-gate call is universal",
                        "PASS" if not stray_brand else "FAIL",
                        "present in every terminal step" if not stray_brand
                        else ", ".join(stray_brand)))

        stray_staging = sorted(a for a, t in emitted.items()
                               if not re.search(r'staged_copy\([^,]+, "report_[^"]+\.pdf"\)', t))
        results.append(("workspace-to-results PDF staging is universal",
                        "PASS" if not stray_staging else "FAIL",
                        "present in every terminal step" if not stray_staging
                        else ", ".join(stray_staging)))

        missing_stage = pathlib.Path(shutil.copytree(bc, tmp / "rc010-missing"))
        edit(missing_stage, 'staged_copy(workspace_report_file, "report_custom.pdf")\n', "")
        expect_fail("RC010 fires when the PDF staged-copy call is deleted", missing_stage, "RC010")

        wrong_stage = pathlib.Path(shutil.copytree(bc, tmp / "rc010-wrong"))
        edit(wrong_stage, '"report_custom.pdf")\nrecord_pdf_review',
             '"report_other.pdf")\nrecord_pdf_review')
        expect_fail("RC010 fires when staged_copy publishes a different PDF", wrong_stage, "RC010")

        # A cell is an instruction to the author, so it may not contradict what the scaffolder emits:
        # never the workflow, which every archetype gets, and never a section name nothing emits.
        # Evidence Tier and Scientific caveats belong in the cells — the doc says outright that the
        # scaffolder writes those for all four and the author deletes them, so they are not hits.
        anatomy = (PKG_ROOT / "references" / "generated-skill-anatomy.md").read_text(encoding="utf-8")
        sections = set(re.findall(r"`##\s+([A-Za-z][^`]*?)`", anatomy))
        omit = {mo.group(1): mo.group(2) for mo in
                re.finditer(r"^\|\s*\*\*([a-z-]+)\*\*\s*\|([^|]*)\|", anatomy, re.M)}
        contradicts: list[str] = []
        for arch in sorted(ARCHETYPES):
            cell = omit.get(arch)
            if cell is None:
                contradicts.append(f"{arch}: no row in the per-archetype table")
                continue
            if "## Standard Workflow" in emitted[arch] and re.search(r"workflow|step", cell, re.I):
                contradicts.append(f"{arch}: told to omit {' '.join(cell.split())[:44]!r}")
            for name in sorted(s for s in sections if s in cell):
                if not any(f"## {name}" in t for t in emitted.values()):
                    contradicts.append(f"{arch}: omits {name!r}, which the scaffolder never writes")
        results.append(("the per-archetype table does not contradict the scaffolder",
                        "PASS" if not contradicts else "FAIL",
                        f"{len(omit)} rows agree" if not contradicts else "; ".join(contradicts)))

        # FG001's figure-per-step comparison only discriminates while the scaffolder's step form is
        # the one check_skill scans for. A form the scan cannot see counts zero steps and warns about
        # nothing, so the step COUNT has to be what complains. Start from a complete package — one
        # analysing step, one derived row, clean — then retitle the figure step into a second analysing
        # step. That is a genuine 2-steps-1-figure mismatch without touching the table, and it stays a
        # WARN: nothing static can know which steps have nothing worth plotting.
        m = complete(tmp / "m14", "fg001-stepform")
        edit(m, "**Step 3 — Generate and validate figures.**",
             "**Step 3 — Score the residuals.**")
        code, out = run_check(m)
        miss = ("FG001 silent — the scan sees no steps to compare against" if "FG001" not in out else
                "FG001 fired, but not on the step count" if "analysis step(s) but" not in out else
                "it blocked" if code == 1 else "")
        results.append(("check_skill's step scan matches the form the scaffolder writes",
                        "PASS" if not miss else "FAIL", miss or "step count compared"))

        # --- frontmatter must be YAML a parser accepts, not just what CI's per-line regex tolerates
        nasty_capabilities = json.loads(json.dumps(GOOD_RECORD["capabilities"]))
        nasty_capabilities["entries"][0]["claim"] = NASTY_DESC
        nasty_capabilities["trigger"] = ""
        q = complete(tmp / "yq", "yaml-quoting",
                     record={"capabilities": nasty_capabilities})
        lines = (q / "SKILL.md").read_text(encoding="utf-8").splitlines()
        fm = lines[1:lines.index("---", 1)]
        unparsable = [ln.split(":", 1)[0] for ln in fm if not FM_DQ_RE.fullmatch(ln)]
        parsed = {ln.split(": ", 1)[0]: yaml_unescape(ln.split(": ", 1)[1][1:-1])
                  for ln in fm if FM_DQ_RE.fullmatch(ln)}
        contract = json.loads((q / "skill_contract.json").read_text(encoding="utf-8"))
        task = contract["starting_task"]
        rendered_prompt = task["user_prompt"]
        trips = (parsed.get("description") == " ".join(NASTY_DESC.split())
                 and parsed.get("starting-prompt") == " ".join(rendered_prompt.split()))
        results.append(("frontmatter values are valid double-quoted YAML scalars",
                        "PASS" if not unparsable and trips else "FAIL",
                        "all lines parse and round-trip" if not unparsable and trips
                        else f"unparsable {unparsable}, round-trip {trips}"))

        # A raw control byte is invisible to any line-regex validator and fatal to a real parser.
        control_capabilities = json.loads(json.dumps(GOOD_RECORD["capabilities"]))
        control_capabilities["entries"][0]["claim"] = "escape \x1b and delete \x7f in a description"
        control_capabilities["trigger"] = ""
        c = complete(tmp / "yc", "yaml-control", record={"capabilities": control_capabilities})
        raw = (c / "SKILL.md").read_bytes()
        why = []
        if yaml_dq(" ".join('a\x1bb\tc\nd "e" \\f'.split())) != '"a\\x1bb c d \\"e\\" \\\\f"':
            why.append("yaml_dq does not escape C0")
        if [b for b in raw if b < 0x20 and b != 0x0A] or bytes([0x7f]) in raw:
            why.append("raw control byte in SKILL.md")
        if b"\\x1b" not in raw or b"\\x7f" not in raw:
            why.append("no \\xNN escape emitted")
        results.append(("control characters are escaped, never emitted raw",
                        "PASS" if not why else "FAIL", "; ".join(why) or "escaped as \\xNN"))

        # --- FM011: that grammar, now as a gate rule for the file nobody scaffolded ----------------
        # Every mutation below satisfies the per-line parse and is refused by a real loader, so each
        # has to be written as raw bytes: yaml_dq escapes precisely what is under test.
        m = complete(tmp / "fm011a", "fm011-quote")
        set_fm(m, "description", 'description: "compare "A" versus "B" here"')
        expect_fail("FM011 fires on an unescaped quote in description", m, "FM011")

        # The quote is escaped, so the scalar never closes — and the line still looks quoted.
        m = complete(tmp / "fm011b", "fm011-backslash")
        set_fm(m, "description", 'description: "a windows path C:\\"')
        expect_fail("FM011 fires on a dangling backslash", m, "FM011")

        m = complete(tmp / "fm011c", "fm011-singlequote")
        set_fm(m, "description", "description: 'it's broken'")
        code, out = run_check(m)
        miss = ("FM011 did NOT fire" if "FM011" not in out else
                "never said 'must be doubled'" if "must be doubled" not in out else
                "did not block" if code != 1 else "")
        results.append(("FM011 fires on an undoubled single quote",
                        "PASS" if not miss else "FAIL", miss or "blocked, naming the doubling rule"))

        # The rule is only keepable if the whole of yaml_dq's output survives it — the quotes,
        # backslashes and tab in `q` and the \xNN escapes in `c` are correct YAML, not defects.
        fp: list[str] = []
        for label, pkg in (("quotes, backslashes, tab", q), ("\\xNN escapes", c)):
            code, out = run_check(pkg)
            if "FM011" in out or code not in (0, 2):
                fp.append(f"{label}: exit {code}")
        results.append(("FM011 does not fire on the scaffolder's escaped output",
                        "PASS" if not fp else "FAIL",
                        "every escape accepted" if not fp else "; ".join(fp)))

        # A plain scalar is not a quoted scalar. Judged by the quoted grammar it FAILs, which is most
        # of the fleet, so the rule has to skip it rather than reject it.
        utf8 = complete(tmp / "fm011u", "fm011-utf8",
                        extra=["--description", "accents é, em dash —, cjk 中文, emoji 😀"])
        plain = complete(tmp / "fm011p", "fm011-plain")
        set_fm(plain, "category", "category: transcriptomics")
        fired = [w for w, pkg in (("real UTF-8", utf8), ("a plain scalar", plain))
                 if "FM011" in run_check(pkg)[1]]
        results.append(("FM011 does not fire on real UTF-8 or on an unquoted plain scalar",
                        "PASS" if not fired else "FAIL",
                        "both accepted" if not fired else "fired on " + ", ".join(fired)))

        # Branch order: a block scalar belongs to FM009, and re-judging it as a broken quoted scalar
        # swaps a WARN for a FAIL — the same value, a different contract.
        m = complete(tmp / "fm009b", "fm009-block")
        edit(m, "<!-- contract: evidence-v1 -->\n", "")
        (m / "skill_contract.json").unlink()
        legacy_sentence = (PKG_ROOT / "assets" / "contract" / "delegation_sentence_legacy.txt") \
            .read_text(encoding="utf-8").strip()
        edit(m, sentence, legacy_sentence)
        edit(m, sentence, legacy_sentence)
        set_fm(m, "description", "description: |")
        code, out = run_check(m)
        miss = ("FM009 did not fire" if "FM009" not in out else
                "escalated to FM011" if "FM011" in out else
                "it blocked" if code == 1 else "")
        results.append(("FM009 still WARNs on a block scalar and is not upgraded to FM011",
                        "PASS" if not miss else "FAIL", miss or f"WARN only (exit {code})"))

        # --- report_qc resolves deliverables against the results mount, not the CWD --------------
        # A skill runs with CWD=/workspace, so a gate that reads a relative path from the CWD
        # checks a file nobody will ever see — and passes when the deliverable was never written.
        sys.path.insert(0, str(PKG_ROOT / "templates"))
        cwd = pathlib.Path.cwd()
        try:
            import report_qc  # type: ignore

            mount = tmp / "mount"
            work = tmp / "workspace"
            (mount / "figures").mkdir(parents=True, exist_ok=True)
            (work / "figures").mkdir(parents=True, exist_ok=True)
            report_qc.RESULTS = mount
            os.chdir(work)

            def landed(label: str, got: pathlib.Path, want: pathlib.Path,
                       absent: pathlib.Path | None = None) -> None:
                ok = got == want and want.exists() and (absent is None or not absent.exists())
                results.append((label, "PASS" if ok else "FAIL", under(tmp, got)))

            def expect_gate(label: str, fn, *args, needle: str = "") -> None:
                try:
                    fn(*args)
                except report_qc.GateFailure as exc:
                    if needle and needle not in str(exc):
                        results.append((label, "FAIL", f"raised, but did not name {needle!r}"))
                    else:
                        results.append((label, "PASS", "raised"))
                except Exception as exc:  # noqa: BLE001
                    results.append((label, "FAIL", f"wrong error: {type(exc).__name__}"))
                else:
                    results.append((label, "FAIL", "did not raise"))

            def expect_no_raise(label: str, fn, *args, detail: str = "accepted") -> None:
                try:
                    fn(*args)
                except Exception as exc:  # noqa: BLE001
                    results.append((label, "FAIL", f"raised {type(exc).__name__}: {exc}"[:60]))
                else:
                    results.append((label, "PASS", detail))

            landed("write_facts writes to the results mount, not the CWD",
                   report_qc.write_facts("report_facts.json", {"n": 1}),
                   mount / "report_facts.json", work / "report_facts.json")

            outside = tmp / "elsewhere" / "facts.json"
            landed("write_facts honours an absolute path",
                   report_qc.write_facts(outside, {"n": 1}), outside,
                   mount / "elsewhere" / "facts.json")

            (mount / "figures" / "figure_2_qc.png").write_bytes(png_bytes())
            (mount / "figures" / "manifest.json").write_text(json.dumps(
                [{"step": 2, "file": "figures/figure_2_qc.png", "caption": "3 of 412 genes pass"}]),
                encoding="utf-8")
            try:
                entries = report_qc.assert_figures("figures/manifest.json")
                results.append(("assert_figures reads a relative manifest from the mount",
                                "PASS" if len(entries) == 1 else "FAIL", f"{len(entries)} entries"))
            except Exception as exc:  # noqa: BLE001
                results.append(("assert_figures reads a relative manifest from the mount",
                                "FAIL", f"raised {type(exc).__name__}: {exc}"[:60]))
            expect_gate("assert_figures names the mount-rooted path for an absent manifest",
                        report_qc.assert_figures, "figures/never_written.json", needle=str(mount))

            # The CWD copy is blank: a CWD-rooted resolver reads it and fails, so this test only
            # passes if the mount copy is the one being checked.
            (work / "figures" / "f.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (mount / "figures" / "f.png").write_bytes(png_bytes())
            expect_no_raise("assert_figure_ok resolves a relative figure under RESULTS",
                            report_qc.assert_figure_ok, "figures/f.png",
                            detail="read the mount, not the CWD decoy")
            expect_gate("assert_figure_ok names the mount-rooted path for an unwritten figure",
                        report_qc.assert_figure_ok, "figures/never_written.png", needle=str(mount))

            (work / "staged.h5ad").write_bytes(b"0" * 100)
            try:
                dst = report_qc.staged_copy("staged.h5ad", "adata.h5ad")
                ok = (dst == mount / "adata.h5ad" and dst.stat().st_size == 100
                      and not (work / "adata.h5ad").exists())
                results.append(("staged_copy lands dst on the mount, src stays CWD-relative",
                                "PASS" if ok else "FAIL", under(tmp, dst)))
            except Exception as exc:  # noqa: BLE001
                results.append(("staged_copy lands dst on the mount, src stays CWD-relative",
                                "FAIL", f"raised {type(exc).__name__}: {exc}"[:60]))

            (work / "replacement.pdf").write_bytes(b"new-pdf" * 100)
            (mount / "report_replace.pdf").write_bytes(b"old-pdf" * 100)
            original_run = report_qc.subprocess.run

            def copy_only_to_missing(argv, check):
                target = pathlib.Path(argv[2])
                if target.exists():
                    raise AssertionError("destination still existed before copy")
                shutil.copyfile(argv[1], target)
                return subprocess.CompletedProcess(argv, 0)

            report_qc.subprocess.run = copy_only_to_missing
            try:
                replaced = report_qc.staged_copy("replacement.pdf", "report_replace.pdf")
                ok = replaced.read_bytes() == (work / "replacement.pdf").read_bytes()
                results.append(("staged_copy removes an existing object before publishing",
                                "PASS" if ok else "FAIL", "replacement copied" if ok else "bytes differ"))
            except Exception as exc:  # noqa: BLE001
                results.append(("staged_copy removes an existing object before publishing",
                                "FAIL", f"raised {type(exc).__name__}: {exc}"[:60]))
            finally:
                report_qc.subprocess.run = original_run

            same = mount / "same.pdf"
            same.write_bytes(b"same-file" * 100)
            original = same.read_bytes()
            expect_gate("staged_copy rejects identical source and destination without deleting it",
                        report_qc.staged_copy, same, same, needle="must be different")
            results.append(("identical staged_copy rejection preserves the file",
                            "PASS" if same.read_bytes() == original else "FAIL",
                            "preserved" if same.read_bytes() == original else "changed"))

            (work / "fallback.pdf").write_bytes(b"complete" * 100)

            def partial_then_fail(argv, check):
                pathlib.Path(argv[2]).write_bytes(b"partial")
                raise subprocess.CalledProcessError(1, argv)

            report_qc.subprocess.run = partial_then_fail
            try:
                fallback = report_qc.staged_copy("fallback.pdf", "fallback.pdf")
                ok = fallback.read_bytes() == (work / "fallback.pdf").read_bytes()
                results.append(("staged_copy replaces a partial cp result before fallback",
                                "PASS" if ok else "FAIL", "complete" if ok else "partial"))
            except Exception as exc:  # noqa: BLE001
                results.append(("staged_copy replaces a partial cp result before fallback",
                                "FAIL", f"raised {type(exc).__name__}: {exc}"[:60]))
            finally:
                report_qc.subprocess.run = original_run

            # The report is required at the ROOT, so this one resolver must NOT be applied here.
            (mount / "report_x.pdf").write_bytes(b"%PDF-1.4\n" + b"0" * 30_000)
            try:
                rep = report_qc.assert_report_exists("deliverables/report_x.pdf")
                results.append(("assert_report_exists still basenames to the results root",
                                "PASS" if rep == mount / "report_x.pdf" else "FAIL",
                                under(tmp, rep)))
            except Exception as exc:  # noqa: BLE001
                results.append(("assert_report_exists still basenames to the results root",
                                "FAIL", f"raised {type(exc).__name__}: {exc}"[:60]))

            # GenerateImage call/result and PDF pixel-lineage mutations live in
            # test_report_contract.py so this core mutation suite remains below the repository's
            # 2,000-line file limit.

            # --- provider-neutral report styling ------------------------------------------------
            style = tmp / "style"
            style.mkdir(parents=True, exist_ok=True)
            style_root = style / "skills"

            def profile(path: pathlib.Path, provider: str, required: list[str],
                        supporting: list[str], *, activation: str = "default",
                        minimum: int = 2) -> pathlib.Path:
                path.parent.mkdir(parents=True, exist_ok=True)
                skill_path = path.parent.parent / "SKILL.md"
                skill_path.write_text(
                    f'---\nname: "{provider}"\ndescription: "Fixture style provider."\n---\n',
                    encoding="utf-8",
                )
                path.write_text(json.dumps({
                    "schema": "biomni-report-style/1",
                    "provider": provider,
                    "activation": activation,
                    **({"user_selection_aliases": [f"{provider} styling"]} if activation == "explicit_only" else {}),
                    "pdf_markers": {
                        "required_any": required,
                        "supporting_any": supporting,
                        "minimum_distinct_markers": minimum,
                    },
                }), encoding="utf-8")
                return path

            profile(
                style_root / "fixture-style" / "assets" / "report_style.json",
                "fixture-style", [FIXTURE_REQUIRED], list(FIXTURE_SUPPORTING),
            )
            report_qc._SYSTEM_STYLE_ROOT = style_root
            report_qc._PERSONAL_STYLE_ROOT = style_root

            def styled(label: str, name: str, *, provider: str | None = None,
                       tol: int = 2) -> None:
                try:
                    evidence = report_qc.assert_report_styled(
                        name, style_provider=provider or "fixture-style", tol=tol
                    )
                except Exception as exc:  # noqa: BLE001
                    results.append((label, "FAIL", f"raised {type(exc).__name__}: {exc}"[:90]))
                else:
                    ok = evidence.get("provider") == (provider or "fixture-style")
                    results.append((label, "PASS" if ok else "FAIL",
                                    str(evidence.get("provider"))))
            def rejected(label: str, fn, *needles: str) -> None:
                try:
                    fn()
                except report_qc.GateFailure as exc:
                    missing = [needle for needle in needles if needle not in str(exc)]
                    results.append((label, "FAIL" if missing else "PASS",
                                    f"missing {missing[0]!r}" if missing else "rejected"))
                except Exception as exc:  # noqa: BLE001
                    results.append((label, "FAIL", f"wrong error: {type(exc).__name__}"))
                else:
                    results.append((label, "FAIL", "did not reject"))
            forged_root = style / "workspace-forgery"
            profile(
                forged_root / "workspace-style" / "assets" / "report_style.json",
                "workspace-style", [FIXTURE_REQUIRED], [MARK],
            )
            os.environ["BIOMNI_SKILLS_ROOT"] = str(forged_root)
            rejected(
                "workspace environment cannot substitute a provider-owned style profile",
                lambda: report_qc.report_style_profile(
                    "workspace-style", allow_personal=True
                ),
                "no provider-owned", str(style_root),
            )
            os.environ.pop("BIOMNI_SKILLS_ROOT", None)
            styled_payload = rg(FIXTURE_REQUIRED, MARK)
            write_pdf(mount / "report_styled.pdf", styled_payload)
            styled("provider profile accepts its required and supporting markers",
                   "report_styled.pdf")
            user_style_root = style / "user-skills"
            profile(
                user_style_root / "user-style" / "assets" / "report_style.json",
                "user-style", [FIXTURE_REQUIRED], [MARK], activation="explicit_only",
            )
            report_qc._USER_STYLE_ROOT = user_style_root
            try:
                user_style = report_qc.assert_report_styled(
                    "report_styled.pdf",
                    style_provider="user-style",
                    allow_personal_provider=True,
                )
            except Exception as exc:  # noqa: BLE001
                results.append(("an explicitly selected user-installed style provider resolves",
                                "FAIL", f"raised {type(exc).__name__}: {exc}"[:90]))
            else:
                results.append(("an explicitly selected user-installed style provider resolves",
                                "PASS", str(user_style.get("provider"))))
            rejected(
                "a user-installed style provider cannot silently become the default",
                lambda: report_qc.assert_report_styled(
                    "report_styled.pdf", style_provider="user-style"
                ),
                "no provider-owned", str(style_root),
            )
            write_pdf(mount / "report_flate.pdf", zlib.compress(styled_payload), "/FlateDecode")
            styled("style gate reads a Flate-compressed content stream", "report_flate.pdf")
            stacked = base64.a85encode(zlib.compress(styled_payload)) + b"~>"
            write_pdf(mount / "report_stacked.pdf", stacked, "[ /ASCII85Decode /FlateDecode ]")
            styled("style gate reads ReportLab's ASCII85-over-Flate stack", "report_stacked.pdf")
            write_pdf(mount / "report_primary_only.pdf", rg(FIXTURE_REQUIRED))
            rejected(
                "one primary marker cannot satisfy the independent-marker floor",
                lambda: report_qc.assert_report_styled(
                    "report_primary_only.pdf", style_provider="fixture-style"
                ),
                "2", "supporting",
            )
            write_pdf(mount / "report_support_only.pdf", rg(*FIXTURE_SUPPORTING[:2]))
            rejected(
                "supporting markers cannot substitute for a required marker",
                lambda: report_qc.assert_report_styled(
                    "report_support_only.pdf", style_provider="fixture-style"
                ),
                FIXTURE_REQUIRED, "exact-value",
            )
            malformed_profiles = [
                ("white marker", ["#FFFFFF"], [MARK], 2),
                ("black support", [FIXTURE_REQUIRED], ["#000000"], 2),
                ("one-marker minimum", [FIXTURE_REQUIRED], [MARK], 1),
                ("impossible minimum", [FIXTURE_REQUIRED], [MARK], 3),
            ]
            malformed_blocked = []
            for index, (_, required, supporting, minimum) in enumerate(malformed_profiles):
                provider = f"bad-style-{index}"
                profile(
                    style_root / provider / "assets" / "report_style.json",
                    provider, required, supporting, minimum=minimum,
                )
                try:
                    report_qc.assert_report_styled(
                        "report_styled.pdf", style_provider=provider
                    )
                    malformed_blocked.append(False)
                except report_qc.GateFailure:
                    malformed_blocked.append(True)
            results.append(("malformed or non-evident provider profiles fail closed",
                            "PASS" if all(malformed_blocked) else "FAIL",
                            f"{sum(malformed_blocked)}/{len(malformed_blocked)} blocked"))

            rejected(
                "provider slugs cannot traverse the skills root",
                lambda: report_qc.assert_report_styled(
                    "report_styled.pdf", style_provider="../other-style"
                ),
                "lowercase skill slug",
            )
            profile(
                style_root / "requested-style" / "assets" / "report_style.json",
                "other-style", [FIXTURE_REQUIRED], [MARK],
            )
            rejected(
                "a provider-owned profile must declare its directory provider",
                lambda: report_qc.assert_report_styled(
                    "report_styled.pdf", style_provider="requested-style"
                ),
                "requested-style", "other-style",
            )

            # Mutation-backed: taking away either half of the artifact check makes a known-bad PDF
            # pass, so these tests fail if those lines become decorative.
            no_required = qc_variant(
                style / "qc_no_required.py", mount,
                "if not required_hits:", "if False and not required_hits:",
            )
            no_floor = qc_variant(
                style / "qc_no_floor.py", mount,
                "if len(distinct_hits) < minimum:", "if False and len(distinct_hits) < minimum:",
            )
            mutation_effects = []
            for module, name in (
                (no_required, "report_support_only.pdf"),
                (no_floor, "report_primary_only.pdf"),
            ):
                module._SYSTEM_STYLE_ROOT = style_root
                try:
                    module.assert_report_styled(name, style_provider="fixture-style")
                    mutation_effects.append(True)
                except Exception:  # noqa: BLE001
                    mutation_effects.append(False)
            results.append(("required-marker and independent-marker checks are load-bearing",
                            "PASS" if all(mutation_effects) else "FAIL",
                            f"{sum(mutation_effects)}/2 mutations admitted their bad fixture"))

            write_pdf(mount / "report_channel_trap.pdf", rg("#146543", MARK))
            any_channel = qc_variant(
                style / "qc_any_channel.py", mount,
                "return all(abs(a - b) <= tol", "return any(abs(a - b) <= tol",
            )
            any_channel._SYSTEM_STYLE_ROOT = style_root
            try:
                report_qc.assert_report_styled(
                    "report_channel_trap.pdf", style_provider="fixture-style"
                )
                shipped_rejected = False
            except report_qc.GateFailure:
                shipped_rejected = True
            try:
                any_channel.assert_report_styled(
                    "report_channel_trap.pdf", style_provider="fixture-style"
                )
                mutant_accepted = True
            except Exception:  # noqa: BLE001
                mutant_accepted = False
            results.append(("style marker tolerance compares all three colour channels",
                            "PASS" if shipped_rejected and mutant_accepted else "FAIL",
                            f"shipped_rejected={shipped_rejected}, mutant_accepted={mutant_accepted}"))

            (mount / "report_text.pdf").write_bytes(b"not a pdf")
            rejected(
                "a non-PDF named .pdf is not silently styled",
                lambda: report_qc.assert_report_styled(
                    "report_text.pdf", style_provider="fixture-style"
                ),
                "no colour operators",
            )
            rejected(
                "the style gate requires the report at the results root",
                lambda: report_qc.assert_report_styled(
                    "report_never_built.pdf", style_provider="fixture-style"
                ),
                "assert_report_exists",
            )

            image = stream_obj(
                bytes(range(256)) * 4096,
                dictionary="/Type /XObject /Subtype /Image /Width 512 /Height 512 "
                           "/ColorSpace /DeviceRGB /BitsPerComponent 8",
            )
            write_pdf(mount / "report_image.pdf", zlib.compress(styled_payload), "/FlateDecode",
                      extra=(image,))
            started = time.monotonic()
            try:
                image_result = report_qc.assert_report_styled(
                    "report_image.pdf", style_provider="fixture-style"
                )
            except Exception as exc:  # noqa: BLE001
                image_result = {"error": type(exc).__name__}
            elapsed = time.monotonic() - started
            results.append(("a 1 MB image-bearing report keeps its vector style stream",
                            "PASS" if image_result.get("provider") == "fixture-style"
                            and elapsed < 5 else "FAIL",
                            f"{image_result.get('provider', image_result)} in {elapsed:.2f}s"))

            img_dict = ("/Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceRGB "
                        "/BitsPerComponent 8")
            form_dict = "/Type /XObject /Subtype /Form /BBox [0 0 8 8]"
            write_pdf(mount / "report_img_style.pdf", rg("#000000"),
                      extra=(stream_obj(styled_payload, dictionary=img_dict),))
            write_pdf(mount / "report_form_style.pdf", rg("#000000"),
                      extra=(stream_obj(styled_payload, dictionary=form_dict),))
            try:
                report_qc.assert_report_styled(
                    "report_img_style.pdf", style_provider="fixture-style"
                )
                image_rejected = False
            except report_qc.GateFailure:
                image_rejected = True
            try:
                form_result = report_qc.assert_report_styled(
                    "report_form_style.pdf", style_provider="fixture-style"
                )
                form_accepted = form_result.get("provider") == "fixture-style"
            except Exception:  # noqa: BLE001
                form_accepted = False
            results.append(("raster pixels cannot impersonate provider style markers",
                            "PASS" if image_rejected and form_accepted else "FAIL",
                            f"image_rejected={image_rejected}, form_accepted={form_accepted}"))

            # --- write_receipt: the outcomes are returns, not assertions about themselves ----------
            # The receipt used to be five booleans the agent wrote next to a copy-pasteable all-true
            # block. These rows check the replacement actually measures: a real branded report and
            # real figures produce a true receipt carrying the artifacts it read, and each outcome
            # goes false — with its reason — when the thing it names is genuinely absent.
            fig_dir = mount / "figures"
            fig_dir.mkdir(parents=True, exist_ok=True)
            (fig_dir / "figure_2_qc.png").write_bytes(png_bytes())
            manifest = [{"step": 2, "file": "figures/figure_2_qc.png", "caption": "per-sample QC"}]

            # assert_report_exists rejects anything under 20 kB as a rendering failure, so the brand
            # fixtures above are too small to carry a receipt. Pad inside the content stream with a PDF
            # comment: the colour operators still parse, and the size gate is not what these rows test.
            def big(payload: bytes) -> bytes:
                return payload + b"\n% " + b"padding " * 3_000

            write_pdf(
                mount / "report_full.pdf",
                big(rg(FIXTURE_REQUIRED, MARK)),
                embedded_image=True,
            )
            (mount / "results_demo.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            (mount / "ran_this.py").write_text("# the bundled script\n", encoding="utf-8")

            def receipt_for(name: str, **kw) -> dict:
                report_qc.write_receipt(
                    report_name=name, figures=manifest,
                    bundled_files=[str(mount / "ran_this.py")],
                    outputs=["results_demo.csv"], style_provider="fixture-style",
                    path=f"receipt_{pathlib.Path(name).stem}.json", strict=False, **kw)
                return json.loads(
                    (mount / f"receipt_{pathlib.Path(name).stem}.json").read_text(encoding="utf-8"))

            got = receipt_for("report_full.pdf")
            missing_true = [k for k in RECEIPT_KEYS if got.get(k) is not True]
            results.append(("write_receipt records every outcome true for a good run",
                            "PASS" if not missing_true else "FAIL",
                            ", ".join(
                                f"{k}={got.get(k)!r} ({got.get(f'{k}_reason', 'no reason')})"
                                for k in missing_true
                            )
                            or f"all {len(RECEIPT_KEYS)} true"))

            # Evidence, not adjectives: each outcome has to carry the artifact it was read from.
            bare = [k for k in RECEIPT_KEYS if not (got.get("evidence") or {}).get(k)]
            results.append(("write_receipt attaches evidence to every outcome",
                            "PASS" if not bare and got.get("schema") == RECEIPT_SCHEMA else "FAIL",
                            ", ".join(bare) or f"schema {got.get('schema')!r}, all keys evidenced"))

            style_ev = (got.get("evidence") or {}).get("report_branded") or {}
            results.append(("the legacy style outcome records the provider source it read",
                            "PASS" if style_ev.get("provider") == "fixture-style"
                            and style_ev.get("required_marker_hits") == 1
                            else "FAIL",
                            f"provider={style_ev.get('provider')!r}"))

            # An unbranded report is the artifact that motivated the whole brand gate. The receipt
            # must come back false with a reason, not raise before it is written: a failing run should
            # leave the diagnostic behind.
            write_pdf(mount / "report_plain.pdf", big(rg("#123456", "#654321")),
                      embedded_image=True)
            unbranded = receipt_for("report_plain.pdf")
            ok = (unbranded.get("report_branded") is False
                  and "report_branded_reason" in unbranded
                  and unbranded.get("figures_present_and_nonblank") is True)
            results.append(("write_receipt records a style-mismatched report as false, with a reason",
                            "PASS" if ok else "FAIL",
                            f"branded={unbranded.get('report_branded')!r}, "
                            f"reason={'yes' if 'report_branded_reason' in unbranded else 'no'}"))

            # strict=True is the default and has to raise AFTER writing, so the receipt survives.
            try:
                report_qc.write_receipt(
                    report_name="report_plain.pdf", figures=manifest,
                    bundled_files=[str(mount / "ran_this.py")], outputs=["results_demo.csv"],
                    style_provider="fixture-style", path="receipt_strict.json")
                why = "did not raise on a failing run"
            except report_qc.GateFailure:
                why = ("" if (mount / "receipt_strict.json").exists()
                       else "raised without leaving the receipt behind")
            except Exception as exc:  # noqa: BLE001
                why = f"wrong error: {type(exc).__name__}"
            results.append(("strict write_receipt raises but still leaves the diagnostic",
                            "PASS" if not why else "FAIL", why or "raised, receipt on disk"))

            # A promised output that never appeared is the defect the outputs key exists for.
            (mount / "results_demo.csv").unlink()
            gone = receipt_for("report_full.pdf")
            results.append(("write_receipt records a missing declared output as false",
                            "PASS" if gone.get("outputs_appeared") is False else "FAIL",
                            f"outputs_appeared={gone.get('outputs_appeared')!r}"))
            (mount / "results_demo.csv").write_text("a,b\n1,2\n", encoding="utf-8")

            # The two files cannot import each other — report_qc is copied into every generated skill
            # and has to stand alone — so the shared constants are held by tests instead.
            results.append(("report_qc and the gate agree on the receipt schema",
                            "PASS" if report_qc.RECEIPT_SCHEMA == RECEIPT_SCHEMA else "FAIL",
                            f"{report_qc.RECEIPT_SCHEMA!r} vs {RECEIPT_SCHEMA!r}"))
            results.append(("report_qc and the gate agree on the embedding states",
                            "PASS" if report_qc.EMBED_STATES == EMBED_STATES else "FAIL",
                            f"{report_qc.EMBED_STATES} vs {EMBED_STATES}"))

            # The tri-state has to be read off the artifact, not defaulted. pypdf is absent here, so
            # a good run lands on not_evaluable — and the figure artifacts still verify, which is the
            # separation the finding asked for: two claims, each accurate about itself.
            got_emb = receipt_for("report_full.pdf")
            ok = (got_emb.get("figures_present_and_nonblank") is True
                  and got_emb.get("figures_embedded") in EMBED_STATES)
            results.append(("write_receipt records the embedding verdict apart from the artifacts",
                            "PASS" if ok else "FAIL",
                            f"present={got_emb.get('figures_present_and_nonblank')!r} "
                            f"embedded={got_emb.get('figures_embedded')!r}"))

            # And it must say what the check actually was, so a reader downstream does not take an
            # image count for identity matching.
            method = ((got_emb.get("evidence") or {}).get("figures_present_and_nonblank") or {})
            method = (method.get("embedding") or {}).get("method", "")
            results.append(("the embedding evidence says it does not match figure identity",
                            "PASS" if "does not match figure identity" in method else "FAIL",
                            method[:60] or "no method recorded"))

            # Keep the two claims separate without weakening the terminal gate: valid figure files
            # remain true, while a verified PDF-embedding mismatch still blocks a strict receipt.
            write_pdf(mount / "report_no_embed.pdf", big(rg(FIXTURE_REQUIRED, MARK)))
            original_embed_check = report_qc.report_embeds_figures
            report_qc.report_embeds_figures = lambda *_: (
                False, "report embeds 0 image(s) but 1 figure(s) were declared"
            )
            try:
                try:
                    report_qc.write_receipt(
                        report_name="report_no_embed.pdf", figures=manifest,
                        bundled_files=[str(mount / "ran_this.py")], outputs=["results_demo.csv"],
                        style_provider="fixture-style", path="receipt_no_embed.json",
                    )
                    why = "did not raise"
                except report_qc.GateFailure:
                    no_embed = json.loads(
                        (mount / "receipt_no_embed.json").read_text(encoding="utf-8"))
                    why = "" if (
                        no_embed.get("figures_present_and_nonblank") is True
                        and no_embed.get("figures_embedded") == "fail"
                    ) else "receipt conflated artifact validity with embedding"
            finally:
                report_qc.report_embeds_figures = original_embed_check
            results.append(("a verified embedding mismatch stays separate and blocks strict mode",
                            "PASS" if not why else "FAIL", why or "artifact true, embedding fail"))
        finally:
            os.chdir(cwd)
            sys.path.pop(0)

        # --- RR001: every outcome in RECEIPT_KEYS is required BY NAME ----------------------------
        # A receipt that names its own outcomes can always pass, which is the gate-that-cannot-fail
        # defect this whole package exists to prevent.
        r2 = complete(tmp / "rr2", "rr-required-keys")
        # The write_receipt() shape, so each row below fails for the reason it names rather than
        # tripping RR002 on the way past. RR002's own cases are grouped with the RR001 basics above.
        all_true = receipt_dict()

        expect_receipt_fail("RR001 fires when a required outcome is absent", r2,
                            receipt_dict(keys=[k for k in EVIDENCE_RECEIPT_KEYS
                                               if k != "figure_contract_satisfied"]),
                            "figure_contract_satisfied is absent")

        (r2 / "run_receipt.json").write_text(json.dumps({"seemed_fine": True}), encoding="utf-8")
        code, out = run_check(r2, ["--require-run-receipt"])
        named = [k for k in EVIDENCE_RECEIPT_KEYS if f"{k} is absent" in out]
        ok = code == 1 and len(named) == len(EVIDENCE_RECEIPT_KEYS)
        results.append(("RR001 fires on a receipt that invents its own outcome names",
                        "PASS" if ok else "FAIL",
                        f"exit {code}, {len(named)}/{len(EVIDENCE_RECEIPT_KEYS)} named"))

        # Truthiness is not proof: "true" and 1 must not stand in for the boolean.
        blocked = []
        for v in ("true", 1, None):
            (r2 / "run_receipt.json").write_text(
                json.dumps(dict(all_true, execution_contract_satisfied=v)), encoding="utf-8")
            code, out = run_check(r2, ["--require-run-receipt"])
            blocked.append(code == 1 and "execution_contract_satisfied is" in out
                           and "not the boolean true" in out)
        results.append(("RR001 rejects a non-boolean required value",
                        "PASS" if all(blocked) else "FAIL", f"{sum(blocked)}/3 blocked"))

        expect_receipt_fail("RR001 still names the reason for a false required outcome", r2,
                            dict(all_true, figure_contract_satisfied=False,
                                 figure_contract_satisfied_reason="figure 3 was blank"),
                            "figure_contract_satisfied is false (figure 3 was blank)")

        expect_receipt_fail("RR001 fires on a non-required boolean that is false", r2,
                            dict(all_true, trace_checked=False), "trace_checked is false")

        # A gate nobody can pass gets deleted, so the satisfiable case is part of the contract.
        (r2 / "run_receipt.json").write_text(json.dumps(all_true), encoding="utf-8")
        code, out = run_check(r2, ["--require-run-receipt"])
        results.append((f"RR001 stays satisfiable: all {len(RECEIPT_KEYS)} required keys, all true",
                        "PASS" if code in (0, 2) and "RR001" not in out else "FAIL", f"exit {code}"))

        # Spec-code drift: every evidence-v1 outcome required by the gate must be documented.
        spec = (PKG_ROOT / "SKILL.md").read_text(encoding="utf-8")
        undocumented = [k for k in EVIDENCE_RECEIPT_KEYS if k not in spec]
        stale = "fails if the receipt is missing or any boolean is false" in spec
        results.append(("SKILL.md Step 5 names every key the gate requires",
                        "PASS" if not undocumented and not stale else "FAIL",
                        ", ".join(undocumented) or ("stale wording still present" if stale
                                                    else "all named, old wording gone")))

        # --- the receipt the scaffolder prints must be the receipt the gate accepts ---------------
        # A terminal step that says "record it in the run receipt" without naming the keys sends the
        # author off to invent four sensible names, and RR001 then blocks on the spelling.
        rt = scaffold(tmp / "rt", "receipt-round-trip")
        rt_md = (rt / "SKILL.md").read_text(encoding="utf-8")
        unnamed = [k for k in EVIDENCE_RECEIPT_KEYS if k not in rt_md]
        results.append(("the generated SKILL.md names every receipt key the gate requires",
                        "PASS" if not unnamed else "FAIL",
                        f"all {len(EVIDENCE_RECEIPT_KEYS)} named" if not unnamed else ", ".join(unnamed)))

        # This block used to copy the JSON receipt the terminal step printed, paste it verbatim, and
        # assert the gate accepted it. It did — and that was the finding raised in review: the cheapest
        # way to satisfy the shipping gate was to copy the all-true block out of the very skill being
        # recorded. The round trip is inverted, because the property worth holding is the opposite one.
        pasteable = [b for b in re.findall(r"```json\n(\{.*?\})\n```", rt_md, re.S)
                     if any(k in b for k in EVIDENCE_RECEIPT_KEYS)]
        results.append(("the terminal step hands out no pasteable receipt block",
                        "PASS" if not pasteable else "FAIL",
                        "nothing copyable" if not pasteable
                        else f"{len(pasteable)} all-true block(s) still copyable"))

        calls = [b for b in FENCE_RE.findall(rt_md) if "write_receipt(" in b]
        results.append(("the terminal step calls write_receipt instead",
                        "PASS" if calls else "FAIL", f"{len(calls)} fence(s) call it"))

        # And the paste it used to hand out must now be rejected, end to end: the exact all-true shape
        # the old block had, against the gate the generated skill ships with.
        (rt / "run_receipt.json").write_text(receipt_json(evidence=False, schema=None),
                                             encoding="utf-8")
        code, out = run_check(rt, ["--require-run-receipt"])
        results.append(("the receipt the old block would have produced is now rejected",
                        "PASS" if code == 1 and "RR002" in out else "FAIL", f"exit {code}"))

        # --- RC009: the receipt call must name real files -----------------------------------------
        # Raised in review. The scaffolder shipped `bundled_files=[...]`, which reads as a
        # documentation placeholder and is not one: `...` is Ellipsis, so the call passed every static
        # check here and then died on pathlib.Path(Ellipsis) inside write_receipt(). Both the empty
        # form (what ships now, deliberately) and the Ellipsis form have to block.
        rc9 = complete(tmp / "rc009", "rc009-placeholders")
        for label, arg, bad in (
            ("empty bundled_files", "bundled_files", "[]"),
            ("empty outputs", "outputs", "[]"),
            ("Ellipsis bundled_files", "bundled_files", "[...]"),
            ("Ellipsis outputs", "outputs", "[...]"),
        ):
            md9 = rc9 / "SKILL.md"
            keep = md9.read_text(encoding="utf-8")
            md9.write_text(re.sub(rf"\b{arg}\s*=\s*[^,\n#]*,", f"{arg}={bad},", keep, count=1),
                           encoding="utf-8")
            code, out = run_check(rc9)
            results.append((f"RC009 fires on {label}",
                            "PASS" if code == 1 and "RC009" in out else "FAIL", f"exit {code}"))
            md9.write_text(keep, encoding="utf-8")
        # A variable is a legitimate answer — the list may only exist at run time — so the rule must
        # reject the known-bad forms and nothing else, or an author works around it by inlining.
        md9 = rc9 / "SKILL.md"
        md9.write_text(md9.read_text(encoding="utf-8").replace(
            'bundled_files=["scripts/report_qc.py"],', "bundled_files=ran_scripts,"), encoding="utf-8")
        expect_quiet("RC009 accepts a variable rather than demanding a literal", rc9, "RC009")

        # The regression test asked for by name in review: EXECUTE the generated call. Nothing else in
        # this suite runs a fence, which is precisely how an Ellipsis shipped — every row read the
        # code and none of it ran.
        ex = complete(tmp / "execfence", "exec-the-fence")
        mount = tmp / "execmount"
        (mount / "figures").mkdir(parents=True, exist_ok=True)
        (mount / "figures" / "f2.png").write_bytes(png_bytes())
        (mount / "results_demo.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (mount / "facts_payload.json").write_text(json.dumps({
            "n_input": 2, "n_tested": 1, "n_not_tested": 1,
        }), encoding="utf-8")
        (mount / "report_text.txt").write_text(
            "Task Context\nMethods & Sources\nResults\n"
            "Conclusions & Interpretation\nLimitations\n", encoding="utf-8")
        (mount / "rendered_pages").mkdir()
        (mount / "rendered_pages" / "page-1.png").write_bytes(png_bytes())
        (mount / "infographic.png").write_bytes(png_bytes())
        report_headings = (
            "Task Context", "Methods & Sources", "Results",
            "Conclusions & Interpretation", "Limitations",
        )
        write_pdf(mount / "report_exec_the_fence.pdf",
                  rg(FIXTURE_REQUIRED, MARK) + b"\n% " + b"padding " * 3_000,
                  embedded_image=True, text_lines=report_headings)
        workspace_report = write_pdf(tmp / "workspace_report.pdf",
                                     rg(FIXTURE_REQUIRED, MARK) + b"\n% " + b"padding " * 3_000,
                                     embedded_image=True, text_lines=report_headings)
        import hashlib
        file_hash = lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
        trace_ids = ("evt-command", "evt-text", "evt-render", "evt-review")
        trace_dir = mount / "execution_trace"
        trace_dir.mkdir()
        (trace_dir / "transcript.jsonl").write_text(
            "\n".join(json.dumps(item) for item in [{"type": "user", "i": 1, "content": "Please use user-style styling."}, *({"event_id": event_id} for event_id in trace_ids)]) + "\n",
            encoding="utf-8",
        )
        ledger = {
            "schema": "phylo-qc-run-log/1", "generated_by": "report_qc",
            "events": [
                {"type": "command",
                 "bundled_file": "scripts/report_qc.py",
                 "bundled_sha256": file_hash(ex / "scripts" / "report_qc.py"), "exit_status": 0,
                 "produced_artifacts": [
                     {"path": "results_demo.csv", "sha256": file_hash(mount / "results_demo.csv")},
                ]},
                {"type": "generateimage_snapshot", "filename": "infographic.png",
                 "image_sha256": file_hash(mount / "infographic.png"),
                 "decoded_pixel_sha256": "fixture-pixels",
                 "trace_evidence": {"tool_call_id": "generate-1", "result_id": "result-1",
                                    "requested_filename": "infographic.png",
                                    "returned_filename": "infographic.png"}},
            ],
        }
        (mount / "qc_run_log.json").write_text(json.dumps(ledger), encoding="utf-8")

        fence = next((b for b in FENCE_RE.findall((ex / "SKILL.md").read_text(encoding="utf-8"))
                      if "write_receipt(" in b), "")
        facts_fence = next((b for b in FENCE_RE.findall(
            (ex / "SKILL.md").read_text(encoding="utf-8")
        ) if "write_facts_from_artifact(" in b), "")
        why = "" if fence and facts_fence else "generated facts or receipt fence is missing"
        if fence:
            env = os.environ.get("BIOMNI_RESULTS")
            os.environ["BIOMNI_RESULTS"] = str(mount)
            sys.path.insert(0, str(ex / "scripts"))
            try:
                for mod in [m for m in sys.modules if m == "report_qc"]:
                    del sys.modules[mod]
                import report_qc as rq_gen  # the copy the package ships, not the template
                rq_gen._SYSTEM_STYLE_ROOT = style_root
                rq_gen._USER_STYLE_ROOT = user_style_root
                rq_gen._PERSONAL_STYLE_ROOT = style_root

                ns = {
                    "figures": [{"step": 2, "file": "figures/f2.png", "caption": "per-sample fit"}],
                    "ran_scripts": [str(ex / "scripts" / "report_qc.py")],
                    "selected_branch_ids": ["input:airway-demo"],
                    "extracted_text_file": "report_text.txt",
                    "rendered_page_files": ["rendered_pages/page-1.png"],
                    "reviewed_page_numbers": [1],
                    "visual_review_notes": "media-check:event-review",
                    "visual_review_verdict": "pass",
                    "visual_review_issues": [],
                    "workspace_report_file": str(workspace_report),
                }
                src = fence.replace(
                    "from report_qc import record_pdf_review, staged_copy, write_receipt", ""
                )
                ns["write_receipt"] = rq_gen.write_receipt
                ns["record_pdf_review"] = rq_gen.record_pdf_review
                ns["staged_copy"] = rq_gen.staged_copy
                rq_gen._pdf_page_count = lambda report: 1
                rq_gen.assert_generated_by_tool = lambda *names: [
                    {"tool_call_id": "generate-1", "result_id": "result-1",
                     "requested_filename": name, "returned_filename": name}
                    for name in names
                ]
                rq_gen.assert_infographic_pdf_lineage = lambda report, evidence: [
                    {**item, "embedded_page": 1, "embedded_image_index": 1}
                    for item in evidence
                ]
                rq_gen._decoded_pixel_sha256 = lambda path: "fixture-pixels"
                exec(compile(facts_fence, "<generated facts fence>", "exec"), ns)  # noqa: S102
                exec(compile(src, "<generated SKILL.md fence>", "exec"), ns)  # noqa: S102
                got = json.loads((mount / "run_receipt.json").read_text(encoding="utf-8"))
                missing = [k for k in EVIDENCE_RECEIPT_KEYS if got.get(k) is not True]
                explicit_style = got.get("evidence", {}).get("report_style_verified", {})
                why = (
                    f"receipt not all-true: {missing}" if missing
                    else "explicit provider selection was not recorded"
                    if explicit_style.get("selection") != "explicit_override"
                    else ""
                )
                if not why:
                    default_profile, _, _ = rq_gen._report_style.resolve_provider(
                        "pdf-report-generation",
                        (PKG_ROOT.parent,),
                        activation_hint="default",
                    )
                    default_markers = default_profile["pdf_markers"]
                    default_payload = rg(
                        default_markers["required_any"][0],
                        default_markers["supporting_any"][0],
                    ) + b"\n% " + b"padding " * 3_000
                    write_pdf(
                        mount / "report_exec_the_fence.pdf",
                        default_payload,
                        embedded_image=True,
                        text_lines=report_headings,
                    )
                    write_pdf(
                        workspace_report,
                        default_payload,
                        embedded_image=True,
                        text_lines=report_headings,
                    )
                    (trace_dir / "transcript.jsonl").write_text(json.dumps({"type": "user", "i": 1, "content": "Prepare the default report."}) + "\n", encoding="utf-8")
                    previous_style_root = rq_gen._SYSTEM_STYLE_ROOT
                    rq_gen._SYSTEM_STYLE_ROOT = PKG_ROOT.parent
                    try:
                        exec(compile(fence, "<generated default-style fence>", "exec"), ns)  # noqa: S102
                    finally:
                        rq_gen._SYSTEM_STYLE_ROOT = previous_style_root
                    default_receipt = json.loads(
                        (mount / "run_receipt.json").read_text(encoding="utf-8")
                    )
                    default_style = (
                        default_receipt.get("evidence", {}).get("report_style_verified", {})
                    )
                    missing = [
                        key for key in EVIDENCE_RECEIPT_KEYS
                        if default_receipt.get(key) is not True
                    ]
                    if (
                        missing
                        or default_style.get("provider") != "pdf-report-generation"
                        or default_style.get("selection") != "contract_default"
                    ):
                        why = (
                            f"default provider failed: missing={missing}, "
                            f"provider={default_style.get('provider')!r}"
                        )
            except Exception as exc:  # noqa: BLE001
                why = f"{type(exc).__name__}: {exc}"[:70]
            finally:
                sys.path.pop(0)
                sys.modules.pop("report_qc", None)
                if env is None:
                    os.environ.pop("BIOMNI_RESULTS", None)
                else:
                    os.environ["BIOMNI_RESULTS"] = env
        results.append(("the generated receipt fence supports explicit and default style providers",
                        "PASS" if not why else "FAIL", why or "both receipts all-true"))

        # --- DG001: a rule whose data did not load must never read as a pass ----------------------
        victim = complete(tmp / "dg", "degrade-victim")

        gate = gate_copy(tmp / "gate-nostanzas")
        (gate / "assets" / "contract" / "known_stanzas.json").unlink()
        expect_degrade("DG001 fires and exits 3 when known_stanzas.json is absent", gate, victim,
                       "ST001-ST003 never ran", "did not load")

        # Against a clean victim, never against PKG_ROOT: this package's SKILL.md names
        # templates/report_qc.py, so with templates/ gone BF001 FAILs and exit 1 hides the degrade.
        gate = gate_copy(tmp / "gate-notemplates")
        shutil.rmtree(gate / "templates")
        expect_degrade("DG001 fires and exits 3 when templates/ is absent", gate, victim,
                       "BF002 never ran", "is absent")

        # Present but empty is the quieter case: the loader succeeds and the rule matches nothing.
        # One blind loader must not stand in for the other, so both messages have to appear.
        blind = gate_copy(tmp / "gate-blind")
        (blind / "assets" / "contract" / "known_stanzas.json").write_text('{"stanzas": []}',
                                                                         encoding="utf-8")
        shutil.rmtree(blind / "templates")
        (blind / "templates").mkdir()
        expect_degrade("DG001 fires once per loader that came back empty", blind, victim,
                       "lists no usable stanza", "ships no python module", n=2)

        # Precedence, both directions. A real failure is the more actionable answer, so it keeps exit
        # 1 — every caller that keys on 1 stops seeing failures the moment a degrade outranks them.
        (victim / "__pycache__").mkdir(exist_ok=True)
        (victim / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        code, out = run_gate(blind, victim)
        ok = code == 1 and "PK001" in out and "DG001" in out
        results.append(("a blocking finding outranks a degrade",
                        "PASS" if ok else "FAIL",
                        f"exit {code}" + ("" if "DG001" in out else ", degrade not even printed")))
        shutil.rmtree(victim / "__pycache__")

        (victim / "uv.lock").write_text("x", encoding="utf-8")
        code, out = run_gate(blind, victim)
        ok = code == 3 and "PK002" in out and "GATE PASSED" not in out
        results.append(("a degrade outranks a warning and never prints GATE PASSED",
                        "PASS" if ok else "FAIL",
                        f"exit {code}" + (", printed GATE PASSED" if "GATE PASSED" in out else "")))

        # A new exit path is where the old ones regress, so the intact gate has to keep all three.
        normal = [("warns 2", run_check(victim)[0] == 2)]
        (victim / "uv.lock").unlink()
        normal.append(("clean 0", run_check(victim)[0] == 0))
        (victim / "__pycache__").mkdir(exist_ok=True)
        (victim / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        normal.append(("blocking 1", run_check(victim)[0] == 1))
        shutil.rmtree(victim / "__pycache__")
        wrong = [w for w, held in normal if not held]
        results.append(("the intact gate still exits 0 clean, 1 blocking, 2 warnings-only",
                        "PASS" if not wrong else "FAIL",
                        "all three hold" if not wrong else "wrong: " + ", ".join(wrong)))

        # --measure hides a blind rule the other way round: the row is simply absent from the table,
        # and absence reads as a 0% fire rate on a rule worth keeping.
        corpus = tmp / "corpus"
        scaffold(corpus, "measure-one")
        pasted = scaffold(corpus, "measure-two")
        edit(pasted, "## Common Issues",
             "4. Write from Scratch (1%) - only if impossible\n\n## Common Issues")

        def measured(gate: pathlib.Path) -> tuple[int, str]:
            p = subprocess.run([sys.executable, str(gate / "scripts" / "check_skill.py"),
                                "--measure", str(corpus / "skills")],
                               capture_output=True, text=True)
            return p.returncode, p.stdout + p.stderr

        def has_row(out: str, rule: str) -> bool:
            """A table row starts at column 0; the degrade banner indents, so it is not a row."""
            return any(ln.startswith(rule) for ln in out.splitlines())

        c_ok, o_ok = measured(PKG_ROOT)
        c_blind, o_blind = measured(blind)
        miss = (f"intact run exited {c_ok} without an ST001 row"
                if not (c_ok == 0 and has_row(o_ok, "ST001")) else
                f"blind run exited {c_blind}, not 3" if c_blind != 3 else
                "ST001 still has a row with the stanzas blind" if has_row(o_blind, "ST001") else
                "; ".join(f"banner omits {x!r}" for x in
                          ("not measured at 0%", "lists no usable stanza", "ships no python module")
                          if x not in o_blind))
        results.append(("--measure exits 3 and names the rules missing from its table",
                        "PASS" if not miss else "FAIL",
                        miss or "ST001 measured, then named as blind"))

    # --- report ---------------------------------------------------------------------------------
    width = max(len(n) for n, _, _ in results)
    print(f"{'check':{width}}  verdict  detail")
    print("-" * (width + 40))
    for name, verdict, detail in results:
        print(f"{name:{width}}  {verdict:7}  {detail}")

    failed = [r for r in results if r[1] == "FAIL"]
    stuck = [r for r in results if r[1] == "DEGRADE"]
    print()
    if not results:
        print("RESULT: nothing ran — all skipped counts as failure, not success.")
        return 2
    print(f"RESULT: {len(results) - len(failed) - len(stuck)}/{len(results)} passed")
    if failed:
        return 1
    # A test this environment cannot evaluate is not a test that passed, and the same precedence as
    # the gate: a real failure is the more actionable answer, so it keeps 1.
    for name, _, detail in stuck:
        print(f"DEGRADED — could not evaluate: {name} ({detail})")
    return 3 if stuck else 0


if __name__ == "__main__":
    if not SCAFFOLD.exists() or not CHECK.exists():
        print(f"RESULT: scripts not found under {PKG_ROOT} — all skipped counts as failure.")
        sys.exit(2)
    sys.exit(main())
