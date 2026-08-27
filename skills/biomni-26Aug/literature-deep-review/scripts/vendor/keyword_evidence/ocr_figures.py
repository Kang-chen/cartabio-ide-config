"""In-figure OCR for the literature keyword evidence skill.

Primary OCR backend: EasyOCR (chosen for this environment because PaddleOCR 3.7.0
currently fails with NotImplementedError on PaddlePaddle 3.3 OneDNN — see
references/library_comparison.md).

Output per figure (cached as JSON):
{
  "figure_id": str,
  "image_path": str,
  "ocr": [
    {"text": str, "conf": float, "bbox": [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]}
  ]
}
"""
from __future__ import annotations
import json
import pathlib
import sys
import time
from typing import Any

# We initialise the reader lazily so that callers that only need the cache
# don't pay the import cost.
_READER = None
_READER_LANGS: tuple[str, ...] = ()


class OcrUnavailable(RuntimeError):
    """The OCR engine could not be started at all.

    Distinct from "this figure had no readable text": a missing easyocr, a
    broken torch install or a failed first-run model download fails EVERY
    figure identically. Collapsing the two let an engine failure be swallowed
    per figure and then cached as an empty result, so the run produced no
    provenance boxes, said nothing, and stayed broken on the next run even
    after the environment was fixed.
    """


def _get_reader(langs=("en",)):
    """Return a cached EasyOCR Reader (lazy)."""
    global _READER, _READER_LANGS
    if _READER is not None and _READER_LANGS == tuple(langs):
        return _READER
    try:
        import easyocr
    except ImportError as exc:
        raise OcrUnavailable(
            "easyocr is not installed, so no in-figure text can be read. "
            "Rerun scripts/install.sh to install and initialize it."
        ) from exc
    try:
        _READER = easyocr.Reader(list(langs), gpu=False, verbose=False)
    except Exception as exc:  # model download / torch / device failures
        raise OcrUnavailable(f"easyocr could not start: {exc}") from exc
    _READER_LANGS = tuple(langs)
    return _READER


def ocr_image(
    image_path: str | pathlib.Path,
    min_conf: float = 0.5,
    langs: tuple[str, ...] = ("en",),
) -> list[dict[str, Any]]:
    """Run EasyOCR on one image, returning [{"text", "conf", "bbox"}].

    bbox is the EasyOCR polygon (list of 4 [x,y] points).
    Lines with confidence < min_conf are filtered out (cuts decorative noise).
    """
    reader = _get_reader(langs)
    raw = reader.readtext(str(image_path), detail=1)
    out = []
    for bbox, text, conf in raw:
        if conf is None:
            continue
        if conf < min_conf:
            continue
        clean = text.strip()
        if len(clean) < 2:
            continue
        out.append({
            "text": clean,
            "conf": float(conf),
            "bbox": [[float(x), float(y)] for x, y in bbox],
        })
    return out


def _should_skip(f: dict[str, Any], min_size_px: int = 100) -> str | None:
    """Return a reason string if this figure should be skipped from OCR, else None."""
    image_path = f.get("image_path")
    if not image_path:
        return "no_image_path"
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            w, h = im.size
        if min(w, h) < min_size_px:
            return f"too_small_{w}x{h}"
    except Exception:
        return None  # let the OCR pass try anyway
    return None


def ocr_figures(
    figures: list[dict[str, Any]],
    cache_dir: str | pathlib.Path | None = None,
    min_conf: float = 0.5,
    langs: tuple[str, ...] = ("en",),
    verbose: bool = False,
    min_size_px: int = 100,
    require_caption: bool = False,
) -> list[dict[str, Any]]:
    """OCR every figure (with image_path) in `figures`.

    Returns a list of dicts mirroring `figures` plus an "ocr" key.
    Cache key: `<figure_id>.ocr.json` under cache_dir/<paper_id>/.

    Skips OCR for figures that are clearly decorative:
      - smaller than min_size_px on either axis (default 100)
      - if require_caption=True, also skip figures without a caption
    """
    out = []
    for f in figures:
        figure_id = f.get("figure_id")
        image_path = f.get("image_path")
        rec = dict(f)
        if not image_path:
            rec["ocr"] = []
            rec["ocr_attempted"] = False
            rec["ocr_status"] = "not_attempted"
            rec["ocr_error"] = "missing_image_path"
            out.append(rec)
            continue
        skip_reason = _should_skip(f, min_size_px=min_size_px)
        if skip_reason is None and require_caption and not f.get("caption"):
            skip_reason = "no_caption"
        if skip_reason:
            rec["ocr"] = []
            rec["_ocr_skipped"] = skip_reason
            rec["ocr_attempted"] = False
            rec["ocr_status"] = "skipped"
            rec["ocr_error"] = skip_reason
            if verbose:
                print(f"[ocr] skip {figure_id}: {skip_reason}")
            out.append(rec)
            continue
        cache_path = None
        if cache_dir is not None:
            paper_id = rec.get("paper_id", "unknown")
            subdir = pathlib.Path(cache_dir) / str(paper_id)
            subdir.mkdir(parents=True, exist_ok=True)
            cache_path = subdir / f"{figure_id}.ocr.json"
            if cache_path.exists():
                try:
                    with open(cache_path) as fp:
                        cached = json.load(fp)
                    rec["ocr"] = cached.get("ocr", [])
                    rec["ocr_attempted"] = True
                    rec["ocr_status"] = (
                        "completed" if rec["ocr"] else "empty"
                    )
                    rec["ocr_error"] = ""
                    if verbose:
                        print(f"[ocr] cache hit {figure_id}")
                    out.append(rec)
                    continue
                except Exception:
                    pass
        t0 = time.time()
        failed = False
        error = ""
        try:
            lines = ocr_image(image_path, min_conf=min_conf, langs=langs)
        except OcrUnavailable:
            raise  # engine-level: every remaining figure would fail identically
        except Exception as e:
            print(f"[ocr] FAILED {figure_id}: {e}", file=sys.stderr)
            lines, failed, error = [], True, str(e)
        rec["ocr"] = lines
        rec["ocr_attempted"] = True
        rec["ocr_status"] = "failed" if failed else (
            "completed" if lines else "empty"
        )
        rec["ocr_error"] = error
        if failed:
            rec["_ocr_failed"] = True
        if verbose:
            print(f"[ocr] {figure_id}: {len(lines)} lines in {time.time()-t0:.1f}s")
        # Never cache a failure. A cached empty result is indistinguishable from
        # a figure that genuinely has no text, so it would survive a fixed
        # environment and keep the boxes missing on every later run.
        if cache_path is not None and not failed:
            try:
                with open(cache_path, "w") as fp:
                    json.dump({
                        "figure_id": figure_id,
                        "image_path": str(image_path),
                        "ocr": lines,
                        "ocr_attempted": True,
                        "ocr_status": "completed" if lines else "empty",
                        "ocr_error": "",
                    }, fp)
            except Exception:
                pass
        out.append(rec)
    return out


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("image_paths", nargs="+")
    ap.add_argument("--min-conf", type=float, default=0.5)
    args = ap.parse_args()
    for p in args.image_paths:
        lines = ocr_image(p, min_conf=args.min_conf)
        print(json.dumps({"image": p, "n_lines": len(lines),
                         "lines": [{"text": l["text"], "conf": round(l["conf"], 3)}
                                  for l in lines]}, indent=2))
