#!/usr/bin/env bash
# Minimal runtime for the claim-first grounded review pipeline.
set -euo pipefail

WITH_MARKER=0
for arg in "$@"; do
  case "$arg" in
    # Backward-compatible no-op: OCR is now checked and initialized by default.
    --with-ocr) ;;
    --with-marker-fallback) WITH_MARKER=1 ;;
    --help|-h)
      echo "Usage: $0 [--with-marker-fallback]"
      echo "Existing EasyOCR/PyTorch are reused; missing OCR dependencies are installed."
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

# Runtime dependencies, PINNED. An unpinned install into the system
# environment is not reproducible: a parser or renderer minor release changes
# extraction output, and this skill's whole premise is that a stored locator
# still resolves. Bump these deliberately, and re-run the suite when you do.
#
# Lower bounds with an upper guard rather than exact pins, so security patches
# still land but a major version cannot arrive unannounced.
PACKAGES=(
  "requests>=2.31,<3"
  "numpy>=1.26,<3"
  "pypdfium2>=4.25,<5"
  "pdfplumber>=0.11,<0.12"
  "pysbd>=0.3.4,<0.4"
  "Pillow>=10.2,<12"
  # Required by the deliverable builders and the PDF gates. Omitting these
  # fails in a confusing way rather than an obvious one: without matplotlib
  # the synthesis panel silently renders as nothing, and the contract gate
  # then reports a missing panel — pointing at the report instead of at the
  # absent dependency.
  "pypdf>=4.2,<7"
  "reportlab>=4.1,<5"
  "matplotlib>=3.8,<4"
)

# Biomni normally provides EasyOCR and PyTorch. Preserve that working runtime:
# only ask the package manager for EasyOCR (which declares PyTorch as a
# dependency) when either import is unavailable.
if python -c 'import easyocr, torch' >/dev/null 2>&1; then
  echo "[install] existing EasyOCR and PyTorch detected; skipping OCR package install."
else
  echo "[install] EasyOCR or PyTorch unavailable; installing the OCR runtime."
  PACKAGES+=("easyocr>=1.7,<2")
fi
[[ $WITH_MARKER -eq 1 ]] && PACKAGES+=("marker-pdf>=0.2,<2")

if command -v uv >/dev/null 2>&1; then
  uv pip install --system --quiet "${PACKAGES[@]}"
else
  python -m pip install --quiet "${PACKAGES[@]}"
fi

WITH_MARKER="$WITH_MARKER" python - <<'PY'
import importlib
import os

modules = ["requests", "numpy", "pypdfium2", "pdfplumber", "pysbd", "PIL",
           "pypdf", "reportlab", "matplotlib", "easyocr", "torch"]
if os.environ["WITH_MARKER"] == "1":
    modules.append("marker")
for module in modules:
    importlib.import_module(module)
print("install OK:", ", ".join(modules))

# Import success alone is insufficient: EasyOCR downloads model weights on the
# first Reader construction. Initialize now so a long background review cannot
# reach its figure pass and only then discover the model is unavailable.
import easyocr
easyocr.Reader(["en"], gpu=False, verbose=False)
print("EasyOCR English model ready")
PY

echo "[install] claim-first literature review runtime ready (no LangExtract)."
