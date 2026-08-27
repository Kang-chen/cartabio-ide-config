#!/usr/bin/env python3
"""Report QC gates for a generated Biomni skill. Copy into the skill's scripts/ and CALL them.

A gate is wired only when a script calls it. Several shipped skills define validators that nothing
invokes — those are documentation, not gates. Call these from your export step.

    from report_qc import assert_generated_by_tool, assert_report_styled, staged_copy

Every deliverable path taken by this module is resolved against RESULTS when relative: the working
directory is /workspace but deliverables must land on the results mount, so a bare "report_facts.json"
written relative to the CWD is invisible to the report builder and to the user.

Stdlib only except assert_figure_ok, which needs Pillow or numpy (it degrades to a size check).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import zlib

import report_style as _report_style

RESULTS = pathlib.Path(os.environ.get("BIOMNI_RESULTS", "/mnt/results"))
_TRANSCRIPT_REL = pathlib.Path("execution_trace") / "transcript.jsonl"   # resolved at call time
TRANSCRIPT = RESULTS / _TRANSCRIPT_REL


class GateFailure(AssertionError):
    """Raised before any facts artifact is written, so a failing run produces no quotable numbers."""


def _at_results(path: str | pathlib.Path) -> pathlib.Path:
    """A relative path names the results mount, not the CWD. Absolute paths are honoured as given.

    Reads RESULTS at call time so a caller (or a test) can repoint the mount.
    """
    p = pathlib.Path(path)
    return p if p.is_absolute() else RESULTS / p


def _resolved_results_artifact(path: str | pathlib.Path, *, strict: bool) -> pathlib.Path:
    """Return an artifact path only when its resolved target stays beneath RESULTS."""
    candidate = _at_results(path)
    try:
        resolved_root = RESULTS.resolve(strict=True)
        candidate.resolve(strict=strict).relative_to(resolved_root)
    except (FileNotFoundError, ValueError) as exc:
        raise GateFailure(
            f"artifact {_at_results(path)} does not resolve beneath the results root"
        ) from exc
    return candidate


# --- the infographic honesty gate ----------------------------------------------------------------

def _record_text(line: str) -> str:
    """One transcript record flattened to searchable text."""
    try:
        return json.dumps(json.loads(line), ensure_ascii=False)
    except ValueError:
        return line


# Anything that cannot appear inside a path. Backslash also separates JSON-escaped line breaks from
# a preceding filename, so a multi-line tool result cannot glue the basename to its next field.
_TOKEN_SPLIT_RE = re.compile(r"""[\s"',;:()\[\]{}<>|`=\\]+""")


def _basenames_in(record: str) -> set[str]:
    """Every path-like token in one record, reduced to its basename.

    Substring matching is not usable here: `surreal.png` contains `real.png`, so a success for the
    first would evidence a request for the second. Tokenise, then compare basenames exactly. Reading
    tokens rather than parsing one fixed success sentence means a reworded tool message yields no
    basenames and the caller raises — the failure is loud, never a silent pass.
    """
    out: set[str] = set()
    for tok in _TOKEN_SPLIT_RE.split(record):
        tok = tok.strip().rstrip(".,")
        if not tok:
            continue
        base = os.path.basename(tok.replace("\\", "/"))
        if base:
            out.add(base)
    return out


def _content_blocks(value, *, message_id: str | None = None) -> list[dict]:
    """Return typed tool blocks in serialized order, including task-message API envelopes."""
    if isinstance(value, list):
        blocks: list[dict] = []
        for index, item in enumerate(value):
            nested = _content_blocks(item, message_id=message_id)
            for block in nested:
                block.setdefault("_content_index", index)
            blocks.extend(nested)
        return blocks
    if not isinstance(value, dict):
        return []
    if value.get("type") in {"tool_use", "tool_result", "tool"} or isinstance(
        value.get("tool_calls"), list
    ):
        record = dict(value)
        if message_id is not None:
            record.setdefault("_message_id", message_id)
        return [record]
    content = value.get("content")
    if isinstance(content, (list, dict)):
        return _content_blocks(content, message_id=str(value.get("id") or message_id or "") or None)
    if value.get("type") == "list" and isinstance(value.get("data"), list):
        return _content_blocks(value["data"], message_id=message_id)
    return []


def _transcript_records(path: pathlib.Path) -> list[dict]:
    """Parse a pretty JSON API response or JSONL mount without inventing missing join ids."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        return _content_blocks(json.loads(raw))
    except ValueError:
        records: list[dict] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                records.extend(_content_blocks(json.loads(line)))
            except ValueError:
                continue
        return records


def _generateimage_calls(record: dict) -> list[dict]:
    """Normalize typed and flattened GenerateImage calls that carry immutable ids."""
    if record.get("type") == "tool_use" and record.get("name") == "GenerateImage":
        args = record.get("input")
        call_id = record.get("id")
        if isinstance(args, dict) and call_id:
            return [{"tool_call_id": str(call_id), "args": args}]
        return []
    calls = []
    for item in record.get("tool_calls", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        args = item.get("args")
        function = item.get("function")
        if isinstance(function, dict):
            name = name or function.get("name")
            args = args if args is not None else function.get("arguments")
        if name != "GenerateImage":
            continue
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = None
        call_id = item.get("id") or item.get("tool_call_id")
        if call_id and isinstance(args, dict):
            calls.append({"tool_call_id": str(call_id), "args": args})
    return calls


def assert_generated_by_tool(*filenames: str, transcript: pathlib.Path | None = None) -> list[dict]:
    """Return evidence for one exact same-id GenerateImage call/result pair per filename.

    A requested filename elsewhere in a prompt and an unrelated success message never combine into
    provenance. Pretty task-message API envelopes and mounted JSONL traces are both supported, but
    flattened records without immutable call/result join ids fail closed.
    """
    path = _at_results(transcript if transcript is not None else _TRANSCRIPT_REL)
    if not path.exists():
        raise GateFailure(
            f"cannot verify infographic provenance: {path} is missing. "
            "Do not claim a GenerateImage infographic you cannot evidence."
        )

    records = _transcript_records(path)
    calls: dict[str, dict] = {}
    for record in records:
        for call in _generateimage_calls(record):
            call_id = call["tool_call_id"]
            if call_id in calls:
                raise GateFailure(f"duplicate GenerateImage tool-call id {call_id!r}")
            calls[call_id] = call

    results: dict[str, list[dict]] = {}
    for record in records:
        typed = record.get("type") == "tool_result"
        flat = record.get("type") == "tool" and record.get("tool_name") == "GenerateImage"
        if not (typed or flat):
            continue
        call_id = record.get("tool_use_id") or record.get("tool_call_id") or record.get("call_id")
        if not call_id:
            continue
        result_id = record.get("id") or record.get("result_id")
        if not result_id and record.get("_message_id"):
            result_id = f"{record['_message_id']}#content-{record.get('_content_index', 0)}"
        if not result_id and typed:
            result_id = f"tool_result:{call_id}"
        text = _record_text(json.dumps(record, ensure_ascii=False))
        if result_id and "generated successfully" in text.lower():
            results.setdefault(str(call_id), []).append({
                "result_id": str(result_id), "returned_filenames": _basenames_in(text),
            })

    evidence = []
    for filename in [os.path.basename(fn) for fn in filenames]:
        candidates = []
        for call_id, call in calls.items():
            args = call["args"]
            requested = args.get("file_name") or args.get("filename") or args.get("output_filename")
            if not isinstance(requested, str) or os.path.basename(requested) != filename:
                continue
            for result in results.get(call_id, []):
                if filename in result["returned_filenames"]:
                    prompt = args.get("prompt")
                    candidates.append({
                        "tool_call_id": call_id,
                        "result_id": result["result_id"],
                        "requested_filename": filename,
                        "returned_filename": filename,
                        "prompt_sha256": hashlib.sha256(str(prompt).strip().encode()).hexdigest()
                        if isinstance(prompt, str) and prompt.strip() else None,
                    })
        if len(candidates) != 1:
            raise GateFailure(
                f"need exactly one same-id GenerateImage call/result pair for {filename}; "
                f"found {len(candidates)}"
            )
        evidence.append(candidates[0])
    return evidence


def _decoded_pixel_sha256_from_image(image) -> str:
    rgb = image.convert("RGB")
    payload = rgb.width.to_bytes(4, "big") + rgb.height.to_bytes(4, "big") + rgb.tobytes()
    return hashlib.sha256(payload).hexdigest()


def _decoded_pixel_sha256(path: pathlib.Path) -> str:
    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise GateFailure("Pillow unavailable; infographic pixel identity is not_evaluable") from exc
    with Image.open(path) as image:
        return _decoded_pixel_sha256_from_image(image)


def assert_infographic_pdf_lineage(report_name: str, trace_evidence: list[dict]) -> list[dict]:
    """Bind exact generated pixels to exactly one first image on page 1 of the final PDF."""
    try:
        import pypdf  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise GateFailure("PDF/image extractor unavailable; infographic lineage is not_evaluable") from exc
    report = assert_report_exists(report_name)
    reader = pypdf.PdfReader(str(report))
    bound = []
    for trace in trace_evidence:
        filename = trace["requested_filename"]
        image_path = _at_results(filename)
        if not image_path.is_file():
            raise GateFailure(f"generated infographic missing: {image_path}")
        pixel_hash = _decoded_pixel_sha256(image_path)
        matches = []
        for page_number, page in enumerate(reader.pages, 1):
            try:
                content = pypdf.generic.ContentStream(page.get_contents(), reader)
                draw_order = [str(operands[0]) for operands, operator in content.operations
                              if operator == b"Do" and operands]
            except Exception as exc:
                raise GateFailure(
                    f"cannot determine PDF image draw order on page {page_number}: {exc}"
                ) from exc
            for embedded in getattr(page, "images", []) or []:
                try:
                    embedded_image = (embedded.image if hasattr(embedded, "image")
                                      else Image.open(io.BytesIO(embedded.data)))
                    embedded_hash = _decoded_pixel_sha256_from_image(embedded_image)
                except (AttributeError, OSError, TypeError, ValueError):
                    continue
                if embedded_hash == pixel_hash:
                    resource_name = "/" + str(embedded.name).rsplit(".", 1)[0]
                    positions = [i + 1 for i, name in enumerate(draw_order) if name == resource_name]
                    if len(positions) != 1:
                        raise GateFailure(
                            f"cannot bind {filename} to one PDF draw operation on page {page_number}"
                        )
                    matches.append((page_number, positions[0], embedded_hash))
        if len(matches) != 1:
            raise GateFailure(
                f"expected exactly one pixel-identical PDF embedding for {filename}, found {len(matches)}"
            )
        page_number, image_index, embedded_hash = matches[0]
        if page_number != 1 or image_index != 1:
            raise GateFailure(
                "infographic must be the first substantive visual on page 1; "
                f"found page {page_number}, image {image_index}"
            )
        bound.append({
            **trace,
            "generated_image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
            "generated_decoded_pixel_sha256": pixel_hash,
            "final_pdf_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            "embedded_page": page_number,
            "embedded_image_index": image_index,
            "embedded_decoded_pixel_sha256": embedded_hash,
        })
    return bound


# --- figure sanity -------------------------------------------------------------------------------

def assert_report_exists(report_name: str, min_bytes: int = 20_000) -> pathlib.Path:
    """Fail unless the report was actually produced, at the results root, under its declared name.

    Why this exists: the platform's own guidance tells an agent not to create file deliverables for
    simple queries or single analyses. A skill that declares a mandatory report is therefore asking
    for something the agent may reasonably decide to skip — and without this assertion the run then
    finishes *successfully* with no deliverable and nobody notices.

    This does not stop an agent skipping the step. It stops the run quietly succeeding when it did.
    Call it as the last thing the skill does.
    """
    base = os.path.basename(report_name)
    path = RESULTS / base
    if not path.exists():
        raise GateFailure(
            f"the run is not complete: {base} was not produced at {RESULTS}/. "
            "The report is a required deliverable of this skill, not an optional extra. "
            "Generate it before finishing, or report plainly that the run failed and why."
        )
    size = path.stat().st_size
    if size < min_bytes:
        raise GateFailure(
            f"{base} is only {size} B — a report of a multi-step analysis that small is a "
            "rendering failure, not a concise summary. Check the build for a silent error."
        )
    return path


def assert_figures(manifest: str | pathlib.Path | list[dict]) -> list[dict]:
    """Every declared figure must exist and be non-blank. Returns the manifest for the facts file.

    Call this BEFORE writing report_facts.json, so a run that produced a blank figure stops instead
    of shipping a report that points at it. The returned list is what the report should read its
    figure inventory from — a report that lists figures by reading this cannot claim one it never
    produced, or quietly drop one it did.

    A manifest entry is {"step": 2, "file": "figures/figure_2_qc.png", "caption": "..."}. The
    manifest and every existing figure must resolve beneath RESULTS, including after symlinks.
    """
    if isinstance(manifest, (str, pathlib.Path)):
        try:
            p = _resolved_results_artifact(manifest, strict=True)
        except GateFailure as exc:
            raise GateFailure(
                f"figure manifest {_at_results(manifest)} is missing or outside the results root. Declare one "
                "entry per analysis step; if a step "
                "genuinely has nothing to plot, record it with \"file\": null and a reason."
            ) from exc
        if not p.is_file():
            raise GateFailure(f"figure manifest is not a file: {p}")
        entries = json.loads(p.read_text(encoding="utf-8"))
    else:
        entries = manifest

    if not isinstance(entries, list) or not entries:
        raise GateFailure("figure manifest is empty — an analysis with no figure shows the reader nothing")

    checked = []
    for e in entries:
        fn = e.get("file")
        if fn is None:                       # an explicit, reasoned absence is allowed
            if not e.get("reason"):
                raise GateFailure(f"step {e.get('step')!r} declares no figure and gives no reason")
            checked.append(e)
            continue
        assert_figure_ok(fn)
        if not str(e.get("caption", "")).strip():
            raise GateFailure(
                f"{fn} has no caption. State what the figure shows, not what it is called."
            )
        checked.append(e)
    return checked


def report_embeds_figures(pdf_path: str | pathlib.Path, figures: list[dict]) -> tuple[bool, str]:
    """Soft check: does the built report actually contain the declared figures?

    Returns (ok, detail) rather than raising — this is the deliberately soft half of the figure rule.
    Degrades to 'not evaluable' when no PDF library is present, and 'not evaluable' is never a pass.
    A relative `pdf_path` is resolved against RESULTS, where the report has to sit.
    """
    expected = [e for e in figures if e.get("file")]
    if not expected:
        return True, "no figures declared"
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(str(_at_results(pdf_path)))
        n = sum(len(getattr(page, "images", []) or []) for page in reader.pages)
    except Exception as exc:                 # noqa: BLE001 - any failure is 'not evaluable'
        return False, f"NOT-EVALUABLE: could not count embedded images ({type(exc).__name__})"
    if n < len(expected):
        return False, f"report embeds {n} image(s) but {len(expected)} figure(s) were declared"
    return True, f"report embeds {n} image(s) for {len(expected)} declared figure(s)"


def assert_figure_ok(path: str | pathlib.Path, min_bytes: int = 5_000) -> pathlib.Path:
    """Fail on a blank or degenerate figure. A blank plot passing QC is a gate that cannot fail.

    A relative path is resolved against RESULTS.
    """
    p = _resolved_results_artifact(path, strict=True)
    if not p.exists():
        raise GateFailure(f"figure not written: {p}")
    size = p.stat().st_size
    if size < min_bytes:
        raise GateFailure(f"figure {p.name} is {size} B — almost certainly blank")

    if p.suffix.lower() == ".svg":
        return p
    try:
        from PIL import Image  # type: ignore

        with Image.open(p) as im:
            extrema = im.convert("L").getextrema()
        if extrema[0] == extrema[1]:
            raise GateFailure(f"figure {p.name} has a single pixel value — it is blank")
    except ImportError:
        pass  # size check already ran; do not install anything into a live session
    return p


# --- the report-style gate ----------------------------------------------------------------------

REPORT_STYLE_SCHEMA = _report_style.REPORT_STYLE_SCHEMA
_SYSTEM_STYLE_ROOT = pathlib.Path("/mnt/skills/system")
_USER_STYLE_ROOT = pathlib.Path("/mnt/skills/user")
_PERSONAL_STYLE_ROOT = pathlib.Path("/mnt/skills/personal")


def _style_roots(*, allow_personal: bool) -> tuple[pathlib.Path, ...]:
    """Return installed, read-only skill roots; callers cannot substitute a workspace profile."""
    if allow_personal:
        return (_SYSTEM_STYLE_ROOT, _USER_STYLE_ROOT, _PERSONAL_STYLE_ROOT)
    return (_SYSTEM_STYLE_ROOT,)


def _rgb(hexv: str) -> tuple[int, int, int]:
    return int(hexv[1:3], 16), int(hexv[3:5], 16), int(hexv[5:7], 16)


def _resolved_style_provider(
    style_provider: str | None,
    *,
    allow_personal: bool = False,
    activation_hint: str | None = None,
) -> tuple[dict, pathlib.Path, dict]:
    try:
        return _report_style.resolve_provider(
            style_provider,
            _style_roots(allow_personal=allow_personal),
            activation_hint=activation_hint,
        )
    except _report_style.StyleProviderError as exc:
        raise GateFailure(str(exc)) from exc


def report_style_profile(
    style_provider: str | None,
    *,
    allow_personal: bool = False,
) -> tuple[dict, pathlib.Path]:
    """Load a provider profile from structured data or its installed SKILL.md."""
    profile, path, _ = _resolved_style_provider(
        style_provider,
        allow_personal=allow_personal,
    )
    return profile, path


def _selected_style_from_transcript() -> tuple[str | None, dict | None]:
    """Derive the current explicit provider solely from immutable user-message evidence."""
    transcript = _resolved_results_artifact(_TRANSCRIPT_REL, strict=True)
    try:
        return _report_style.selected_style_from_transcript(
            transcript,
            _style_roots(allow_personal=True),
            _TRANSCRIPT_REL.as_posix(),
        )
    except _report_style.StyleProviderError as exc:
        raise GateFailure(str(exc)) from exc


# Content streams are normally compressed, and ReportLab's default is /Filter [/ASCII85Decode
# /FlateDecode] — a85 stacked OVER flate. Decoding only the flate half yields zero bytes, which reads
# as "this PDF has no colour operators at all" and is a parser bug wearing a finding's clothes.
_STREAM_RE = re.compile(rb"(?<!end)stream\r?\n")     # 'endstream' contains 'stream'; do not match it
_FILTER_RE = re.compile(rb"/Filter\s*(/[A-Za-z0-9]+|\[[^\]]*\])")
_NUM = rb"[-+]?(?:\d{1,12}(?:\.\d{0,12})?|\.\d{1,12})"   # bounded: unbounded \d+ backtracks on noise
_RGB_RE = re.compile(rb"(?<![\w.])(%s)\s+(%s)\s+(%s)\s+(?:rg|RG)(?![\w.])" % (_NUM, _NUM, _NUM))
_GRAY_RE = re.compile(rb"(?<![\w.])(%s)\s+(?:g|G)(?![\w.])" % _NUM)
_CMYK_RE = re.compile(rb"(?<![\w.])(%s)\s+(%s)\s+(%s)\s+(%s)\s+(?:k|K)(?![\w.])" % ((_NUM,) * 4))
_NONTEXT_RE = re.compile(rb"[^\t\r\n\x20-\x7e]")


def _flate(b: bytes) -> bytes:
    """decompressobj, not decompress: a stream padded to /Length makes the one-shot form raise."""
    for wbits in (15, -15):                  # -15 = raw deflate, no zlib header
        obj = zlib.decompressobj(wbits)
        try:
            out = obj.decompress(b) + obj.flush()
        except zlib.error:
            continue
        if out:
            return out
    raise zlib.error("not a flate stream")


def _a85(b: bytes) -> bytes:
    b = re.sub(rb"\s", b"", b)
    stop = b.find(b"~>")                     # Adobe EOD; a85decode chokes on it
    return base64.a85decode(b[:stop] if stop >= 0 else b)


def _ahx(b: bytes) -> bytes:
    h = re.sub(rb"[^0-9A-Fa-f]", b"", b.split(b">")[0])
    return binascii.unhexlify(h + b"0" * (len(h) % 2))   # spec: a lone final digit is padded with 0


_DECODERS = {b"FlateDecode": _flate, b"Fl": _flate, b"ASCII85Decode": _a85, b"A85": _a85,
             b"ASCIIHexDecode": _ahx, b"AHx": _ahx}


def _content_streams(data: bytes) -> list[bytes]:
    """Every stream that decodes to something text-shaped. Images and font programs are skipped.

    Never parses the xref or follows /Contents: a colour operator anywhere in the drawing operators
    counts, and an object graph is a great deal of code to reach the same answer. Binary payloads are
    dropped twice over — by their object dict, then by a printability test — because regexing image
    bytes for `rg` costs minutes and finds noise.
    """
    out = []
    for m in _STREAM_RE.finditer(data):
        head = data[data.rfind(b"obj", 0, m.start()):m.start()]
        if b"/Image" in head or b"/Length1" in head or b"FontFile" in head:
            continue
        end = data.find(b"endstream", m.end())
        if end < 0:
            continue
        raw = data[m.end():end]
        if raw.endswith(b"\r\n"):            # the EOL before 'endstream' is not stream data
            raw = raw[:-2]
        elif raw[-1:] in (b"\n", b"\r"):
            raw = raw[:-1]

        fm = _FILTER_RE.search(head)         # /Filter is often an ARRAY, applied left to right
        names = re.findall(rb"/([A-Za-z0-9]+)", fm.group(1)) if fm else []
        try:
            for name in names:
                raw = _DECODERS[name](raw)   # KeyError = a filter we cannot undo, e.g. DCTDecode
        except (KeyError, ValueError, zlib.error, binascii.Error):
            continue
        sample = raw[:4096]
        if len(_NONTEXT_RE.findall(sample)) > len(sample) // 10:
            continue
        out.append(raw)
    return out


def pdf_colors(pdf_path: str | pathlib.Path) -> dict[str, int]:
    """Distinct colours set by a PDF's content streams, as {"#RRGGBB": times set}.

    Stdlib only, so this always reaches a verdict — unlike report_embeds_figures, which needs pypdf.
    A relative path is resolved against RESULTS.
    """
    data = _at_results(pdf_path).read_bytes()
    counts: dict[str, int] = {}

    def add(rgb: tuple[float, ...]) -> None:
        chans = tuple(round(v * 255) for v in rgb)
        if all(0 <= c <= 255 for c in chans):
            key = "#%02X%02X%02X" % chans
            counts[key] = counts.get(key, 0) + 1

    for stream in _content_streams(data):
        for m in _RGB_RE.finditer(stream):
            add(tuple(float(m.group(i)) for i in (1, 2, 3)))
        for m in _GRAY_RE.finditer(stream):
            add((float(m.group(1)),) * 3)
        # Provider profiles use RGB hex, so this is only a safety net against a CMYK producer failing
        # the gate. (1-v)(1-k) is what a reader renders DeviceCMYK as, and it preserves exact RGB;
        # ReportLab's own cmyk2rgb uses 1-min(1,v+k) instead, which does not.
        for m in _CMYK_RE.finditer(stream):
            k = float(m.group(4))
            add(tuple((1 - float(m.group(i))) * (1 - k) for i in (1, 2, 3)))
    return counts


def _within(hexv: str, target: tuple[int, int, int], tol: int) -> bool:
    """All three channels or nothing. ReportLab writes 6 decimals (.831373 .627451 .290196), which
    round-trips exactly; 2 units absorbs a producer that writes only 2 or 3.

    Per channel, never a luminance or single-channel match: two unrelated colors can be close in one
    channel, so accepting any channel would produce false style matches.
    """
    chans = (int(hexv[1:3], 16), int(hexv[3:5], 16), int(hexv[5:7], 16))
    return all(abs(a - b) <= tol for a, b in zip(chans, target))


def assert_report_styled(
    report_name: str,
    *,
    style_provider: str | None = None,
    allow_personal_provider: bool = False,
    expected_activation: str | None = None,
    tol: int = 2,
) -> dict:
    """Fail unless the PDF carries the selected provider's required and supporting markers.

    This is an exact-value artifact check, not a perceptual design review or proof of which code
    rendered the PDF. The provider owns the marker contract; this module knows only its versioned
    schema. Requiring a primary marker plus an independent supporting marker prevents a single accent
    rectangle from turning an otherwise unrelated report into a pass.
    """
    profile, profile_path, style_source = _resolved_style_provider(
        style_provider,
        allow_personal=allow_personal_provider,
        activation_hint=expected_activation,
    )
    marker_sets = profile["pdf_markers"]
    required = marker_sets["required_any"]
    supporting = marker_sets["supporting_any"]
    minimum = marker_sets["minimum_distinct_markers"]

    base = os.path.basename(report_name)
    path = RESULTS / base
    if not path.exists():
        raise GateFailure(
            f"cannot verify report style: {base} is not at {RESULTS}/. "
            "Call assert_report_exists first."
        )
    found = pdf_colors(path)
    if not found:
        raise GateFailure(
            f"no colour operators could be read from {base}: the PDF sets no vector fill or stroke "
            "colour, so its report style is not evaluable. Rasterized images do not count because a "
            "screenshot of a styled report is not a styled report artifact."
        )

    required_hits = [
        marker for marker in required
        if any(_within(color, _rgb(marker), tol) for color in found)
    ]
    supporting_hits = [
        marker for marker in supporting
        if any(_within(color, _rgb(marker), tol) for color in found)
    ]
    distinct_hits = list(dict.fromkeys(required_hits + supporting_hits))
    seen = sorted(found, key=lambda color: -found[color])
    used = ", ".join(seen[:12]) + (", ..." if len(seen) > 12 else "")
    if not required_hits:
        raise GateFailure(
            f"{base} does not carry any required marker for report style provider "
            f"{profile['provider']!r}; exact-value check used tolerance {tol}/255 per channel and "
            f"expected one of {', '.join(required)} from {profile_path}. Colours used: {used}"
        )
    if len(distinct_hits) < minimum:
        raise GateFailure(
            f"{base} carries only {len(distinct_hits)} distinct marker(s) for report style provider "
            f"{profile['provider']!r}; {minimum} are required, including an independent supporting "
            f"marker from {', '.join(supporting)}. Colours used: {used}"
        )
    return {
        "provider": profile["provider"],
        "activation": profile["activation"],
        "style_source": style_source,
        "required_marker_hits": len(required_hits),
        "supporting_marker_hits": len(supporting_hits),
        "minimum_distinct_markers": minimum,
        "method": "exact PDF vector-colour markers from the resolved installed provider source",
    }


# --- writing large binaries to the results mount -------------------------------------------------

def staged_copy(src: str | pathlib.Path, dst: str | pathlib.Path) -> pathlib.Path:
    """Publish a completed binary from workspace without truncating it on the results mount.

    Direct writes or rewrites of PDF, HDF5, spreadsheet, presentation, and database files on the
    object-backed mount can fail or truncate. The destination is removed only after a non-empty
    staging file exists, then copied as a complete object.

    A relative `dst` is resolved against RESULTS. `src` stays relative to the CWD — it is the
    workspace staging file, which is the whole point of the two-step write.
    """
    src, dst = pathlib.Path(src), _at_results(dst)
    if not src.is_file() or src.stat().st_size == 0:
        raise GateFailure(f"staging file is absent or empty: {src}")
    if src.resolve() == dst.resolve():
        raise GateFailure("staging source and results destination must be different files")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        subprocess.run(["cp", str(src), str(dst)], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        shutil.copyfile(src, dst)
    if not dst.exists() or dst.stat().st_size == 0:
        raise GateFailure(f"staged copy produced a 0-byte file at {dst}")
    return dst


# --- the facts artifact --------------------------------------------------------------------------

def _load_skill_contract(path: str | pathlib.Path = "skill_contract.json") -> dict:
    candidate = pathlib.Path(path)
    if not candidate.is_absolute():
        candidate = SKILL_ROOT / candidate
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateFailure(f"cannot load facts/evidence contract at {candidate}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != "phylo-skill-evidence/1":
        raise GateFailure(f"{candidate} is not a phylo-skill-evidence/1 contract")
    return data


def clarification_branch_id(question_id: object, choice_id: object) -> str:
    """Return the stable runtime identifier for one clarification branch."""
    question = str(question_id).strip()
    choice = str(choice_id).strip()
    if not question or not choice:
        raise GateFailure("clarification branch IDs require both question_id and choice_id")
    if ":" in question or ":" in choice:
        raise GateFailure("clarification question and choice IDs may not contain ':'")
    return f"{question}:{choice}"


def outputs_for_selected_branches(
    selected_branch_ids: list[str],
    contract: str | pathlib.Path | dict = "skill_contract.json",
) -> list[str]:
    """Resolve receipt outputs from the branches actually selected for this run.

    Branch IDs use ``<question_id>:<choice_id>``. A static union can make mutually exclusive
    choices impossible to complete, so unknown, duplicate, or empty selections fail closed.
    """
    if (not isinstance(selected_branch_ids, list) or not selected_branch_ids
            or not all(isinstance(value, str) and value.strip() for value in selected_branch_ids)):
        raise GateFailure("selected_branch_ids must be a non-empty list of branch ID strings")
    if len(set(selected_branch_ids)) != len(selected_branch_ids):
        raise GateFailure("selected_branch_ids contains a duplicate branch ID")

    spec = contract if isinstance(contract, dict) else _load_skill_contract(contract)
    branches = spec.get("clarification_branches")
    if not isinstance(branches, list) or not branches:
        raise GateFailure("skill contract has no clarification branches")
    by_id: dict[str, dict] = {}
    for branch in branches:
        if not isinstance(branch, dict):
            raise GateFailure("skill contract contains a non-object clarification branch")
        branch_id = clarification_branch_id(branch.get("question_id"), branch.get("choice_id"))
        if branch_id in by_id:
            raise GateFailure(f"skill contract contains duplicate branch ID {branch_id!r}")
        by_id[branch_id] = branch

    unknown = [branch_id for branch_id in selected_branch_ids if branch_id not in by_id]
    if unknown:
        raise GateFailure(f"selected clarification branch IDs are not in the contract: {unknown}")
    questions = spec.get("clarification_questions")
    if not isinstance(questions, list) or not questions:
        raise GateFailure("skill contract has no clarification questions")
    selected_questions = [str(by_id[branch_id].get("question_id"))
                          for branch_id in selected_branch_ids]
    for question in questions:
        if not isinstance(question, dict):
            raise GateFailure("skill contract contains a non-object clarification question")
        question_id = str(question.get("id", "")).strip()
        count = selected_questions.count(question_id)
        if count == 0:
            raise GateFailure(f"clarification question {question_id!r} has no selected branch")
        if question.get("selection_mode") == "single" and count != 1:
            raise GateFailure(f"single-select clarification question {question_id!r} has {count} selections")
        if question.get("selection_mode") not in ("single", "multiple"):
            raise GateFailure(f"clarification question {question_id!r} has an invalid selection mode")
    outputs: list[str] = []
    for branch_id in selected_branch_ids:
        paths = by_id[branch_id].get("artifact_paths")
        if not isinstance(paths, list) or not paths:
            raise GateFailure(f"selected clarification branch {branch_id!r} has no artifact paths")
        for path in paths:
            value = str(path).strip()
            if not value:
                raise GateFailure(f"selected clarification branch {branch_id!r} has an empty output path")
            portable = pathlib.PurePosixPath(value.replace("\\", "/"))
            if portable.is_absolute() or ".." in portable.parts:
                raise GateFailure(
                    f"selected clarification branch {branch_id!r} has an output outside results: {value!r}"
                )
            if value not in outputs:
                outputs.append(value)
    return outputs


def _json_value(data: object, dotted_path: str) -> object:
    value = data
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise GateFailure(f"facts/witness field {dotted_path!r} is absent at {part!r}")
        value = value[part]
    return value


def _same_json_value(left: object, right: object) -> bool:
    """Compare values with JSON type semantics (where true is not the number 1)."""
    try:
        return json.dumps(left, sort_keys=True, separators=(",", ":"), allow_nan=False) == \
            json.dumps(right, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return False


def assert_semantic_facts(facts: dict, contract: dict) -> None:
    """Validate operational headline fields and denominator/completion accounting identities."""
    spec = contract.get("facts", {})
    if spec.get("requirement") != "required":
        raise GateFailure("write_facts called although skill_contract marks facts not_applicable")
    for headline in spec.get("headline_definitions", []):
        field = headline.get("field") if isinstance(headline, dict) else None
        definition = headline.get("operational_definition") if isinstance(headline, dict) else None
        if not field or not definition or _json_value(facts, field) is None:
            raise GateFailure("each headline fact needs a value and an operational definition")
    for group in spec.get("partition_groups", []):
        denominator = _json_value(facts, group["denominator_field"])
        members = [_json_value(facts, field) for field in group["member_fields"]]
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   for value in [denominator, *members]):
            raise GateFailure(f"partition {group['name']!r} contains a non-numeric value")
        if group["identity"] == "sum_members_equals_denominator" and sum(members) != denominator:
            raise GateFailure(
                f"partition {group['name']!r} does not account: {sum(members)} != {denominator}"
            )


def write_facts(path: str | pathlib.Path, facts: dict, *,
                contract: str | pathlib.Path | dict | None = None) -> pathlib.Path:
    """Write the numbers the report is allowed to quote. Call this AFTER every gate has passed.

    The renderer must read from this file rather than restating what the agent remembers. A report
    that prints its count by reading the table cannot disagree with the table.

    A relative path is resolved against RESULTS: facts written under the CWD are invisible to both
    the report builder and the user.
    """
    if contract is not None:
        spec = contract if isinstance(contract, dict) else _load_skill_contract(contract)
        assert_semantic_facts(facts, spec)
    p = _at_results(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(facts, indent=2, sort_keys=True), encoding="utf-8")
    return p


def _facts_from_runtime_artifact(
    source: str | pathlib.Path,
    figures: list[dict],
) -> tuple[dict, pathlib.Path]:
    """Reconstruct the only facts value valid for the current payload and figure inventory."""
    source_path = _resolved_results_artifact(source, strict=True)
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateFailure(f"cannot load facts runtime payload at {source_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise GateFailure("facts runtime payload must be a JSON object")
    if "figures" in payload and not _same_json_value(payload["figures"], figures):
        raise GateFailure("facts runtime payload disagrees with the validated figure inventory")
    return {**payload, "figures": figures}, source_path


def _assert_distinct_facts_artifacts(
    output: str | pathlib.Path,
    source: str | pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path]:
    """Reject direct and symlink aliases between canonical facts and their runtime evidence."""
    output_path = _resolved_results_artifact(output, strict=False)
    source_path = _resolved_results_artifact(source, strict=True)
    if output_path.resolve(strict=False) == source_path.resolve(strict=True):
        raise GateFailure(
            "facts artifact and runtime payload artifact must be distinct files; "
            "the report facts artifact cannot evidence itself"
        )
    return output_path, source_path


def write_facts_from_artifact(
    path: str | pathlib.Path,
    *,
    source: str | pathlib.Path,
    figures: list[dict],
    contract: str | pathlib.Path | dict = "skill_contract.json",
) -> pathlib.Path:
    """Load a runtime-produced JSON payload, attach checked figures, and write validated facts."""
    _assert_distinct_facts_artifacts(path, source)
    complete, _ = _facts_from_runtime_artifact(source, figures)
    return write_facts(path, complete, contract=contract)


# --- the run receipt -----------------------------------------------------------------------------

# Must equal check_skill.RECEIPT_SCHEMA. This module is copied into every generated skill and has to
# run standalone, so it cannot import the gate; a cross-file test asserts the two strings agree.
RECEIPT_SCHEMA = "phylo-run-receipt/1"
RECEIPT_SCHEMA_V2 = "phylo-run-receipt/2"
RECEIPT_SCHEMA_V3 = "phylo-run-receipt/3"
QC_RUN_LOG_SCHEMA = "phylo-qc-run-log/1"

#: Carried in the receipt beside every embedding verdict, so nobody downstream reads the verdict as
#: stronger than it is. Counting images proves the report contains at least as many pictures as were
#: declared; it does not prove they are those pictures. Identity matching is deliberately outside
#: this heuristic, so the receipt states the limitation beside the verdict.
EMBED_STATES = ("pass", "fail", "not_evaluable", "not_applicable")

_EMBED_METHOD = ("counts embedded images in the PDF and compares against the declared figure count; "
                 "does not match figure identity")
MIN_RENDERED_PAGE_BYTES = 1_000
REPORT_SECTIONS = (
    "Task Context", "Methods & Sources", "Results",
    "Conclusions & Interpretation", "Limitations",
)


def _ev_file(p: pathlib.Path) -> dict:
    """One artifact, as evidence: where it is and how big it is."""
    return {"path": str(p), "bytes": p.stat().st_size}


def _sha256(p: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with p.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_qc_run_log(path: str | pathlib.Path) -> tuple[dict, list[dict]]:
    """Load records written by this module's helpers, never an author-composed trace ledger."""
    ledger_path = _at_results(path)
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateFailure(f"cannot load execution ledger at {ledger_path}: {exc}") from exc
    events = ledger.get("events") if isinstance(ledger, dict) else None
    if (not isinstance(ledger, dict) or ledger.get("schema") != QC_RUN_LOG_SCHEMA
            or not isinstance(events, list) or not all(isinstance(event, dict) for event in events)):
        raise GateFailure(f"QC run log must be a {QC_RUN_LOG_SCHEMA!r} object")
    if ledger.get("generated_by") != "report_qc":
        raise GateFailure("QC run log does not identify report_qc as its generator")
    return ledger, events


def _event(events: list[dict], event_type: str, report_name: str) -> dict:
    matches = [event for event in events
               if event.get("type") == event_type and event.get("report") == os.path.basename(report_name)]
    if len(matches) != 1:
        raise GateFailure(f"need exactly one {event_type!r} event for {report_name}, found {len(matches)}")
    return matches[0]


def _pdf_page_count(report: pathlib.Path) -> int:
    """Count pages with the platform's pinned PDF parser; never treat a byte regex as proof."""
    try:
        import pypdf  # type: ignore
    except ImportError as exc:
        advisory = len(re.findall(rb"/Type\s*/Page(?!s)\b", report.read_bytes()))
        raise GateFailure(
            f"pypdf is unavailable, so page-tree verification is NOT-EVALUABLE "
            f"(advisory byte scan found {advisory} page markers)"
        ) from exc
    else:
        try:
            count = len(pypdf.PdfReader(str(report)).pages)
        except Exception as exc:  # pypdf uses version-specific PdfReadError classes
            raise GateFailure(f"could not parse PDF page tree: {exc}") from exc
    if count < 1:
        raise GateFailure("could not verify the PDF page count")
    return count


def _pdf_review_evidence(events: list[dict], report_name: str) -> dict[str, dict]:
    report = assert_report_exists(report_name)
    report_sha256 = _sha256(report)
    text_event = _event(events, "pdf_text_extraction", report_name)
    if text_event.get("report_sha256") != report_sha256:
        raise GateFailure("PDF text extraction was not recorded for the current report bytes")
    text_path = _at_results(text_event.get("artifact", ""))
    if (not text_path.is_file() or text_event.get("artifact_sha256") != _sha256(text_path)
            or not text_path.read_text(encoding="utf-8", errors="replace").strip()):
        raise GateFailure("PDF text extraction event has no non-empty text artifact")

    render_event = _event(events, "pdf_render", report_name)
    if render_event.get("report_sha256") != report_sha256:
        raise GateFailure("PDF renders were not recorded for the current report bytes")
    pages = render_event.get("pages")
    if not isinstance(pages, list) or not pages:
        raise GateFailure("PDF render event contains no pages")
    page_numbers = []
    rendered = []
    for page in pages:
        number = page.get("page") if isinstance(page, dict) else None
        image = _at_results(page.get("image", "")) if isinstance(page, dict) else pathlib.Path()
        if (not isinstance(number, int) or number < 1 or not image.is_file()
                or image.stat().st_size < MIN_RENDERED_PAGE_BYTES
                or page.get("image_sha256") != _sha256(image)):
            raise GateFailure("PDF render event has an invalid or blank page image")
        page_numbers.append(number)
        rendered.append(_ev_file(image))
    expected = list(range(1, max(page_numbers) + 1))
    if sorted(page_numbers) != expected:
        raise GateFailure(f"PDF render pages {sorted(page_numbers)} do not cover {expected}")
    pdf_page_count = _pdf_page_count(report)
    if len(expected) != pdf_page_count:
        raise GateFailure(f"rendered {len(expected)} page(s), but the PDF contains {pdf_page_count}")

    review_event = _event(events, "pdf_visual_review", report_name)
    if review_event.get("report_sha256") != report_sha256:
        raise GateFailure("PDF visual review was not recorded for the current report bytes")
    reviewed_raw = review_event.get("pages")
    if (not isinstance(reviewed_raw, list)
            or not all(isinstance(page, int) and not isinstance(page, bool) for page in reviewed_raw)):
        raise GateFailure("PDF visual-review pages must be an array of page numbers")
    reviewed = sorted(reviewed_raw)
    if reviewed != expected or not str(review_event.get("review_evidence", "")).strip():
        raise GateFailure("visual review must cover every rendered page and name its evidence")
    verdict = review_event.get("verdict")
    issues = review_event.get("issues")
    if verdict not in {"pass", "fail"}:
        raise GateFailure("PDF visual review must record verdict 'pass' or 'fail'")
    if not isinstance(issues, list) or not all(
        isinstance(issue, str) and issue.strip() for issue in issues
    ):
        raise GateFailure("PDF visual review issues must be an array of non-empty strings")
    if verdict != "pass" or issues:
        detail = "; ".join(issues) if issues else "visual reviewer returned fail"
        raise GateFailure(f"PDF visual review did not pass: {detail}")
    return {
        "text_extracted": {"report": _ev_file(report), "text": _ev_file(text_path)},
        "pages_rendered": {"pages": rendered},
        "visual_review_attested": {
            "pages": reviewed,
            "verdict": verdict,
            "issues": issues,
            "attestation": review_event["review_evidence"],
            "limitation": "author/agent attestation; not independently machine-verifiable",
        },
    }


def _report_sections_evidence(events: list[dict], report_name: str) -> dict:
    """Require the universal report headings in extracted-text order, not just in SKILL.md prose."""
    report = assert_report_exists(report_name)
    text_event = _event(events, "pdf_text_extraction", report_name)
    if text_event.get("report_sha256") != _sha256(report):
        raise GateFailure("report-section text was not extracted from the current report bytes")
    text_path = _at_results(text_event.get("artifact", ""))
    if (not text_path.is_file()
            or text_event.get("artifact_sha256") != _sha256(text_path)):
        raise GateFailure("PDF text extraction event has no text artifact")
    raw_text = text_path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    line_pages: list[int] = []
    for page_number, page in enumerate(raw_text.split("\f"), 1):
        for line in page.splitlines():
            lines.append(" ".join(line.split()).casefold())
            line_pages.append(page_number)
    positions = []
    cursor = 0
    contents_positions = [
        index for index, line in enumerate(lines)
        if line in {"contents", "table of contents"}
    ]
    if contents_positions:
        contents_index = contents_positions[0]
        task_context = REPORT_SECTIONS[0].casefold()
        task_positions = [
            index for index in range(contents_index + 1, len(lines))
            if lines[index] == task_context
        ]
        if len(task_positions) >= 2:
            cursor = task_positions[1]
        elif (len(task_positions) == 1
              and line_pages[task_positions[0]] > line_pages[contents_index]):
            cursor = task_positions[0]
        else:
            raise GateFailure(
                "report body has no Task Context heading distinguishable from its table of contents"
            )
    for section in REPORT_SECTIONS:
        expected = section.casefold()
        match = next((index for index in range(cursor, len(lines))
                      if lines[index] == expected), None)
        if match is None:
            raise GateFailure(
                f"report has no ordered {section!r} heading occurrence after line {cursor}"
            )
        positions.append(match)
        cursor = match + 1
    return {"report": _ev_file(report), "text": _ev_file(text_path),
            "sections": list(REPORT_SECTIONS), "line_positions": positions}


def assert_source_witnesses(contract: dict) -> list[dict]:
    """Verify computation-critical asserted values against artifacts produced by the run."""
    checked = []
    assertions = contract.get("source_assertions")
    if not isinstance(assertions, list):
        raise GateFailure("source_assertions must be an array")
    if not assertions:
        reason = str(contract.get("source_assertions_not_applicable_reason", "")).strip()
        if not reason:
            raise GateFailure("no source assertions and no non-empty applicability reason")
        return []
    for assertion in assertions:
        if not isinstance(assertion, dict) or not isinstance(assertion.get("runtime_witness"), dict):
            raise GateFailure("each source assertion needs a runtime_witness object")
        witness = assertion["runtime_witness"]
        if not _same_json_value(witness.get("expected_value"), assertion.get("asserted_value")):
            raise GateFailure(
                f"source assertion {assertion.get('id')!r} disagrees with its runtime witness"
            )
        raw_artifact = witness.get("artifact")
        if not isinstance(raw_artifact, str) or not raw_artifact.strip():
            raise GateFailure("source witness artifact must be a non-empty results-relative path")
        relative_artifact = pathlib.PurePosixPath(raw_artifact.replace("\\", "/"))
        if relative_artifact.is_absolute() or ".." in relative_artifact.parts:
            raise GateFailure(f"source witness escapes the results root: {raw_artifact!r}")
        artifact = _at_results(raw_artifact)
        try:
            artifact.resolve(strict=True).relative_to(RESULTS.resolve(strict=True))
        except (FileNotFoundError, ValueError) as exc:
            raise GateFailure(
                f"source witness does not resolve beneath the results root: {raw_artifact!r}"
            ) from exc
        try:
            payload = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GateFailure(f"cannot read source witness {artifact}: {exc}") from exc
        got = _json_value(payload, witness["json_path"])
        if not _same_json_value(got, witness["expected_value"]):
            raise GateFailure(
                f"source witness {assertion['id']!r} is {got!r}, expected {witness['expected_value']!r}"
            )
        checked.append({"id": assertion["id"], "artifact": str(artifact), "sha256": _sha256(artifact)})
    return checked


#: The installed skill package — this module is copied to ``<package>/scripts/report_qc.py``, so the
#: package root is two levels up. Resolved at import, not hardcoded, because the slug differs per
#: skill and the mount point differs between authoring (``/mnt/results/skills/<slug>``) and installed
#: (``/mnt/skills/<slug>``).
SKILL_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _find_bundled(name: str) -> pathlib.Path:
    """Resolve a package-relative bundled file across authoring and installed layouts.

    A path such as ``scripts/analyse.py`` can be relative to the current directory, the skill
    package, or the results root. The receipt verifies the first existing candidate and records its
    resolved identity and digest.
    """
    tried = []
    for cand in (pathlib.Path(name), SKILL_ROOT / name, _at_results(name)):
        tried.append(cand)
        if cand.exists():
            return cand
    raise GateFailure(
        f"bundled file {name!r} is not present. Looked in the working directory, the skill package "
        f"({SKILL_ROOT}) and the results root — "
        + ", ".join(str(t) for t in tried)
        + ". Name it as it sits in the package, e.g. 'scripts/analyse.py'."
    )


def _write_qc_event(event: dict, path: str | pathlib.Path = "qc_run_log.json") -> pathlib.Path:
    log_path = _at_results(path)
    if log_path.is_file():
        ledger, events = _load_qc_run_log(log_path)
    else:
        ledger, events = {"schema": QC_RUN_LOG_SCHEMA, "generated_by": "report_qc"}, []
    events.append(event)
    ledger["events"] = events
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    return log_path


def _replace_qc_events(replacements: list[dict], *,
                       path: str | pathlib.Path = "qc_run_log.json") -> pathlib.Path:
    """Replace matching report/type events and persist one coherent retry set in one write."""
    log_path = _at_results(path)
    if log_path.is_file():
        ledger, events = _load_qc_run_log(log_path)
    else:
        ledger, events = {"schema": QC_RUN_LOG_SCHEMA, "generated_by": "report_qc"}, []
    def identity(event: dict) -> tuple[object, object]:
        subject = event.get("filename") if event.get("type") == "generateimage_snapshot" \
            else event.get("report")
        return event.get("type"), subject

    keys = {identity(event) for event in replacements}
    ledger["events"] = [
        event for event in events if identity(event) not in keys
    ] + replacements
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    return log_path


def _artifact_state(path: pathlib.Path) -> tuple[int, str] | None:
    """Content fingerprint used to distinguish current-run outputs from stale files."""
    path = _resolved_results_artifact(path, strict=False)
    if not path.is_file():
        return None
    _resolved_results_artifact(path, strict=True)
    return path.stat().st_size, _sha256(path)


def run_bundled(argv: list[str], bundled_file: str, expected_outputs: list[str], *,
                invocation_id: str | None = None,
                log_path: str | pathlib.Path = "qc_run_log.json") -> subprocess.CompletedProcess:
    """Run one bundled command without a shell and log measured hashes and exit status."""
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise GateFailure("argv must be a non-empty list of strings")
    if invocation_id is not None and (not isinstance(invocation_id, str) or not invocation_id.strip()):
        raise GateFailure("invocation_id must be a non-empty string when supplied")
    bundled = _find_bundled(bundled_file)
    bundled_identity = bundled.resolve()
    argv_paths = {pathlib.Path(item).resolve() for item in argv}
    if bundled_file not in argv and bundled_identity not in argv_paths:
        raise GateFailure("argv does not name the bundled file whose execution would be recorded")
    before = {output: _artifact_state(_at_results(output)) for output in expected_outputs}
    completed = subprocess.run(argv, check=False, capture_output=True, text=True)
    produced = []
    for output in expected_outputs:
        artifact = _at_results(output)
        after = _artifact_state(artifact)
        if after is not None and after[0] and after != before[output]:
            produced.append({"path": output, "sha256": _sha256(artifact), "bytes": artifact.stat().st_size})
    scope = invocation_id or hashlib.sha256(
        json.dumps(argv, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    _write_qc_event({
        "type": "command", "argv": argv, "bundled_file": bundled_file,
        "invocation_id": scope, "expected_outputs": list(expected_outputs),
        "bundled_sha256": _sha256(bundled), "exit_status": completed.returncode,
        "produced_artifacts": produced,
    }, log_path)
    if completed.returncode:
        raise GateFailure(f"bundled command exited {completed.returncode}: {completed.stderr[-500:]}")
    return completed


def record_generated_infographic(filename: str, *,
                                 log_path: str | pathlib.Path = "qc_run_log.json") -> pathlib.Path:
    """Snapshot tool evidence and image hashes immediately after GenerateImage returns."""
    basename = os.path.basename(filename)
    trace = assert_generated_by_tool(basename)
    image = _at_results(basename)
    if not image.is_file():
        raise GateFailure(f"generated infographic missing at snapshot boundary: {image}")
    event = {
        "type": "generateimage_snapshot", "filename": basename,
        "image_sha256": _sha256(image),
        "decoded_pixel_sha256": _decoded_pixel_sha256(image),
        "trace_evidence": trace[0],
    }
    return _replace_qc_events([event], path=log_path)


def assert_infographic_snapshot_lineage(report_name: str, filenames: list[str],
                                        ledger_events: list[dict]) -> dict:
    """Bind the immutable-at-boundary snapshot to the current file and final PDF."""
    trace = assert_generated_by_tool(*filenames)
    snapshots = {event.get("filename"): event for event in ledger_events
                 if event.get("type") == "generateimage_snapshot"}
    for item in trace:
        filename = item["requested_filename"]
        snapshot = snapshots.get(filename)
        image = _at_results(filename)
        if not isinstance(snapshot, dict):
            raise GateFailure(f"no immediate GenerateImage snapshot for {filename}")
        if snapshot.get("trace_evidence") != item:
            raise GateFailure(f"GenerateImage trace changed after snapshot for {filename}")
        if (not image.is_file() or snapshot.get("image_sha256") != _sha256(image)
                or snapshot.get("decoded_pixel_sha256") != _decoded_pixel_sha256(image)):
            raise GateFailure(f"generated infographic changed after snapshot: {filename}")
    return {
        "items": assert_infographic_pdf_lineage(report_name, trace),
        "snapshots": [snapshots[item["requested_filename"]] for item in trace],
        "transcript": str(_at_results(_TRANSCRIPT_REL)),
    }


def _latest_command_events(events: list[dict]) -> dict[tuple[str, str], tuple[int, dict]]:
    """Return each distinct invocation, with retries reduced to their last attempt."""
    latest: dict[tuple[str, str], tuple[int, dict]] = {}
    for index, event in enumerate(events):
        bundled_file = event.get("bundled_file") if event.get("type") == "command" else None
        if isinstance(bundled_file, str):
            scope = event.get("invocation_id")
            if not isinstance(scope, str) or not scope:
                argv = event.get("argv")
                scope = hashlib.sha256(
                    json.dumps(argv, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ).hexdigest() if isinstance(argv, list) else "legacy"
            latest[(bundled_file, scope)] = (index, event)
    return latest


def record_pdf_review(report_name: str, text_artifact: str, rendered_page_files: list[str],
                      reviewed_page_numbers: list[int], review_attestation: str,
                      review_verdict: str, review_issues: list[str], *,
                      log_path: str | pathlib.Path = "qc_run_log.json") -> pathlib.Path:
    """Regenerate PDF artifacts, then record an honestly labelled visual-review attestation."""
    if (not isinstance(rendered_page_files, list) or not rendered_page_files
            or not all(isinstance(path, str) and path.strip() for path in rendered_page_files)):
        raise GateFailure("rendered_page_files must name every rendered PDF page")
    if not isinstance(text_artifact, str) or not text_artifact.strip():
        raise GateFailure("text_artifact must name a results-root extraction target")
    if not isinstance(review_attestation, str) or not review_attestation.strip():
        raise GateFailure("visual review needs a non-empty attestation")
    if review_verdict not in {"pass", "fail"}:
        raise GateFailure("review_verdict must be 'pass' or 'fail'")
    if not isinstance(review_issues, list) or not all(
        isinstance(issue, str) and issue.strip() for issue in review_issues
    ):
        raise GateFailure("review_issues must be an array of non-empty strings")
    report = assert_report_exists(report_name)
    report_sha256 = _sha256(report)

    def output_path(raw: str) -> pathlib.Path:
        candidate = _at_results(raw)
        try:
            candidate.resolve(strict=False).relative_to(RESULTS.resolve(strict=True))
        except (FileNotFoundError, ValueError) as exc:
            raise GateFailure(f"PDF review artifact escapes the results root: {raw!r}") from exc
        return candidate

    text_path = output_path(text_artifact)
    page_paths = [output_path(path) for path in rendered_page_files]
    resolved_artifacts = [text_path.resolve(strict=False),
                          *(path.resolve(strict=False) for path in page_paths)]
    if len(set(resolved_artifacts)) != len(resolved_artifacts):
        raise GateFailure("PDF text and rendered-page artifact paths must be distinct")
    if report.resolve() in set(resolved_artifacts):
        raise GateFailure("PDF review artifacts cannot overwrite the report")

    with tempfile.TemporaryDirectory(prefix="phylo-pdf-review-") as tmp:
        scratch = pathlib.Path(tmp)
        extracted = scratch / "report.txt"
        prefix = scratch / "page"
        commands = (
            (["pdftotext", "-layout", str(report), str(extracted)], "PDF text extraction"),
            (["pdftoppm", "-png", "-r", "144", str(report), str(prefix)], "PDF rendering"),
        )
        for argv, label in commands:
            if shutil.which(argv[0]) is None:
                raise GateFailure(f"{label} is NOT-EVALUABLE: {argv[0]} is unavailable")
            try:
                completed = subprocess.run(
                    argv, check=False, capture_output=True, text=True, timeout=120,
                )
            except subprocess.TimeoutExpired as exc:
                raise GateFailure(f"{label} exceeded the 120-second bound") from exc
            if completed.returncode:
                raise GateFailure(f"{label} failed: {completed.stderr[-500:]}")
        generated_pages = sorted(
            scratch.glob("page-*.png"),
            key=lambda path: int(path.stem.rsplit("-", 1)[1]),
        )
        if len(generated_pages) != len(page_paths):
            raise GateFailure(
                f"PDF produced {len(generated_pages)} rendered pages, but "
                f"{len(page_paths)} page artifact paths were supplied"
            )
        if report_sha256 != _sha256(report):
            raise GateFailure("PDF changed while its review artifacts were being generated")
        # The results mount is object-backed: metadata preservation and in-place truncation can
        # both fail on a retry. Publish completed scratch artifacts through the same unlink-first
        # path used for other large binaries.
        staged_copy(extracted, text_path)
        for generated, destination in zip(generated_pages, page_paths):
            staged_copy(generated, destination)
    if report_sha256 != _sha256(report):
        raise GateFailure("PDF changed before its review evidence was recorded")
    pages = [{"page": number, "image": path,
              "image_sha256": _sha256(page_paths[number - 1])}
             for number, path in enumerate(rendered_page_files, 1)]
    events = [
        {"type": "pdf_text_extraction", "report": os.path.basename(report_name),
         "report_sha256": report_sha256, "artifact": text_artifact,
         "artifact_sha256": _sha256(text_path)},
        {"type": "pdf_render", "report": os.path.basename(report_name),
         "report_sha256": report_sha256, "pages": pages},
        {"type": "pdf_visual_review", "report": os.path.basename(report_name),
         "report_sha256": report_sha256,
         "pages": reviewed_page_numbers, "review_evidence": review_attestation,
         "verdict": review_verdict, "issues": review_issues,
         "evidence_type": "author_or_agent_attestation"},
    ]
    return _replace_qc_events(events, path=log_path)


def write_receipt(report_name: str | None, figures: list[dict], *,
                  bundled_files: tuple[str, ...] | list[str] = (),
                  outputs: tuple[str, ...] | list[str] = (),
                  infographics: tuple[str, ...] | list[str] = (),
                  style_provider: str | None = None,
                  path: str | pathlib.Path = "run_receipt.json",
                  qc_run_log: str | pathlib.Path | None = None,
                  figure_not_applicable_reason: str | None = None,
                  contract: str | pathlib.Path | dict = "skill_contract.json",
                  strict: bool = True) -> pathlib.Path:
    """Run the gates and write the receipt from what they returned. Do not hand-write this file.

    Every boolean is returned by a gate and carries the evidence it was decided from: a resolved
    path, byte count, PDF colour sample, parsed contract witness, or measured subprocess event.

    What it does NOT do: prove anything against an author who is willing to write the JSON by hand.
    An agent that can write a file can write any file, and no in-band artifact fixes that. What it
    does is make the honest path one call and make a pasted checklist fail — `evidence` has to be
    there and has to be non-empty, and it is tedious to forge convincingly. Read it as raising the
    cost of the wrong thing, not as proof against a hostile author.

    Without an execution ledger, legacy receipt v1 can verify only that a bundled file exists. With
    the evidence-v1 ledger, receipt v2 matches its hash to a successful transcript event. Other
    outcomes are read off the artifacts and stand on their own, with one
    documented soft edge — the figure-embed count needs pypdf, so where it cannot run the receipt
    records `embed_check_ran: false` rather than failing or pretending it passed.

    Writes to the RESULTS root, never beside SKILL.md: once `Skill(action="create")` installs the
    package that directory is mounted read-only, so a per-run receipt written there cannot work.

    Every check runs even after one fails, the file is written either way, and `strict` then raises —
    a failing run should leave the diagnostic behind rather than dying before it is written.
    """
    outcome: dict[str, bool | str] = {}
    evidence: dict[str, object] = {}
    reasons: dict[str, str] = {}
    # Tri-state rather than a boolean, and separate from figures_present_and_nonblank, because the
    # two are checked to different strengths: the artifacts are proved with the stdlib, the embedding
    # needs pypdf and may not be evaluable at all. Starts "not_evaluable" so a run that dies before
    # the figure step records the honest answer rather than a default that flatters it.
    embedded = ["not_evaluable"]
    validated_figures: list[list[dict] | None] = [None]
    ledger_requested = qc_run_log is not None
    ledger_events: list[dict] = []
    ledger_error = ""
    if ledger_requested:
        try:
            _, ledger_events = _load_qc_run_log(qc_run_log)
        except (GateFailure, OSError, ValueError) as exc:
            ledger_error = f"{type(exc).__name__}: {exc}"
    try:
        contract_data = contract if isinstance(contract, dict) else _load_skill_contract(contract)
    except (GateFailure, OSError, ValueError):
        contract_data = {}
    policy = contract_data.get("deliverable_policy", {}) if isinstance(contract_data, dict) else {}
    report_policy = policy.get("report", {}) if isinstance(policy, dict) else {}
    infographic_policy = policy.get("infographic", {}) if isinstance(policy, dict) else {}
    report_required = report_policy.get("required", True) is True
    infographic_required = infographic_policy.get("required", True) is True
    execution_policy = (
        contract_data.get("execution", {}) if isinstance(contract_data, dict) else {}
    )
    # A filesystem-only helper deliberately has no command, PDF, or image event to log. Treat a
    # genuinely absent log as an empty ledger only for that explicit contract shape. An existing
    # malformed log, or any report/infographic workflow, must still fail closed.
    if (
        ledger_requested
        and ledger_error
        and execution_policy.get("bundled_commands_applicable") is False
        and not report_required
        and not infographic_required
        and not _at_results(qc_run_log).exists()
    ):
        ledger_error = ""

    def mark_not_applicable(key: str, reason: str) -> None:
        if not reason:
            raise GateFailure(f"{key} is not applicable but has no contract reason")
        outcome[key] = "not_applicable"
        evidence[key] = {"status": "not_applicable", "reason": reason}

    def record(key: str, fn) -> None:
        try:
            evidence[key] = fn()
            outcome[key] = True
        except (GateFailure, OSError, ValueError) as exc:
            outcome[key] = False
            reasons[f"{key}_reason"] = f"{type(exc).__name__}: {exc}"

    def _bundled() -> dict:
        if not bundled_files:
            execution = contract_data.get("execution", {}) if ledger_requested else {}
            reason = str(execution.get("not_applicable_reason", "")).strip()
            if ledger_requested and execution.get("bundled_commands_applicable") is False and reason:
                return {"status": "not_applicable", "reason": reason}
            raise GateFailure("no bundled files named and execution is not explicitly not_applicable")
        if ledger_requested and ledger_error:
            raise GateFailure(ledger_error)
        seen = []
        latest_commands = _latest_command_events(ledger_events) if ledger_requested else {}
        for f in bundled_files:
            bundled = _find_bundled(f)
            if ledger_requested:
                digest = _sha256(bundled)
                invocations = [item for (name, _), item in latest_commands.items() if name == f]
                if not invocations or any(
                    event.get("bundled_sha256") != digest or event.get("exit_status") != 0
                    for _, event in invocations
                ):
                    raise GateFailure(
                        f"one or more latest execution attempts did not succeed for bundled file {f!r}"
                    )
                seen.append({**_ev_file(bundled), "sha256": digest,
                             "qc_log_events": [index for index, _ in invocations]})
            else:
                seen.append(_ev_file(bundled))
        if ledger_requested:
            return {"executed": seen, "method": "artifact hash matched to report_qc subprocess log"}
        return {"claimed_executed_by_caller": seen,
                "limitation": "existence and size are checked here; execution is the caller's claim"}

    def _outputs() -> dict:
        if not outputs:
            raise GateFailure(
                "no declared outputs named. Pass every path '## Outputs' promises, so the key means "
                "'they appeared' rather than 'nobody looked'."
            )
        if ledger_requested and ledger_error:
            raise GateFailure(ledger_error)
        seen = []
        latest_commands = _latest_command_events(ledger_events) if ledger_requested else {}
        current_events = [item for (name, _), item in latest_commands.items()
                          if name in bundled_files]
        execution = contract_data.get("execution", {}) if isinstance(contract_data, dict) else {}
        commands_applicable = execution.get("bundled_commands_applicable")
        raw_command_outputs = execution.get("command_output_paths")
        if commands_applicable is True:
            if (not isinstance(raw_command_outputs, list)
                    or not all(isinstance(path, str) and path.strip()
                               for path in raw_command_outputs)):
                raise GateFailure(
                    "execution.command_output_paths must explicitly identify command-produced outputs"
                )
            command_outputs = set(raw_command_outputs)
        elif commands_applicable is False:
            command_outputs = set()
        else:
            # Legacy receipt callers predate the per-output provenance field. Preserve their
            # all-command meaning; evidence-v1 packages are statically required to be explicit.
            command_outputs = set(outputs) if bundled_files else set()
        for f in outputs:
            p = _resolved_results_artifact(f, strict=True)
            if not p.is_file():
                raise GateFailure(f"declared output {f} did not appear at {p}")
            if p.stat().st_size == 0:
                raise GateFailure(f"declared output {p} is 0 bytes")
            item = {**_ev_file(p), "sha256": _sha256(p)}
            if ledger_requested and f in command_outputs:
                relative = str(p.relative_to(RESULTS)) if p.is_relative_to(RESULTS) else str(p)
                matches = []
                for event_index, event in current_events:
                    if event.get("exit_status") != 0:
                        continue
                    produced_artifacts = event.get("produced_artifacts", [])
                    if not isinstance(produced_artifacts, list):
                        continue
                    for produced in produced_artifacts:
                        if not isinstance(produced, dict):
                            continue
                        if (produced.get("path") in (f, relative, str(p))
                                and produced.get("sha256") == item["sha256"]):
                            matches.append(event_index)
                if not matches:
                    raise GateFailure(f"output {f!r} has no hash from a latest successful attempt")
                item["qc_log_event"] = matches[0]
                item["provenance"] = "command"
            else:
                item["provenance"] = "filesystem"
            seen.append(item)
        if command_outputs.intersection(outputs):
            method = "declared command outputs matched to the QC log; other outputs use filesystem evidence"
        else:
            method = "filesystem with resolved results-root containment"
        return {"appeared": seen, "method": method}

    def _report() -> dict:
        if not report_name:
            raise GateFailure("report_name is missing although the report is required")
        return _ev_file(assert_report_exists(report_name))

    def _figures() -> dict:
        # Artifact validity and PDF embedding are separate claims with different evidence strengths.
        # `figure_contract_satisfied` proves each declared figure exists, is non-blank, and carries a
        # caption. `figures_embedded` separately reports the PDF image-count heuristic.
        if not figures:
            if not figure_not_applicable_reason:
                raise GateFailure("no figures and no figure_not_applicable_reason")
            validated_figures[0] = []
            embedded[0] = "not_applicable"
            return {"status": "not_applicable", "reason": figure_not_applicable_reason}
        checked = assert_figures(figures)
        validated_figures[0] = checked
        if not report_required:
            embedded[0] = "not_applicable"
            files = [_ev_file(_resolved_results_artifact(e["file"], strict=True))
                     for e in checked if e.get("file")]
            skipped = [{"step": e.get("step"), "reason": e.get("reason")}
                       for e in checked if not e.get("file")]
            return {"figures": files, "declared_unplottable": skipped,
                    "embedding": {"state": "not_applicable",
                                  "detail": "No PDF report is declared."}}
        ok, detail = report_embeds_figures(report_name, checked)
        if ok:
            embedded[0] = "pass"
        elif detail.startswith("NOT-EVALUABLE"):
            embedded[0] = "not_evaluable"
        else:
            embedded[0] = "fail"
        files = [_ev_file(_resolved_results_artifact(e["file"], strict=True))
                 for e in checked if e.get("file")]
        skipped = [{"step": e.get("step"), "reason": e.get("reason")}
                   for e in checked if not e.get("file")]
        return {"figures": files, "declared_unplottable": skipped,
                "embedding": {"state": embedded[0], "detail": detail, "method": _EMBED_METHOD}}

    style_contract = (
        isinstance(report_policy.get("default_style_provider"), str)
        and report_policy.get("explicit_style_override_allowed") is True
    )
    style_outcome_key = (
        "report_style_verified" if ledger_requested and style_contract else "report_branded"
    )

    def _styled() -> dict:
        if not report_name:
            raise GateFailure("report_name is missing although the report is required")
        default_provider = report_policy.get("default_style_provider") if style_contract else None
        selected_provider: str | None = None
        selection_evidence: dict | None = None
        if style_contract:
            selected_provider, selection_evidence = _selected_style_from_transcript()
        chosen_provider = selected_provider or default_provider or style_provider
        if not chosen_provider:
            raise GateFailure("report style provider is missing from the run and contract")
        if style_contract and style_provider and style_provider != chosen_provider:
            raise GateFailure(
                f"caller asserted report style provider {style_provider!r}, but immutable user "
                f"selection resolves {chosen_provider!r}"
            )
        styled = assert_report_styled(
            report_name,
            style_provider=chosen_provider,
            allow_personal_provider=selected_provider is not None or not style_contract,
            expected_activation=(
                "explicit_only" if selected_provider is not None
                else "default" if style_contract
                else None
            ),
        )
        if selected_provider is None and style_contract and styled.get("activation") != "default":
            raise GateFailure(
                f"report style provider {chosen_provider!r} is explicit-only and cannot be the "
                "contract fallback"
            )
        if selected_provider is not None and styled.get("activation") != "explicit_only":
            raise GateFailure(
                f"explicit report style provider {chosen_provider!r} is not explicit-only"
            )
        evidence = {
            **styled,
            "selection": "explicit_override" if selected_provider else "contract_default",
            "contract_default_provider": default_provider,
        }
        if selection_evidence is not None:
            evidence["selection_evidence"] = selection_evidence
        return evidence

    def _facts() -> dict:
        facts_spec = contract_data.get("facts", {}) if isinstance(contract_data, dict) else {}
        facts_path = facts_spec.get("schema") if isinstance(facts_spec, dict) else None
        if not isinstance(facts_path, str) or not facts_path.strip():
            raise GateFailure("facts are required but the contract names no facts artifact")
        artifact = _resolved_results_artifact(facts_path, strict=True)
        try:
            payload = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GateFailure(f"cannot load facts artifact at {artifact}: {exc}") from exc
        if not isinstance(payload, dict):
            raise GateFailure("facts artifact must be a JSON object")
        current_figures = validated_figures[0]
        if current_figures is None:
            raise GateFailure("facts cannot be verified because the figure contract failed")
        source = facts_spec.get("runtime_payload_artifact")
        if not isinstance(source, str) or not source.strip():
            raise GateFailure("facts are required but the contract names no runtime payload artifact")
        _assert_distinct_facts_artifacts(artifact, source)
        expected, source_artifact = _facts_from_runtime_artifact(source, current_figures)
        if not _same_json_value(payload, expected):
            raise GateFailure(
                "facts artifact does not match the current runtime payload and validated figures"
            )
        assert_semantic_facts(payload, contract_data)
        return {**_ev_file(artifact), "sha256": _sha256(artifact),
                "runtime_payload": {**_ev_file(source_artifact),
                                    "sha256": _sha256(source_artifact)},
                "method": "exact runtime-payload reconstruction plus semantic facts validation"}

    record("execution_contract_satisfied" if ledger_requested else "bundled_files_ran", _bundled)
    record("outputs_appeared", _outputs)
    record("figure_contract_satisfied" if ledger_requested else "figures_present_and_nonblank", _figures)
    if ledger_requested:
        facts_spec = contract_data.get("facts", {}) if isinstance(contract_data, dict) else {}
        facts_requirement = facts_spec.get("requirement") if isinstance(facts_spec, dict) else None
        if facts_requirement == "required":
            record("facts_artifact_verified", _facts)
        elif facts_requirement == "not_applicable":
            mark_not_applicable(
                "facts_artifact_verified",
                str(facts_spec.get("not_applicable_reason", "")).strip(),
            )
        else:
            outcome["facts_artifact_verified"] = False
            reasons["facts_artifact_verified_reason"] = (
                "GateFailure: facts.requirement is missing or invalid"
            )
    report_reason = str(report_policy.get("not_applicable_reason", "")).strip()
    if report_required:
        record("report_at_results_root", _report)
        record(style_outcome_key, _styled)
    else:
        mark_not_applicable("report_at_results_root", report_reason)
        mark_not_applicable(style_outcome_key, report_reason)

    if ledger_requested:
        if not report_required:
            for key in ("text_extracted", "pages_rendered", "visual_review_attested",
                        "report_sections_present"):
                mark_not_applicable(key, report_reason)
        elif ledger_error:
            for key in ("text_extracted", "pages_rendered", "visual_review_attested"):
                outcome[key] = False
                reasons[f"{key}_reason"] = ledger_error
        else:
            try:
                review = _pdf_review_evidence(ledger_events, report_name)
            except (GateFailure, OSError, ValueError) as exc:
                for key in ("text_extracted", "pages_rendered", "visual_review_attested"):
                    outcome[key] = False
                    reasons[f"{key}_reason"] = f"{type(exc).__name__}: {exc}"
            else:
                for key in ("text_extracted", "pages_rendered", "visual_review_attested"):
                    evidence[key] = review[key]
                    outcome[key] = True

        if report_required:
            record("report_sections_present",
                   lambda: _report_sections_evidence(ledger_events, report_name))

        def _sources() -> dict:
            if ledger_error:
                raise GateFailure(ledger_error)
            witnessed = assert_source_witnesses(contract_data)
            return {
                "checked": witnessed,
                "not_applicable_reason": contract_data.get("source_assertions_not_applicable_reason")
                if not witnessed else None,
            }

        record("source_assertions_verified", _sources)

    def _infographics() -> dict:
        if not infographics:
            raise GateFailure("at least one GenerateImage infographic is mandatory")
        if not ledger_requested or ledger_error:
            raise GateFailure(ledger_error or "infographic snapshot requires the QC run log")
        return assert_infographic_snapshot_lineage(report_name, list(infographics), ledger_events)

    if infographic_required:
        record("infographic_lineage_verified", _infographics)
    else:
        mark_not_applicable(
            "infographic_lineage_verified",
            str(infographic_policy.get("not_applicable_reason", "")).strip(),
        )

    receipt_schema = (
        RECEIPT_SCHEMA_V3 if ledger_requested and style_contract
        else RECEIPT_SCHEMA_V2 if ledger_requested
        else RECEIPT_SCHEMA
    )
    receipt = {"schema": receipt_schema,
               "generated_by": "report_qc.write_receipt",
               **outcome, "figures_embedded": embedded[0], **reasons, "evidence": evidence}
    p = _at_results(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")

    failed = sorted(k for k, v in outcome.items() if v is False)
    if embedded[0] == "fail":
        failed.append("figures_embedded")
    if failed and strict:
        raise GateFailure(
            f"the run did not pass its own receipt: {', '.join(failed)}. Written to {p} with a "
            f"_reason for each. Fix the run; do not edit the receipt."
        )
    return p
