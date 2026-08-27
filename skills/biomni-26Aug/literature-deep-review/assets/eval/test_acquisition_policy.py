"""Acquisition is governed by actual PDF availability, not OA metadata."""
from __future__ import annotations

import inspect
import pathlib
import sys
import time
from types import SimpleNamespace


VENDOR = (
    pathlib.Path(__file__).resolve().parent.parent
    / "scripts" / "vendor" / "keyword_evidence"
)
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

import acquire  # noqa: E402
import http_policy  # noqa: E402


def test_provider_direct_pdf_is_preserved_without_pmc_render_synthesis():
    from references_to_corpus import record_from_litsearch

    direct = record_from_litsearch({
        "title": "Direct PDF",
        "pmcid": "PMC123",
        "url": "https://example.org/paper.pdf?download=1",
        "provider": "exa",
    }, 0)
    pmc_only = record_from_litsearch({
        "title": "PMC record",
        "pmcid": "PMC456",
        "url": "https://europepmc.org/article/PMC/456",
    }, 1)

    assert direct["pdf_url"] == "https://example.org/paper.pdf?download=1"
    assert pmc_only["pdf_url"] is None


def test_full_retrieval_walk_is_the_default():
    parameter = inspect.signature(acquire.acquire_pdf).parameters["fast_fail_closed"]
    assert parameter.default is False


def test_public_pmc_author_manuscript_is_used_when_oa_subset_has_no_pdf(
    tmp_path, monkeypatch
):
    seen: list[str] = []
    monkeypatch.setattr(acquire, "_session", object)
    monkeypatch.setattr(acquire, "_try_epmc_fulltext_xml", lambda _s, _id: None)
    monkeypatch.setattr(acquire, "_try_pmc_oa", lambda _s, _id: None)

    def try_pdf(_session, url, dest, *, allow_landing_hop=True):
        seen.append(url)
        assert allow_landing_hop is True
        dest.write_bytes(b"%PDF-1.7\n")
        return True, "ok_via_landing_hop"

    monkeypatch.setattr(acquire, "_try_url_as_pdf", try_pdf)

    result = acquire.acquire_pdf(
        {"paper_id": "P", "pmcid": "PMC123"},
        tmp_path,
        use_cache=False,
    )

    assert seen == [acquire._PMC_ARTICLE.format(pmcid="PMC123")]
    assert result["oa_source"] == "ncbi_pmc_author_manuscript"
    assert result["access_state"] == acquire.ACCESS_FREE_TO_READ


def test_landing_hop_accepts_a_pdf_without_oa_metadata(tmp_path, monkeypatch):
    landing = "https://publisher.example/article"
    pdf = "https://publisher.example/article.pdf"
    calls: list[str] = []

    def download(_session, url, dest):
        calls.append(url)
        if url == pdf:
            dest.write_bytes(b"%PDF-1.7\n")
            return True, "ok"
        return False, "not_pdf:text/html"

    monkeypatch.setattr(acquire, "_download", download)
    monkeypatch.setattr(acquire, "_pdf_url_from_landing", lambda _s, _u: pdf)

    ok, reason = acquire._try_url_as_pdf(object(), landing, tmp_path / "paper.pdf")

    assert ok is True
    assert reason == "ok_via_landing_hop"
    assert calls == [landing, pdf]


def test_accessible_publisher_pdf_is_readable_and_ocr_eligible_by_default(
    tmp_path, monkeypatch
):
    landing = "https://publisher.example/article"
    seen: list[tuple[str, bool]] = []

    monkeypatch.setattr(acquire, "_session", object)
    monkeypatch.setattr(
        acquire,
        "_epmc_access",
        lambda _session, _doi: (acquire.ACCESS_UNKNOWN, {"decided_by": "test"}),
    )

    def try_pdf(_session, url, dest, *, allow_landing_hop=True):
        seen.append((url, allow_landing_hop))
        dest.write_bytes(b"%PDF-1.7\n")
        return True, "ok_via_landing_hop"

    monkeypatch.setattr(acquire, "_try_url_as_pdf", try_pdf)

    result = acquire.acquire_pdf(
        {
            "paper_id": "paper-1",
            "doi": "10.1000/example",
            "landing_url": landing,
        },
        tmp_path,
        use_cache=False,
    )

    assert seen == [(landing, True)]
    assert result["oa_source"] == "accessible_internet"
    assert result["access_state"] == acquire.ACCESS_FREE_TO_READ
    assert pathlib.Path(result["local_pdf"]).exists()
    # Read/OCR eligibility is independent from permission to reproduce a crop.
    assert result["figure_embedding_allowed"] is False


def test_xml_source_can_fetch_accessible_pdf_for_figure_ocr(tmp_path, monkeypatch):
    landing = "https://publisher.example/article"
    pdf = "https://publisher.example/article.pdf"
    epmc_url = acquire._EPMC_RENDER_PDF.format(pmcid="PMC123")

    def download(_session, url, dest, **_kwargs):
        if url == pdf:
            dest.write_bytes(b"%PDF-1.7\n")
            return True, "ok"
        return False, "not_pdf:text/html"

    monkeypatch.setattr(acquire, "_download", download)
    monkeypatch.setattr(acquire, "_try_pmc_oa", lambda _s, _pmcid: None)
    monkeypatch.setattr(
        acquire,
        "_pdf_url_from_landing",
        lambda _s, _u, **_kwargs: pdf,
    )

    status, path, source = acquire._try_figures_pdf(
        object(),
        {"pmcid": "PMC123", "landing_url": landing},
        tmp_path / "figures.pdf",
        [{"source": "europe_pmc_render", "url": epmc_url, "reason": "http_403"}],
        deadline=time.monotonic() + 10,
    )

    assert status == acquire.FIGURES_PDF_OK
    assert source == "ncbi_pmc_author_manuscript"
    assert path and pathlib.Path(path).exists()


def test_old_negative_cache_does_not_hide_new_retrieval_routes():
    current = {"policy_version": acquire.ACQUIRE_POLICY_VERSION, "ts": 10**12}
    old = {"policy_version": acquire.ACQUIRE_POLICY_VERSION - 1, "ts": 10**12}
    assert acquire._retrieval_miss_fresh(current) is True
    assert acquire._retrieval_miss_fresh(old) is False


def test_retry_after_is_honored_with_jitter(monkeypatch):
    response = SimpleNamespace(headers={"Retry-After": "7"})
    monkeypatch.setattr(acquire.random, "uniform", lambda _low, _high: 0.25)

    assert acquire._retry_delay(response, attempt=0, base_sleep_s=1.5) == 7.25


def test_europe_pmc_requests_are_paced_across_local_workers(tmp_path, monkeypatch):
    calls: list[str] = []
    sleeps: list[float] = []
    times = iter((100.0, 100.0, 100.1, 100.5))
    session = SimpleNamespace(get=lambda url, **_kwargs: calls.append(url) or object())
    monkeypatch.setattr(http_policy, "EPMC_PACER", tmp_path / "epmc.pacer")
    monkeypatch.setattr(http_policy, "EPMC_MIN_INTERVAL_S", 0.5)
    monkeypatch.setattr(http_policy.time, "time", lambda: next(times))
    monkeypatch.setattr(http_policy.time, "sleep", sleeps.append)

    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    http_policy.polite_get(session, url)
    http_policy.polite_get(session, url)

    assert calls == [url, url]
    assert [round(value, 3) for value in sleeps] == [0.4]


def test_pipeline_retries_transient_miss_once_and_recovers(tmp_path, monkeypatch):
    import evidence_first

    calls = 0

    def acquire_one(record, _dirs, opts):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "paper_id": "p1",
                "status": "not_retrieved",
                "record": {
                    **record,
                    "_not_retrieved_kind": "retrieval_failed",
                    "_not_retrieved_reason": "retrieval_failed: timeout",
                },
            }
        assert opts["refresh_acquisition"] is True
        return {
            "paper_id": "p1",
            "status": "parsed",
            "record": {**record, "local_pdf": str(tmp_path / "p1.pdf")},
            "parsed": {
                "paper_id": "p1", "parser": "test", "n_pages": 1,
                "sentences": [], "figures": [],
            },
        }

    monkeypatch.setattr(evidence_first, "_acquire_and_parse_one", acquire_one)
    monkeypatch.setattr(evidence_first, "_resolve_parse_jobs", lambda *_: 1)
    args = SimpleNamespace(
        run_root=str(tmp_path), cache_dir=None, marker_fallback=False,
        min_sentences=20, parse_jobs=1, refresh_acquisition=False,
        fast_fail_closed=False, no_retry_transient=False,
    )
    manifest = {"config": {}, "metrics": {}, "papers": {}, "errors": []}
    papers, misses, _parsed = evidence_first._process_papers(
        args, [{"paper_id": "p1", "title": "Paper"}], manifest, {}
    )
    assert calls == 2
    assert [paper["paper_id"] for paper in papers] == ["p1"]
    assert misses == []
    assert manifest["metrics"]["transient_retry_attempts"] == 1
    assert manifest["metrics"]["transient_retry_recovered"] == 1


def test_preprocess_only_succeeds_when_a_shard_is_entirely_unretrievable(
    tmp_path, monkeypatch
):
    import json
    import evidence_first

    claims = tmp_path / "claims.csv"
    records = tmp_path / "records.jsonl"
    claims.write_text("claim_id,claim_text\nC1,Claim text\n")
    records.write_text(json.dumps({"paper_id": "closed"}) + "\n")
    miss = {
        "paper_id": "closed",
        "_not_retrieved_kind": "paywalled",
        "_not_retrieved_reason": "paywalled: confirmed closed",
    }
    monkeypatch.setattr(evidence_first, "_vendor_modules", lambda: {})
    monkeypatch.setattr(
        evidence_first,
        "_process_papers",
        lambda *_args: ([], [miss], {}),
    )

    rc = evidence_first.main([
        "--run-root", str(tmp_path / "run"),
        "--claims", str(claims),
        "--records", str(records),
        "--review-mode", "broad",
        "--backend", "none",
        "--ocr", "off",
        "--preprocess-only",
    ])
    assert rc == 0
    manifest = json.loads((tmp_path / "run" / "run_manifest.json").read_text())
    assert manifest["status"] == "preprocessed"
    assert manifest["metrics"]["papers_not_retrieved"] == 1
