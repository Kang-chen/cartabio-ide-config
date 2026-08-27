#!/usr/bin/env python3
"""Shared fixtures and mutation helpers for the phylo-create-skill eval modules."""

from __future__ import annotations

import json
import importlib.util
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import zlib

sys.dont_write_bytecode = True

HERE = pathlib.Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent                      # the phylo-create-skill package
SCAFFOLD = PKG_ROOT / "scripts" / "scaffold_skill.py"
CHECK = PKG_ROOT / "scripts" / "check_skill.py"
sys.path.insert(0, str(PKG_ROOT / "templates"))

GOOD_RECORD = {
    "q1": "A DESeq2 results CSV with columns gene_id, log2FoldChange, padj, baseMean.",
    "q2": "They rank by raw p-value and hand over the top 50, which mixes underpowered "
          "low-expression genes into the same list as real hits.",
    "q3": "A gene with a large log2FoldChange and baseMean under 10 counts looks like the "
          "strongest hit and is nearly always a near-zero-denominator artifact.",
    "q4": "Validated requires an independent cohort replicating direction at padj < 0.05.",
    "q5": "A target biologist choosing genes for a knockdown screen.",
    "q6": "User's own data plus Ensembl BioMart annotation, which is permissive.",
    "q7": "Nothing written yet.",
    "starting_task": {
        "user_prompt": "Which dexamethasone-responsive genes remain after adjusting for cell line?",
        "subject_input": "the Bioconductor airway RNA-seq demo count matrix and sample metadata",
        "objective": "Rank dexamethasone-responsive genes after fitting the declared design.",
        "decision_context": "Use design ~ cell_line + dex and compare treated versus untreated at FDR 0.05.",
        "deliverables": "report_demo.pdf, results_demo.csv, and report_facts.json.",
    },
    "deliverable_policy": {
        "audience": "user_facing",
        "report": {"required": True, "not_applicable_reason": ""},
        "infographic": {"required": True, "not_applicable_reason": ""},
    },
    "facts": {
        "runtime_payload_artifact": "facts_payload.json",
        "headline_definitions": [
            {"field": "n_tested", "operational_definition": "rows with a finite raw p-value"},
        ],
        "partition_groups": [
            {"name": "testing", "denominator_field": "n_input",
             "member_fields": ["n_tested", "n_not_tested"],
             "identity": "sum_members_equals_denominator"},
        ],
        "known_answer_eval_refs": ["assets/eval/test_demo.py"],
    },
    "clarification_branches": [
        {"question_id": "input", "choice_id": "airway-demo",
         "implementation_refs": ["scripts/report_qc.py"],
         "artifact_paths": ["results_demo.csv"], "fallback_status": "not_computable",
         "eval_refs": ["assets/eval/test_demo.py"]},
        {"question_id": "input", "choice_id": "provided-table",
         "implementation_refs": ["scripts/report_qc.py"],
         "artifact_paths": ["results_demo.csv"], "fallback_status": "not_computable",
         "eval_refs": ["assets/eval/test_demo.py"]},
    ],
    "clarification_questions": [{
        "id": "input", "prompt": "Which input should be analysed?", "selection_mode": "single",
        "choices": [{"id": "airway-demo", "label": "Airway demo"},
                    {"id": "provided-table", "label": "Provided result table"}],
    }],
    "runtime_instructions": {
        "inputs": ["A DESeq2 results CSV with gene_id, log2FoldChange, padj, and baseMean."],
        "workflow": [{"title": "Triage differential-expression hits",
                      "instruction": "Read the declared table, reject invalid columns, and write results_demo.csv."}],
        "caveats": [{"statement": "Low-count features can create unstable fold changes.",
                     "evidence_ref": "report_facts.json:n_low_count"}],
        "data_sources": [{"name": "Ensembl BioMart", "type": "annotation database",
                          "uri": "https://www.ensembl.org/biomart/", "version": "current at retrieval",
                          "license": "Ensembl terms", "commercial_status": "no_prohibition_found",
                          "commercial_evidence": "Terms URL reviewed at retrieval time",
                          "verification_ref": "input_manifest.json:annotation", "notes": "Version is recorded at runtime.",
                          "included": True}],
        "existing_materials": ["No pre-existing implementation; package scripts are authored fresh."],
    },
    "capabilities": {
        "trigger": "Use when a DESeq2 result table needs evidence-gated triage.",
        "catalog_claim_ids": ["triage"],
        "entries": [
            {"id": "triage", "claim": "Triage differential-expression hits.", "status": "tested",
             "implementation_refs": ["scripts/report_qc.py"],
             "eval_refs": ["assets/eval/test_demo.py"]},
        ],
    },
    "validation_matrix": {
        "auto": {"status": "not_run", "reason": "scaffold has not been run"},
        "guided": {"status": "not_run", "reason": "guided branch is deferred during authoring"},
    },
    "inference_readiness": {
        "applicable": False, "not_applicable_reason": "fixture triages an already fitted result table",
    },
    "external_dependencies": {
        "applicable": False, "not_applicable_reason": "fixture uses only bundled local inputs",
        "services": [],
    },
    "figures": {"applicable": True, "not_applicable_reason": ""},
    "execution": {"bundled_commands_applicable": True,
                  "bundled_file_refs": ["scripts/report_qc.py"],
                  "command_output_paths": ["results_demo.csv"],
                  "not_applicable_reason": ""},
    "source_assertions_not_applicable_reason": "fixture uses an author-provided result table",
    "resource_identity": {
        "applicable": False,
        "not_applicable_reason": "the fixture emits no externally identified resources",
    },
}

DESC = ("Triage differential-expression hits by separating real signal from low-expression "
        "artifacts. Use when handed a results table and asked which genes to take forward.")

# BF001 cannot see a dangling import: `from report_qc import ...` is not a bundled-file path. So the
# import lines of every generated python block are checked against what the scaffolder ships.
FENCE_RE = re.compile(r"```python\n(.*?)```", re.S)
IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()) or sys.builtin_module_names) | {
    "csv", "json", "os", "pathlib", "re", "shutil", "subprocess"}   # 3.9 has no stdlib_module_names

# The one-line double-quoted YAML grammar, which is the stdlib stand-in for the platform's real
# parser: CI's per-line regex accepts values that no YAML parser will load.
FM_DQ_RE = re.compile(r'[A-Za-z0-9_-]+: "(?:[^"\\]|\\.)*"')
NASTY_DESC = 'compare "A" versus "B" with a C:\\path\\to\\x and a trailing backslash \\'

results: list[tuple[str, str, str]] = []


# Hand-built image/PDF fixtures keep the mutation suite independent of reportlab while remaining
# valid to Pillow and pypdf when those optional libraries are present on the platform.
FIXTURE_REQUIRED = "#13579B"
FIXTURE_SUPPORTING = (
    "#2468AC", "#3579BD", "#468ACE", "#579BDF", "#68ACE0", "#79BDF1"
)
MARK = FIXTURE_SUPPORTING[1]


def png_bytes(width: int = 64, height: int = 64) -> bytes:
    """Return a deterministic, non-blank PNG that Pillow and byte-only gates both accept."""
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        for x in range(width):
            scanlines.extend(((x * 3 + y) % 256, (x + y * 5) % 256, (x * 7 + y * 11) % 256))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(scanlines), level=0))
            + chunk(b"IEND", b""))


def stream_obj(payload: bytes, filters: str = "", dictionary: str = "") -> bytes:
    d = f"<< /Length {len(payload)}" + (f" {dictionary}" if dictionary else "")
    d += f" /Filter {filters}" if filters else ""
    return d.encode() + b" >>\nstream\n" + payload + b"\nendstream"


def pdf_bytes(objects: list[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1) + b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    return bytes(out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                 % (len(objects) + 1, start))


def write_pdf(path: pathlib.Path, payload: bytes, filters: str = "",
              extra: tuple[bytes, ...] = (), embedded_image: bool = False,
              text_lines: tuple[str, ...] = ()) -> pathlib.Path:
    resources = []
    if embedded_image:
        payload = b"q 16 0 0 16 10 10 cm /Im0 Do Q\n" + payload
        image = stream_obj(
            b"\xff\x00\x00",
            dictionary=("/Type /XObject /Subtype /Image /Width 1 /Height 1 "
                        "/ColorSpace /DeviceRGB /BitsPerComponent 8"),
        )
        extra = (image, *extra)
        resources.append(b"/XObject << /Im0 5 0 R >>")
    if text_lines:
        escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                   for line in text_lines]
        text = ("BT /F1 12 Tf 72 720 Td 16 TL "
                + " ".join(f"({line}) Tj T*" for line in escaped) + " ET\n").encode("latin-1")
        payload = text + payload
        font_number = 5 + len(extra)
        extra = (*extra, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        resources.append(f"/Font << /F1 {font_number} 0 R >>".encode("ascii"))
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
    if resources:
        page += b" /Resources << " + b" ".join(resources) + b" >>"
    page += b" >>"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", page,
               stream_obj(payload, filters)]
    path.write_bytes(pdf_bytes(objects + list(extra)))
    return path


def rg(*hexes: str) -> bytes:
    return b"\n".join(b"%f %f %f rg 10 10 80 40 re f"
                       % tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)) for h in hexes)


def qc_variant(dest: pathlib.Path, mount: pathlib.Path, old: str, new: str):
    """Import a report_qc copy with one mutation so a test proves a line is load-bearing."""
    src = (PKG_ROOT / "templates" / "report_qc.py").read_text(encoding="utf-8")
    if old not in src:
        raise RuntimeError(f"report_qc.py no longer contains {old[:44]!r}, cannot mutate")
    dest.write_text(src.replace(old, new, 1), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(dest.stem, dest)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.RESULTS = mount
    return mod


def run_check(pkg: pathlib.Path, extra: list[str] | None = None) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(CHECK), str(pkg), "--contract", "A", *(extra or [])],
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def scaffold(dest: pathlib.Path, slug: str, archetype: str = "analysis-workflow",
             record: dict | None = None, extra: list[str] | None = None) -> pathlib.Path:
    dest.mkdir(parents=True, exist_ok=True)
    rec = dest / f"{slug}.json"
    payload = {**GOOD_RECORD, **(record or {})}
    if archetype != "analysis-workflow" and not (record or {}).get("figures"):
        payload["figures"] = {"applicable": False,
                              "not_applicable_reason": "this fixture has no result figure"}
    rec.write_text(json.dumps(payload), encoding="utf-8")
    cmd = [sys.executable, str(SCAFFOLD), "--slug", slug, "--archetype", archetype,
           "--category", "transcriptomics", "--record", str(rec),
           "--dest", str(dest / "skills"), "--description", DESC,
           "--facts-requirement", "required"]
    p = subprocess.run(cmd + (extra or []), capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"scaffold failed for {slug}: {p.stderr}")
    pkg = dest / "skills" / slug
    eval_file = pkg / "assets" / "eval" / "test_demo.py"
    eval_file.parent.mkdir(parents=True, exist_ok=True)
    eval_file.write_text("# known-answer branch fixture\n", encoding="utf-8")
    return pkg


TODO_RE = re.compile(r"<!-- TODO\(author\): (\w+) unanswered[^>]*-->")


def complete(dest: pathlib.Path, slug: str, **kw) -> pathlib.Path:
    """A scaffolded package with the author's part done, for every row that needs a clean baseline.

    A fresh scaffold deliberately does NOT pass the gate: the workflow step, the machine-readable
    output and the figure contract are things only the author can supply, and the scaffolder blocks
    them rather than inventing plausible text that the gate would then bless. So a "clean package"
    fixture has to be built the way a real author builds one — resolve the markers, then derive the
    figure table from the steps that now exist. Anything left unresolved keeps its marker and TF001
    keeps blocking, which is what the control row asserts.
    """
    pkg = scaffold(dest, slug, **kw)
    md = pkg / "SKILL.md"
    t = md.read_text(encoding="utf-8")
    # No bundled path here: naming scripts/analyse.py would make BF001 fire on a file the fixture
    # never ships, which is that rule working correctly and this helper being wrong.
    t = TODO_RE.sub(lambda m: {
        "STEP2": "Fits the reference profiles against each mixture and writes the table below.",
        "OUTPUTS": "`results_demo.csv` — one row per sample, with its fitted proportion",
        "RECEIPT": "The lists below name what this run executes and writes.",
    }.get(m.group(1), m.group(0)), t)
    # RC009 blocks on the empty lists the scaffolder ships, and deleting the marker does not clear
    # it — that is the point of the rule. An author fills them; so does this.
    t = t.replace("bundled_files=[],", 'bundled_files=["scripts/report_qc.py"],')
    t = t.replace("outputs=[],", 'outputs=["results_demo.csv"],')
    md.write_text(t, encoding="utf-8")

    if "## Figures" in t:
        subprocess.run([sys.executable, str(SCAFFOLD), "--figures-from-steps", str(pkg)],
                       capture_output=True, text=True)
        t = md.read_text(encoding="utf-8")
        md.write_text(TODO_RE.sub(
            lambda m: ("the fitted proportion per sample, with the residual"
                       if m.group(1).startswith("FIGURE") else m.group(0)), t), encoding="utf-8")
    return pkg


def expect_fail(name: str, pkg: pathlib.Path, rule: str) -> None:
    code, out = run_check(pkg)
    if code == 1 and rule in out:
        results.append((name, "PASS", f"{rule} fired"))
    elif code == 1:
        results.append((name, "FAIL", f"blocked, but not by {rule}"))
    else:
        results.append((name, "FAIL", f"{rule} did NOT fire (exit {code}) — this check is asleep"))


def expect_pass(name: str, pkg: pathlib.Path) -> None:
    code, out = run_check(pkg)
    if code in (0, 2):
        results.append((name, "PASS", "clean"))
    else:
        first = next((ln.strip() for ln in out.splitlines() if "FAIL" in ln), "?")
        results.append((name, "FAIL", f"false positive: {first}"))


def expect_quiet(name: str, pkg: pathlib.Path, rule: str) -> None:
    """The other half of a mutation test. A rule that fires on a package the fleet already ships is
    deleted by the first person who runs it, and takes its real catches with it."""
    code, out = run_check(pkg)
    if rule in out:
        results.append((name, "FAIL", f"{rule} false positive"))
    elif code in (0, 2):
        results.append((name, "PASS", f"no {rule} (exit {code})"))
    else:
        first = next((ln.strip() for ln in out.splitlines() if "FAIL" in ln), "?")
        results.append((name, "FAIL", f"blocked by something else: {first}"))


def expect_receipt_fail(name: str, pkg: pathlib.Path, data: dict, needle: str) -> None:
    """A receipt that does not prove an outcome must block, and must say which outcome."""
    (pkg / "run_receipt.json").write_text(json.dumps(data), encoding="utf-8")
    code, out = run_check(pkg, ["--require-run-receipt"])
    if code == 1 and needle in out:
        results.append((name, "PASS", f"blocked on {needle!r}"))
    elif code == 1:
        results.append((name, "FAIL", f"blocked, but never said {needle!r}"))
    else:
        results.append((name, "FAIL", f"receipt accepted (exit {code}) — RR001 is asleep"))


def gate_copy(dest: pathlib.Path) -> pathlib.Path:
    """A degrade test has to take away the data a rule loads. It takes it away from a COPY: `assets/`
    is readable by the agent running the skill, so a half-dismantled package here is one it could
    copy — and a test that dies mid-way would leave exactly that."""
    shutil.copytree(PKG_ROOT, dest)
    return dest


def run_gate(gate: pathlib.Path, pkg: pathlib.Path,
             extra: list[str] | None = None) -> tuple[int, str]:
    """run_check against a different checker: the only tests here that do not use the real one."""
    p = subprocess.run([sys.executable, str(gate / "scripts" / "check_skill.py"), str(pkg),
                        "--contract", "A", *(extra or [])], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def expect_degrade(name: str, gate: pathlib.Path, pkg: pathlib.Path, *needles: str,
                   n: int = 1) -> None:
    """A rule whose data did not load must exit 3 and name the rule that went blind. Exit 0 here is
    the whole defect: a gate advertising a pass over rules that were never applied."""
    code, out = run_gate(gate, pkg)
    # Finding lines only. The RESULT line names --explain DG001, so a bare substring count runs one
    # high everywhere, and the two-blind-loaders case would pass with only one loader blind.
    said = sum(1 for ln in out.splitlines() if "DEGRADED" in ln and "DG001" in ln)
    silent = [x for x in needles if x not in out]
    if code != 3:
        results.append((name, "FAIL", f"exit {code}, not 3"))
    elif said != n:
        results.append((name, "FAIL", f"{said} DG001 line(s), expected {n}"))
    elif silent:
        results.append((name, "FAIL", f"never said {silent[0]!r}"))
    elif "GATE PASSED" in out:
        results.append((name, "FAIL", "GATE PASSED with a rule blind"))
    else:
        results.append((name, "PASS", f"exit 3, {said} DG001"))


def caches(root: pathlib.Path) -> list[pathlib.Path]:
    """The artifact PK001 blocks on, wherever it landed."""
    return [p for p in root.rglob("*")
            if p.name == "__pycache__" or p.suffix in (".pyc", ".pyo")]


def under(tmp: pathlib.Path, p: pathlib.Path) -> str:
    """Short path for the detail column. Must not raise: the bug being hunted returns a path that is
    outside tmp entirely, and a crash here would take the whole report down with it."""
    s = str(p)
    return s[len(str(tmp)) + 1:] if s.startswith(str(tmp) + os.sep) else s


def yaml_unescape(body: str) -> str:
    """Inverse of yaml_dq's escaping, so an emitted scalar can be compared with what went in."""
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)),
                  body)


def edit(pkg: pathlib.Path, old: str, new: str) -> None:
    md = pkg / "SKILL.md"
    t = md.read_text(encoding="utf-8")
    if old not in t:
        raise RuntimeError(f"fixture text not found, cannot mutate: {old[:50]!r}")
    md.write_text(t.replace(old, new, 1), encoding="utf-8")


def set_fm(pkg: pathlib.Path, key: str, raw: str) -> None:
    """Overwrite one frontmatter line with raw bytes. Routing it through yaml_dq would escape exactly
    the breakage under test, which is why a hand-written file is the only place this shape occurs."""
    md = pkg / "SKILL.md"
    lines = md.read_text(encoding="utf-8").splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith(f"{key}:"))
    lines[i] = raw
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
