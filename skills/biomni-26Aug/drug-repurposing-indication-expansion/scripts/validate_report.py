"""Post-build report QC: reconcile the PDF's numbers against the analysis outputs.

Catches the classic failure mode of a stale or hallucinated number slipping into the
narrative (e.g. the report says "34 significant reversers" but the table has 33). Extracts
text from the built PDF and checks that the key headline statistics ACTUALLY APPEAR in the
report, and that no contradictory count is present.

This is a guardrail, not a hard gate: it returns a structured report of pass/warn items
that the agent should review before delivering. Also verifies figure references resolve and
the reference list is non-empty.

Public API:
  validate(pdf_path, stats, tables, top_hit_name=None, trials=None,
           disease_label=None, n_references=None, controls=None, verdict=None,
           compound_flags=None) -> dict(status, checks:list, warnings:list)

If `top_hit_name` is given (the canonical #1-ranked drug), two extra checks run: the drug name
must appear verbatim in the report, and a rationale sentence mentioning it must be present -- this
enforces that the top-ranked hit is never emitted unexplained.

If `trials` and `disease_label` are given, two trials-consistency checks run: every distinct
`query_condition` in the trials frame must normalize to a string containing or contained by
`disease_label`, and any integer the report quotes next to 'trial'/'trials' must match an
`n_trials_matched` value rather than an `n_trials_query_total` value.

If `n_references` is given (or stats['references'] is a list), the body's distinct [n] markers
must not exceed the reference-list length, and references lacking a locator token are counted.

If `controls` (the controls DataFrame) or `verdict` (a status string / controls_verdict dict)
is given, and if `compound_flags` (report_config['compound_flags']) is given, three warn-level
consistency checks run: a failed verdict must be stated in the report text; a ranked "approved
candidates" recommendation list must not appear under a failed verdict; and every flagged
compound must carry its flag/caveat wherever it is named. These mirror the hard build-time gate
in build_report so a stale call site is still caught before delivery.
"""
import re


def _extract_text(pdf_path):
    try:
        from pypdf import PdfReader
        r = PdfReader(pdf_path)
        return "\n".join((p.extract_text() or "") for p in r.pages)
    except Exception as e:  # noqa: BLE001
        return f"__EXTRACT_FAILED__:{e}"


def validate(pdf_path, stats, tables, top_hit_name=None, trials=None,
             disease_label=None, n_references=None, controls=None, verdict=None,
             compound_flags=None):
    text = _extract_text(pdf_path)
    checks, warnings = [], []
    if text.startswith("__EXTRACT_FAILED__"):
        return dict(status="error", checks=[], warnings=[f"PDF text extraction failed: {text}"])

    norm = re.sub(r"\s+", " ", text)

    def present(value):
        # match the integer as a standalone token, allowing thousands separators
        v = str(value)
        vc = f"{int(value):,}" if str(value).isdigit() else v
        return bool(re.search(rf"(?<!\d){re.escape(v)}(?!\d)", norm) or
                    re.search(rf"(?<!\d){re.escape(vc)}(?!\d)", norm))

    # Headline stats that SHOULD appear verbatim in the report
    key_stats = {
        "n_drugs (drugs screened)": stats.get("n_drugs"),
        "n_approved (approved w/ signature)": stats.get("n_approved"),
        "n_appr_sig (significant approved reversers)": stats.get("n_appr_sig"),
        "n_up (signature up genes)": stats.get("n_up"),
        "n_dn (signature down genes)": stats.get("n_dn"),
    }
    for label, val in key_stats.items():
        if val is None:
            continue
        ok = present(val)
        checks.append(dict(check=f"stat present: {label} = {val}", ok=ok))
        if not ok:
            warnings.append(f"Headline stat '{label}' = {val} not found verbatim in report text.")

    # Top approved drugs should be named in the report
    appr = tables.get("approved")
    if appr is not None and len(appr):
        top5 = [str(d) for d in appr.head(5)["drug"].tolist()]
        named = [d for d in top5 if re.search(re.escape(d), norm, flags=re.IGNORECASE)]
        checks.append(dict(check=f"top-5 approved drugs named in report ({len(named)}/5)",
                           ok=len(named) >= 3))
        if len(named) < 3:
            warnings.append(f"Only {len(named)}/5 top approved drugs are named in the report narrative.")

    # Canonical #1 hit: must be named AND rationalized (never emitted unexplained)
    if top_hit_name:
        thn = str(top_hit_name).strip()
        named = bool(re.search(re.escape(thn), norm, flags=re.IGNORECASE))
        checks.append(dict(check=f"canonical #1 hit named in report: '{thn}'", ok=named))
        if not named:
            warnings.append(f"Canonical #1-ranked hit '{thn}' is not named in the report text.")
        # rationale heuristic: the report has a 'Top-ranked candidate' callout AND the #1 name
        # appears near rationale language (explain/rationale/likely/artifact/non-specific/because)
        has_callout = bool(re.search(r"top[-\s]?ranked candidate", norm, flags=re.IGNORECASE))
        window = ""
        m = re.search(re.escape(thn), norm, flags=re.IGNORECASE)
        if m:
            window = norm[max(0, m.start() - 400): m.end() + 400].lower()
        rationale_words = ("rational", "likely", "artifact", "non-specific", "nonspecific",
                           "because", "reflect", "caution", "explain", "mechan")
        has_rationale = has_callout and any(w in window for w in rationale_words)
        checks.append(dict(check=f"canonical #1 hit '{thn}' has a rationale sentence", ok=has_rationale))
        if not has_rationale:
            warnings.append(f"No clear rationale sentence found for canonical #1-ranked hit '{thn}'.")

    # References present
    n_markers = len(re.findall(r"\[\d+\]", norm))
    checks.append(dict(check=f"reference citations present (found {n_markers} [n] markers)", ok=n_markers >= 1))
    if n_markers < 1:
        warnings.append("No [n] reference markers found in report.")

    # Reference locator check: count references lacking a verifiable locator token
    refs = stats.get("references", [])
    if not isinstance(refs, list):
        refs = []
    _locator_tokens = ("pmid", "pmcid", "doi.org/", "doi:", "nct", "http")
    n_no_locator = 0
    for i, ref in enumerate(refs, 1):
        ref_lower = str(ref).lower()
        if not any(tok in ref_lower for tok in _locator_tokens):
            n_no_locator += 1
    if refs:
        checks.append(dict(check=f"references with verifiable locator ({len(refs) - n_no_locator}/{len(refs)})",
                           ok=n_no_locator == 0))
        if n_no_locator > 0:
            warnings.append(f"{n_no_locator} reference(s) lack a PMID/DOI/URL locator token; "
                            "every reference must carry a verifiable identifier.")

    # Orphan citation markers: distinct [n] in body must not exceed reference-list length
    ref_list_len = n_references if n_references is not None else len(refs)
    if ref_list_len is not None and ref_list_len > 0:
        distinct_markers = set(int(m) for m in re.findall(r"\[(\d+)\]", norm))
        max_marker = max(distinct_markers) if distinct_markers else 0
        checks.append(dict(check=f"max citation marker [{max_marker}] <= reference list length {ref_list_len}",
                           ok=max_marker <= ref_list_len))
        if max_marker > ref_list_len:
            warnings.append(f"Body cites [{max_marker}] but the reference list has only "
                            f"{ref_list_len} entries — possible orphan/fabricated citation.")

    # Contradiction guard: look for a 'significant' count that differs from n_appr_sig
    nas = stats.get("n_appr_sig")
    if nas is not None:
        m = re.findall(r"(\d+)\s+(?:approved drugs?\s+)?(?:that\s+)?significant", norm, flags=re.IGNORECASE)
        bad = [int(x) for x in m if int(x) != int(nas) and abs(int(x) - int(nas)) < 50]
        if bad:
            warnings.append(f"Possible contradictory 'significant' counts in text: {sorted(set(bad))} vs n_appr_sig={nas}. "
                            "Verify these refer to a different quantity (e.g. all-drug vs approved-only).")

    # Trials consistency checks
    if trials is not None and disease_label is not None:
        import pandas as _pd
        if isinstance(trials, _pd.DataFrame) and len(trials) > 0 and "query_condition" in trials.columns:
            _dl_norm = re.sub(r"\s+", " ", str(disease_label).lower().strip())
            for qc in trials["query_condition"].dropna().unique():
                qc_norm = re.sub(r"\s+", " ", str(qc).lower().strip())
                if not qc_norm:
                    continue
                matches = (qc_norm in _dl_norm) or (_dl_norm in qc_norm)
                checks.append(dict(check=f"trials query_condition '{qc}' matches disease_label '{disease_label}'",
                                   ok=matches))
                if not matches:
                    warnings.append(f"Trials query_condition '{qc}' does not match report disease_label "
                                    f"'{disease_label}' — the report may be answering a different disease's "
                                    "trials question.")

            # Check that integers quoted next to 'trial'/'trials' match n_trials_matched, not n_trials_query_total
            if "n_trials_matched" in trials.columns and "n_trials_query_total" in trials.columns:
                matched_vals = set()
                for v in trials["n_trials_matched"].dropna():
                    try:
                        matched_vals.add(int(v))
                    except (ValueError, TypeError):
                        pass
                query_total_vals = set()
                for v in trials["n_trials_query_total"].dropna():
                    try:
                        query_total_vals.add(int(v))
                    except (ValueError, TypeError):
                        pass
                # Find integers near 'trial'/'trials' in the report text
                trial_nums = re.findall(r"(\d+)\s+trial", norm, flags=re.IGNORECASE)
                for tn in trial_nums:
                    tn_int = int(tn)
                    if tn_int in query_total_vals and tn_int not in matched_vals:
                        checks.append(dict(check=f"trial count {tn_int} in text is a verified n_trials_matched value",
                                           ok=False))
                        warnings.append(f"Report quotes {tn_int} next to 'trial' but that is an "
                                        "n_trials_query_total (loose full-text) value, not an "
                                        "n_trials_matched (intervention-verified) value.")

    # ---- Front-matter / verdict / flag consistency (defect-2 guardrail, warn-level) ----
    # The hard gate lives in build_report._frontmatter_consistency_gate; this re-checks the
    # rendered PDF text so a stale/edited call site is still caught before delivery.
    _status = None
    if verdict is not None:
        _status = verdict.get("status") if isinstance(verdict, dict) else str(verdict)
    elif controls is not None:
        try:
            import controls_and_moa as _cam
            _status = _cam.controls_verdict(controls)["status"]
        except Exception:
            _status = None
    norm_lc = norm.lower()
    if _status == "fail":
        _fail_tokens = ("did not validate", "not validated", "validation did not", "did not pass",
                        "failed validation", "validation failed", "exploratory")
        ok = any(t in norm_lc for t in _fail_tokens)
        checks.append(dict(check="failed controls verdict stated in report", ok=ok))
        if not ok:
            warnings.append("Controls verdict is 'fail' but the report text does not clearly "
                            "state that validation did not pass (page 1 must lead with the "
                            "failed verdict).")
        if re.search(r"approved candidates are\s*#?\s*\d", norm_lc):
            checks.append(dict(check="no ranked 'approved candidates' recommendation under failed verdict",
                               ok=False))
            warnings.append("Report presents a ranked 'approved candidates' list although the "
                            "controls verdict is 'fail'; candidates must be framed as exploratory.")
    # Every flagged compound must carry its flag/caveat wherever it is named in the report.
    _items = []
    if isinstance(compound_flags, dict):
        for k, v in compound_flags.items():
            _items.append((k, v.get("classification") if isinstance(v, dict) else v))
    elif compound_flags:
        _items = [(it.get("name", ""), it.get("classification", "caution"))
                  for it in compound_flags if isinstance(it, dict)]
    _flag_toks = ("flag", "artifact", "caution", "non-specific", "nonspecific", "cytotoxic",
                  "steroid", "unvalidated", "implausible", "caveat",
                  "promiscuous", "off-target", "assay")
    for _name, _cls in _items:
        _cls = str(_cls).strip().lower()
        if _cls in ("credible", "ok", "clear", ""):
            continue
        _nm = str(_name).strip().lower()
        if not _nm:
            continue
        for m in re.finditer(re.escape(_nm), norm_lc):
            w = norm_lc[max(0, m.start() - 200): m.end() + 200]
            if not (_cls in w or any(t in w for t in _flag_toks)):
                warnings.append(f"Compound '{_name}' is flagged '{_cls}' but is named in the "
                                "report without a nearby flag/caveat.")
                break

    status = "pass" if not warnings else "warn"
    return dict(status=status, checks=checks, warnings=warnings)


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) > 1:
        print(json.dumps(validate(sys.argv[1], {}, {}), indent=2))
