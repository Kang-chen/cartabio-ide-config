#!/usr/bin/env python3
"""
run_provenance.py — derive the delivered report's run-provenance disclosure from
the orchestration manifest, and GATE the report so a silent predictor swap can
never ship.

WHY THIS FILE EXISTS
--------------------
In an audited run the user asked for AlphaFold2 (the primary). It sat in MSA
search for the full poll bound with ZERO output files, still reporting
status=running (not failed), and was cancelled. Boltz-2 then produced every
delivered number (mean pLDDT 81.77, pTM 0.779). The delivered result was
therefore NOT from the requested method — but nothing forced the report to say
so. `fold_orchestrate.py` already records exactly what is needed
(`poll_timeout_s`, `predictor_order`, `fallback_trail`, per-attempt
`outcome/status/n_files/cancelled`, `chosen_predictor`); the gap was that the
report writer could ignore it.

This module:
  * render_run_provenance(manifest) -> the canonical disclosure text, built FROM
    THE MANIFEST (not from what the agent remembers). When a fallback occurred it
    states which method was requested, that it was cancelled at the timeout while
    still running with no output, which method actually produced every reported
    number, the poll bound actually used, and that the requested method may have
    been obtainable with a longer bound.
  * check_report(manifest, report_text) -> the LOUD GATE. If the manifest shows a
    fallback but the report omits the disclosure, it fails (raises / exits non-
    zero). Run it before finalizing any report.

The reported poll bound is ALWAYS the manifest's `poll_timeout_s` (the value
actually used), never a documented default — the audited run overrode the 900 s
default to 2700 s, which is exactly why the number must be read, not restated.
"""
import sys
import json
import argparse


# method-key -> human name (as used elsewhere in the skill)
_HUMAN = {"alphafold": "AlphaFold v2", "boltz": "Boltz-2",
          "chai": "Chai-1", "esmfold": "ESMCFold2"}
# substrings that count as "this method is named" in a report (case-insensitive)
_TOKENS = {"alphafold": ["alphafold"], "boltz": ["boltz"], "chai": ["chai"],
           "esmfold": ["esmcfold", "esmfold"]}


def human(method_key):
    if method_key is None:
        return "unknown"
    return _HUMAN.get(str(method_key).lower(), str(method_key))


def load_manifest(path):
    with open(path) as fh:
        return json.load(fh)


def _primary(manifest):
    """The predictor that was intended first: explicit user request if any, else
    the size-default primary (predictor_order[0])."""
    req = manifest.get("requested_methods")
    if req:
        return str(req[0]).lower()
    order = manifest.get("predictor_order") or []
    return str(order[0]).lower() if order else None


def _primary_attempt(manifest, primary):
    for att in manifest.get("attempts", []):
        if str(att.get("method", "")).lower() == primary:
            return att
    return None


def needs_disclosure(manifest):
    """True when the delivered numbers did NOT come from the intended primary:
    a non-empty fallback_trail, or chosen_predictor != primary."""
    if manifest.get("fallback_trail"):
        return True
    chosen = manifest.get("chosen_predictor")
    primary = _primary(manifest)
    return bool(chosen and primary and str(chosen).lower() != primary)


def render_run_provenance(manifest):
    """Canonical run-provenance text, derived entirely from the manifest.

    Safe to include verbatim in the results narrative. Always states the
    predictor used and the poll bound actually used; when a fallback occurred it
    adds the full disclosure (requested method, cancelled-at-timeout-with-0-files,
    which method produced the numbers, longer-bound caveat)."""
    chosen = manifest.get("chosen_predictor")
    primary = _primary(manifest)
    poll = manifest.get("poll_timeout_s")
    poll_txt = f"{poll} s" if poll is not None else "unknown (not in manifest)"
    req = manifest.get("requested_methods")
    req_txt = (f"explicitly requested ({', '.join(human(m) for m in req)})"
               if req else "selected by the size-first default")

    lines = []
    if not needs_disclosure(manifest):
        lines.append(
            f"**Predictor provenance.** {human(chosen)} produced every reported "
            f"number. It was the primary predictor ({req_txt}) and succeeded on "
            f"the first attempt; no fallback occurred. Poll bound actually used: "
            f"{poll_txt} per job (from the run manifest).")
        return "\n".join(lines)

    # --- fallback occurred: full disclosure ---
    pa = _primary_attempt(manifest, primary)
    lines.append(
        f"**Predictor provenance — a fallback occurred; read this in the "
        f"results, not only the caveats.** The requested/primary predictor was "
        f"**{human(primary)}** ({req_txt}), but **{human(chosen)}** produced "
        f"every number reported here (mean pLDDT, pTM, per-residue pLDDT, and the "
        f"delivered structure). {human(primary)} did NOT produce the delivered "
        f"result.")

    if pa is not None:
        outcome = str(pa.get("outcome"))
        nfiles = pa.get("n_files", 0)
        secs = pa.get("seconds")
        cancelled = pa.get("cancelled")
        secs_txt = f" after {secs:g} s" if isinstance(secs, (int, float)) else ""
        if outcome == "timeout":
            detail = (f"{human(primary)} was cancelled at the {poll_txt} poll "
                      f"bound while still running with {nfiles} output "
                      f"file(s) (status was not 'failed' — it was still searching).")
        elif outcome == "empty":
            detail = (f"{human(primary)} reached a terminal status but produced "
                      f"{nfiles} output file(s){secs_txt}, so it was treated as a "
                      f"failure and cancelled.")
        elif outcome in ("failed",):
            detail = f"{human(primary)} failed{secs_txt} (status={pa.get('status')})."
        elif outcome == "exception":
            detail = f"{human(primary)} raised an error{secs_txt}: {pa.get('error')}."
        else:
            detail = (f"{human(primary)} did not complete{secs_txt} "
                      f"(outcome={outcome}, {nfiles} file(s)).")
        if cancelled:
            detail += " The GPU job was cancelled to free the slot for the fallback."
        lines.append(detail)

    trail = manifest.get("fallback_trail") or []
    if trail:
        trail_txt = "; ".join(f"{human(t.get('from'))} -> {t.get('reason')}"
                              for t in trail)
        lines.append(f"Fallback trail (from manifest): {trail_txt}.")

    if primary in ("alphafold", "boltz", "chai"):
        lines.append(
            f"Because {human(primary)} was still running (not failed) at the bound, "
            f"its result may well have been obtainable with a **longer poll bound** "
            f"(`poll_timeout_s` > {poll_txt}); the fallback reflects the time "
            f"budget, not a failure of {human(primary)}.")

    lines.append(f"Poll bound actually used: {poll_txt} per job (from the run "
                 f"manifest, not a documented default).")
    return "\n".join(lines)


def check_report(manifest, report_text):
    """Loud gate. Returns a dict; raises AssertionError when disclosure is
    required but missing (so a report cannot silently ship a swapped predictor).

    Result: {"ok": bool, "required": bool, "missing": [str,...],
             "disclosure": <canonical text>}.
    """
    result = {"required": needs_disclosure(manifest),
              "disclosure": render_run_provenance(manifest),
              "missing": [], "ok": True}
    if not result["required"]:
        return result

    low = (report_text or "").lower()
    chosen = str(manifest.get("chosen_predictor") or "").lower()
    primary = _primary(manifest)

    def _named(mkey):
        return any(tok in low for tok in _TOKENS.get(mkey, [mkey]))

    if primary and not _named(primary):
        result["missing"].append(
            f"requested/primary predictor {human(primary)} is not named in the report")
    if chosen and not _named(chosen):
        result["missing"].append(
            f"the predictor that produced the numbers ({human(chosen)}) is not named")

    poll = manifest.get("poll_timeout_s")
    if poll is not None and str(int(poll)) not in low:
        result["missing"].append(
            f"the poll bound actually used ({poll} s) is not stated")

    if not any(w in low for w in ("fallback", "fell back", "fall back",
                                  "cancel", "timeout", "timed out", "stall")):
        result["missing"].append(
            "no fallback/cancel/timeout language (the swap is not disclosed)")

    if not any(w in low for w in ("longer", "more time", "larger timeout",
                                  "longer bound", "longer window", "longer poll")):
        result["missing"].append(
            "does not note the requested method might have finished with a longer bound")

    result["ok"] = not result["missing"]
    if not result["ok"]:
        raise AssertionError(
            "Report is missing required fallback disclosure:\n  - "
            + "\n  - ".join(result["missing"])
            + "\n\nInclude this canonical disclosure (from the manifest):\n\n"
            + result["disclosure"])
    return result


def main():
    ap = argparse.ArgumentParser(
        description="Render or gate the run-provenance / fallback disclosure from "
                    "a fold run manifest.")
    ap.add_argument("--manifest", required=True, help="<name>_run_manifest.json")
    ap.add_argument("--report", default=None,
                    help="path to the draft report (markdown/text) to GATE; "
                         "omit to just --render")
    ap.add_argument("--render", action="store_true",
                    help="print the canonical disclosure text and exit")
    a = ap.parse_args()

    manifest = load_manifest(a.manifest)
    if a.render or not a.report:
        print(render_run_provenance(manifest))
        return
    with open(a.report) as fh:
        report_text = fh.read()
    try:
        res = check_report(manifest, report_text)
    except AssertionError as e:
        print("GATE FAILED:\n" + str(e), file=sys.stderr)
        sys.exit(1)
    if res["required"]:
        print("GATE PASSED: fallback disclosure present.")
    else:
        print("GATE PASSED: no fallback in manifest; no disclosure required.")


if __name__ == "__main__":
    main()
