#!/usr/bin/env bash
# ============================================================
# Antibody humanization/liability skill - environment setup
# ============================================================
# These packages are NOT part of the default Biomni image and MUST be
# installed before any of the pipeline scripts will run:
#   - ANARCI      (antibody numbering; pulls HMMER)
#   - abnumber    (Chain object / Kabat-Chothia-IMGT-Martin numbering; wraps ANARCI)
#   - pyteomics   (electrochem: pI / net charge)
#   - hmmer       (ANARCI dependency; provides hmmscan)
# Preinstalled and reused as-is: biopython, pandas, numpy, matplotlib,
#   seaborn, reportlab, pypdf, requests.
#
# NetMHCIIpan is a licensed, email-gated academic download (DTU Health Tech)
# and is NEVER auto-installed here. If a customer has a local install, point
# the immunogenicity step at it via env var NETMHCIIPAN_BIN (see below).
#
# Usage:  bash 00_setup_env.sh
set -euo pipefail

echo "[setup] Installing antibody numbering + biophysics stack..."
# conda-forge/bioconda provide the most reliable ANARCI+HMMER pairing, but
# a pure-pip path works too. Try conda first, fall back to pip.
if command -v mamba >/dev/null 2>&1 || command -v conda >/dev/null 2>&1; then
    CONDA=$(command -v mamba || command -v conda)
    echo "[setup] Using $CONDA for hmmer + anarci"
    $CONDA install -y -c bioconda -c conda-forge hmmer anarci abnumber || true
fi

# Ensure Python deps regardless (uv is the required installer on Biomni).
uv pip install --quiet abnumber pyteomics biopython pandas numpy matplotlib seaborn reportlab pypdf requests adjustText || \
  pip install --quiet abnumber pyteomics biopython pandas numpy matplotlib seaborn reportlab pypdf requests adjustText

# ANARCI via pip if conda path was unavailable
python -c "import anarci" 2>/dev/null || uv pip install --quiet anarci || pip install --quiet anarci || {
    echo "[setup][WARN] ANARCI not importable via pip. Install from bioconda:"
    echo "              conda install -c bioconda anarci hmmer"
}

echo "[setup] Verifying imports..."
python - <<'PY'
import importlib, sys
ok = True
for m in ["anarci", "abnumber", "pyteomics.electrochem", "Bio", "pandas", "numpy",
          "matplotlib", "seaborn", "reportlab", "pypdf", "requests"]:
    try:
        importlib.import_module(m)
        print(f"  [ok] {m}")
    except Exception as e:
        ok = False
        print(f"  [MISSING] {m}: {e}")
# HMMER binary (ANARCI backend)
import shutil
print(f"  hmmscan on PATH: {bool(shutil.which('hmmscan'))}")
sys.exit(0 if ok else 1)
PY

# ---- Optional local NetMHCIIpan detection (never auto-downloaded) ----
echo "[setup] Checking for optional local NetMHCIIpan..."
if [[ -n "${NETMHCIIPAN_BIN:-}" ]] && [[ -x "${NETMHCIIPAN_BIN}" ]]; then
    echo "  [ok] NETMHCIIPAN_BIN=${NETMHCIIPAN_BIN} (local predictor will be preferred)"
elif command -v netMHCIIpan >/dev/null 2>&1; then
    echo "  [ok] netMHCIIpan found on PATH (local predictor will be preferred)"
else
    echo "  [info] No local NetMHCIIpan. Immunogenicity step will try the IEDB web API,"
    echo "         and if network egress is blocked it will mark that section UNAVAILABLE."
    echo "         To use a local install: export NETMHCIIPAN_BIN=/path/to/netMHCIIpan"
fi
echo "[setup] Done."
