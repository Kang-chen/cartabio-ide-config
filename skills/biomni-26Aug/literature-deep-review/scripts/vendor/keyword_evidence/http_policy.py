"""Shared polite HTTP pacing for concurrent literature workers."""
from __future__ import annotations

import contextlib
import os
import pathlib
import tempfile
import time
import urllib.parse

import requests

try:
    import fcntl
except ImportError:  # pragma: no cover - Biomni managed machines are POSIX
    fcntl = None  # type: ignore[assignment]


EUROPE_PMC_HOSTS = frozenset({"europepmc.org", "www.ebi.ac.uk"})
DEFAULT_EPMC_MIN_INTERVAL_S = 0.5
EPMC_MIN_INTERVAL_S = float(os.environ.get(
    "EPMC_MIN_INTERVAL_S", str(DEFAULT_EPMC_MIN_INTERVAL_S)))
RATE_LIMIT_DIR = pathlib.Path(os.environ.get(
    "LITERATURE_RATE_LIMIT_DIR", tempfile.gettempdir()))
EPMC_PACER = RATE_LIMIT_DIR / "phylo-literature-europe-pmc.pacer"


def is_europe_pmc_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host in EUROPE_PMC_HOSTS or host.endswith(".europepmc.org")


def _pace_europe_pmc() -> None:
    """Space request starts across all worker processes on this machine."""
    if EPMC_MIN_INTERVAL_S <= 0 or fcntl is None:
        return
    EPMC_PACER.parent.mkdir(parents=True, exist_ok=True)
    with EPMC_PACER.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            try:
                previous = float(handle.read().strip() or 0.0)
            except ValueError:
                previous = 0.0
            now = time.time()
            wait = EPMC_MIN_INTERVAL_S - (now - previous)
            if wait > 0:
                time.sleep(wait)
            handle.seek(0)
            handle.truncate()
            handle.write(str(time.time()))
            handle.flush()
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def polite_get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    if is_europe_pmc_url(url):
        _pace_europe_pmc()
    return session.get(url, **kwargs)
