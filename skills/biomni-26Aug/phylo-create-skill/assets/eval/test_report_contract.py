#!/usr/bin/env python3
"""Mutation tests for universal report structure and exact infographic lineage.

Run with the package's pinned report environment so Pillow, pypdf, and ReportLab are available:

    python assets/eval/test_report_contract.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

from test_checks_support import GOOD_RECORD, PKG_ROOT, SCAFFOLD  # noqa: E402


def load_qc(results_root: pathlib.Path):
    os.environ["BIOMNI_RESULTS"] = str(results_root)
    source = PKG_ROOT / "templates" / "report_qc.py"
    spec = importlib.util.spec_from_file_location("report_contract_qc", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.RESULTS = results_root
    module.TRANSCRIPT = results_root / "execution_trace" / "transcript.jsonl"
    return module


def typed_trace(filename: str, *, linked: bool = True) -> dict:
    call_id = "call-generate-1"
    return {
        "type": "list",
        "data": [
            {"id": "message-a", "type": "message", "content": [
                {"type": "tool_use", "id": call_id, "name": "GenerateImage",
                 "input": {"prompt": "Explain the evidence flow", "filename": filename}},
            ]},
            {"id": "message-b", "type": "message", "content": [
                {"type": "tool_result", "tool_use_id": call_id if linked else "other-call",
                 "content": f"Image generated successfully and saved to /mnt/results/{filename}"},
            ]},
        ],
    }


def write_pdf(report: pathlib.Path, images: list[pathlib.Path], *, page_break_first: bool = False) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(report), pagesize=letter, pageCompression=0)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(54, 750, "Task Context")
    if page_break_first:
        pdf.showPage()
    for index, image in enumerate(images):
        pdf.drawImage(str(image), 54, 430 - index * 190, width=300, height=160)
    for row in range(150):
        pdf.drawString(54, 400 - (row % 35) * 9, f"Methods and evidence row {row:03d}")
        if row and row % 35 == 0:
            pdf.showPage()
    pdf.save()


def write_image(path: pathlib.Path, seed: int) -> None:
    from PIL import Image

    width, height = 420, 240
    pixels = [
        ((x * 17 + y * 7 + seed) % 256,
         (x * 3 + y * 19 + seed * 2) % 256,
         (x * 13 + y * 5 + seed * 3) % 256)
        for y in range(height) for x in range(width)
    ]
    image = Image.new("RGB", (width, height))
    image.putdata(pixels)
    image.save(path)


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        results.append((label, condition, detail or ("held" if condition else "did not hold")))

    def raises(label: str, fn, needle: str = "") -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - mutation suite checks public failure behavior
            check(label, not needle or needle in str(exc), f"{type(exc).__name__}: {exc}"[:140])
        else:
            check(label, False, "did not raise")

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        trace_dir = root / "execution_trace"
        trace_dir.mkdir()
        trace = trace_dir / "transcript.jsonl"
        qc = load_qc(root)

        trace.write_text(json.dumps(typed_trace("infographic.png"), indent=2), encoding="utf-8")
        evidence = qc.assert_generated_by_tool("infographic.png")
        check("pretty API envelope preserves exact same-id pairing",
              len(evidence) == 1 and evidence[0]["tool_call_id"] == "call-generate-1")

        flat = [
            {"type": "assistant", "tool_calls": [{"id": "flat-1", "name": "GenerateImage",
             "args": {"prompt": "Evidence flow", "filename": "flat.png"}}]},
            {"type": "tool", "tool_name": "GenerateImage", "tool_call_id": "flat-1",
             "id": "result-flat-1",
             "content": "Image generated successfully and saved to /mnt/results/flat.png"},
        ]
        flat_path = trace_dir / "flat.jsonl"
        flat_path.write_text("\n".join(json.dumps(item) for item in flat), encoding="utf-8")
        check("flattened trace with immutable ids is accepted",
              qc.assert_generated_by_tool("flat.png", transcript=flat_path)[0]["result_id"]
              == "result-flat-1")

        trace.write_text(json.dumps(typed_trace("infographic.png", linked=False)), encoding="utf-8")
        raises("unmatched result id fails closed",
               lambda: qc.assert_generated_by_tool("infographic.png"), "found 0")

        no_ids = [
            {"type": "assistant", "tool_calls": [{"name": "GenerateImage",
             "args": {"filename": "infographic.png"}}]},
            {"type": "tool", "tool_name": "GenerateImage",
             "content": "Image generated successfully and saved to /mnt/results/infographic.png"},
        ]
        trace.write_text("\n".join(json.dumps(item) for item in no_ids), encoding="utf-8")
        raises("flattened trace without join ids is rejected",
               lambda: qc.assert_generated_by_tool("infographic.png"), "found 0")

        trace.write_text(json.dumps(typed_trace("surreal.png")), encoding="utf-8")
        raises("a success for surreal.png never evidences real.png",
               lambda: qc.assert_generated_by_tool("real.png"), "real.png")

        trace.unlink()
        raises("missing transcript is blocking", lambda: qc.assert_generated_by_tool("x.png"),
               "missing")

        (root / "report.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 30_000)
        (root / "report.txt").write_text(
            "Task Context\nMethods & Sources\nResults\n"
            "Conclusions & Interpretation\nLimitations\n", encoding="utf-8")
        text_events = [{"type": "pdf_text_extraction", "report": "report.pdf",
                        "report_sha256": qc._sha256(root / "report.pdf"),
                        "artifact": "report.txt",
                        "artifact_sha256": qc._sha256(root / "report.txt")}]
        section_evidence = qc._report_sections_evidence(text_events, "report.pdf")
        check("extracted PDF text proves all five report sections in order",
              section_evidence["sections"] == list(qc.REPORT_SECTIONS))
        (root / "report.txt").write_text(
            "Contents\nTask Context\nMethods & Sources\nResults\n"
            "Conclusions & Interpretation\nLimitations\n\n"
            "Task Context\nMethods & Sources\nResults\n"
            "Conclusions & Interpretation\nLimitations\n", encoding="utf-8")
        text_events[0]["artifact_sha256"] = qc._sha256(root / "report.txt")
        duplicate_evidence = qc._report_sections_evidence(text_events, "report.pdf")
        check("table-of-contents repetitions do not impersonate duplicate top-level headings",
              duplicate_evidence["sections"] == list(qc.REPORT_SECTIONS))
        (root / "report.txt").write_text(
            "Table of Contents\nTask Context\nMethods & Sources\nResults\n"
            "Conclusions & Interpretation\nLimitations\n", encoding="utf-8")
        text_events[0]["artifact_sha256"] = qc._sha256(root / "report.txt")
        raises("table-of-contents entries cannot substitute for body sections",
               lambda: qc._report_sections_evidence(text_events, "report.pdf"),
               "body")
        (root / "report.txt").write_text(
            "Task Context\nMethods & Sources\nResults\nLimitations\n", encoding="utf-8")
        text_events[0]["artifact_sha256"] = qc._sha256(root / "report.txt")
        raises("missing Conclusions & Interpretation is blocking",
               lambda: qc._report_sections_evidence(text_events, "report.pdf"),
               "Conclusions & Interpretation")

        try:
            import PIL  # noqa: F401
            import pypdf  # noqa: F401
            import reportlab  # noqa: F401
        except ImportError as exc:
            check("PDF pixel-lineage tests are evaluable", False, f"missing {exc.name}")
        else:
            infographic = root / "infographic.png"
            other = root / "other.png"
            write_image(infographic, 11)
            write_image(other, 29)
            trace.write_text(json.dumps(typed_trace("infographic.png")), encoding="utf-8")
            qc.record_generated_infographic("infographic.png")
            _, snapshot_events = qc._load_qc_run_log("qc_run_log.json")
            trace_evidence = [{"requested_filename": "infographic.png", "tool_call_id": "x",
                               "result_id": "y", "returned_filename": "infographic.png"}]

            report = root / "report.pdf"
            write_pdf(report, [infographic])
            bound = qc.assert_infographic_pdf_lineage("report.pdf", trace_evidence)
            check("pixel-identical infographic is image 1 on page 1",
                  bound[0]["embedded_page"] == 1 and bound[0]["embedded_image_index"] == 1)
            bound_snapshot = qc.assert_infographic_snapshot_lineage(
                "report.pdf", ["infographic.png"], snapshot_events
            )
            check("boundary snapshot binds tool pixels to the final PDF",
                  bound_snapshot["items"][0]["embedded_page"] == 1)

            write_image(infographic, 17)
            write_pdf(report, [infographic])
            raises("replacing the image after GenerateImage snapshot is rejected",
                   lambda: qc.assert_infographic_snapshot_lineage(
                       "report.pdf", ["infographic.png"], snapshot_events),
                   "changed after snapshot")
            write_image(infographic, 11)

            write_pdf(report, [other, infographic])
            raises("an earlier image blocks infographic placement",
                   lambda: qc.assert_infographic_pdf_lineage("report.pdf", trace_evidence),
                   "image 2")

            write_pdf(report, [infographic], page_break_first=True)
            raises("an infographic first appearing on page 2 is rejected",
                   lambda: qc.assert_infographic_pdf_lineage("report.pdf", trace_evidence),
                   "page 2")

            write_pdf(report, [other])
            raises("a different embedded image cannot satisfy pixel identity",
                   lambda: qc.assert_infographic_pdf_lineage("report.pdf", trace_evidence),
                   "found 0")

        record = root / "record.json"
        record.write_text(json.dumps(GOOD_RECORD), encoding="utf-8")
        archetypes = (
            "analysis-workflow", "evidence-synthesis", "protocol-workflow",
            "correctness-guidance", "format-utility", "meta-tooling",
        )
        headings = (
            "`Task Context`", "`Methods & Sources`", "`Results`",
            "`Conclusions & Interpretation`", "`Limitations`",
        )
        semantics = {
            "analysis-workflow": "analysis design",
            "evidence-synthesis": "evidence-grading method",
            "protocol-workflow": "acceptance criteria",
            "correctness-guidance": "counterexamples",
            "format-utility": "transformed artifact",
            "meta-tooling": "tooling outcome",
        }
        for archetype in archetypes:
            run = subprocess.run([
                sys.executable, str(SCAFFOLD), "--slug", f"report-{archetype}",
                "--archetype", archetype, "--category", "general", "--record", str(record),
                "--facts-requirement", "required", "--dry-run",
            ], capture_output=True, text=True)
            missing = [heading for heading in headings if heading not in run.stdout]
            check(f"{archetype} gets the universal adapted report contract",
                  run.returncode == 0 and not missing and semantics[archetype] in run.stdout,
                  run.stderr.strip() or ("missing " + ", ".join(missing) if missing else "complete"))
            starting_prompt = next(
                (line for line in run.stdout.splitlines() if line.startswith("starting-prompt:")), ""
            )
            check(f"{archetype} keeps deliverable instructions out of the sample prompt",
                  all(heading.strip("`") not in starting_prompt for heading in headings),
                  starting_prompt[:120])

    width = max(len(label) for label, _, _ in results)
    for label, passed, detail in results:
        print(f"{label:{width}}  {'PASS' if passed else 'FAIL':4}  {detail}")
    failed = [row for row in results if not row[1]]
    print(f"RESULT: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
