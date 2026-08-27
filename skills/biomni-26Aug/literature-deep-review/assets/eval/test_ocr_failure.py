"""OCR that cannot run must say so, not quietly return nothing.

The engine fails as a unit: a missing easyocr, a broken torch install or a
failed first-run model download fails every figure identically. The original
code caught that per figure, printed only under verbose (the caller passes
verbose=False), and then CACHED the empty result — so a run produced no
provenance boxes, reported nothing wrong, and stayed broken on the next run
even after the environment was fixed.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

VENDOR = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "vendor" / "keyword_evidence"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

import ocr_figures  # noqa: E402


@pytest.fixture
def figure(tmp_path):
    from PIL import Image
    path = tmp_path / "fig1.png"
    Image.new("RGB", (400, 300), "white").save(path)
    return {"figure_id": "fig1", "paper_id": "10.1000/alpha",
            "image_path": str(path), "caption": "Figure 1. Result."}


def test_a_dead_engine_raises_instead_of_returning_empty(figure, tmp_path, monkeypatch):
    def dead(*_a, **_k):
        raise ocr_figures.OcrUnavailable("easyocr is not installed")
    monkeypatch.setattr(ocr_figures, "ocr_image", dead)
    with pytest.raises(ocr_figures.OcrUnavailable):
        ocr_figures.ocr_figures([figure], cache_dir=tmp_path / "cache")


def test_a_dead_engine_never_poisons_the_cache(figure, tmp_path, monkeypatch):
    """The expensive failure: an empty cached result is indistinguishable from a
    figure with genuinely no text, so it survives a fixed environment."""
    cache = tmp_path / "cache"
    monkeypatch.setattr(ocr_figures, "ocr_image",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ocr_figures.OcrUnavailable("no easyocr")))
    with pytest.raises(ocr_figures.OcrUnavailable):
        ocr_figures.ocr_figures([figure], cache_dir=cache)
    assert list(cache.rglob("*.ocr.json")) == []


def test_a_single_bad_image_is_survivable_but_not_cached(figure, tmp_path, capsys):
    """One unreadable image is not an engine failure: keep going, but don't
    record the emptiness as though it were a real read."""
    cache = tmp_path / "cache"
    ocr_figures.ocr_image = lambda *a, **k: (_ for _ in ()).throw(ValueError("corrupt"))
    out = ocr_figures.ocr_figures([figure], cache_dir=cache)
    assert out[0]["ocr"] == []
    assert out[0]["_ocr_failed"] is True
    assert out[0]["ocr_attempted"] is True
    assert out[0]["ocr_status"] == "failed"
    assert "corrupt" in out[0]["ocr_error"]
    assert list(cache.rglob("*.ocr.json")) == []
    assert "FAILED" in capsys.readouterr().err


def test_a_real_read_is_still_cached(figure, tmp_path, monkeypatch):
    """The fix must not disable caching for the working path."""
    cache = tmp_path / "cache"
    monkeypatch.setattr(ocr_figures, "ocr_image",
                        lambda *a, **k: [{"text": "GRN", "conf": 0.9,
                                          "bbox": [[0, 0], [9, 0], [9, 9], [0, 9]]}])
    result = ocr_figures.ocr_figures([figure], cache_dir=cache)
    assert result[0]["ocr_attempted"] is True
    assert result[0]["ocr_status"] == "completed"
    assert result[0]["ocr_error"] == ""
    written = list(cache.rglob("*.ocr.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text())["ocr"][0]["text"] == "GRN"


def test_missing_image_has_explicit_unattempted_status(tmp_path):
    row = {"figure_id": "missing", "paper_id": "P", "caption": ""}

    result = ocr_figures.ocr_figures([row], cache_dir=tmp_path / "cache")

    assert result[0]["ocr_attempted"] is False
    assert result[0]["ocr_status"] == "not_attempted"
    assert result[0]["ocr_error"] == "missing_image_path"


def test_missing_easyocr_is_reported_as_unavailable(monkeypatch):
    """ImportError must surface as OcrUnavailable, not as a bare ImportError
    that the per-figure handler would swallow."""
    ocr_figures._READER = None
    monkeypatch.setitem(sys.modules, "easyocr", None)
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def no_easyocr(name, *args, **kwargs):
        if name == "easyocr":
            raise ImportError("No module named 'easyocr'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", no_easyocr)
    with pytest.raises(ocr_figures.OcrUnavailable, match="not installed"):
        ocr_figures._get_reader()


def test_standard_installer_reuses_working_ocr_before_installing_fallback():
    install = (
        pathlib.Path(__file__).resolve().parent.parent / "scripts" / "install.sh"
    ).read_text()
    probe = install.index("if python -c 'import easyocr, torch'")
    fallback = install.index('PACKAGES+=("easyocr>=1.7,<2")')
    assert probe < fallback
    assert "existing EasyOCR and PyTorch detected" in install
    assert "easyocr.Reader" in install
    assert '"easyocr", "torch"' in install
    assert "WITH_OCR" not in install
    assert "--with-ocr) ;;" in install  # accepted only for backward compatibility


@pytest.mark.parametrize(("probe_exit", "expects_easyocr"), [(0, False), (1, True)])
def test_installer_only_requests_easyocr_when_the_import_probe_fails(
    tmp_path, probe_exit, expects_easyocr
):
    install = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "install.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python"
    fake_python.write_text(
        "#!/bin/sh\n"
        f"if [ \"$1\" = \"-c\" ]; then exit {probe_exit}; fi\n"
        "cat >/dev/null\n"
    )
    fake_uv = bin_dir / "uv"
    fake_uv.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$INSTALL_LOG"\n')
    fake_python.chmod(0o755)
    fake_uv.chmod(0o755)
    log = tmp_path / "install.log"
    env = dict(os.environ)
    env.update({"PATH": f"{bin_dir}:{env['PATH']}", "INSTALL_LOG": str(log)})

    completed = subprocess.run(
        ["bash", str(install)], env=env, text=True, capture_output=True, check=True
    )

    requested_easyocr = "easyocr" in log.read_text().lower()
    assert requested_easyocr is expects_easyocr
    assert ("detected; skipping" in completed.stdout) is (not expects_easyocr)
