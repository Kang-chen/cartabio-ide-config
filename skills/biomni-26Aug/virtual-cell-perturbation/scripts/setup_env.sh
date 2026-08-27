#!/usr/bin/env bash
# ============================================================================
# Virtual Cell Perturbation — environment bootstrap
# ----------------------------------------------------------------------------
# scGPT and GEARS are NOT pre-installed in Biomni. This recipe was validated on
# a GPU sandbox (NVIDIA A10G, CUDA 12.x). It is I/O-bound and takes ~7-8 min.
#
# Run on a Gpu sandbox created with a LARGE `timeout` (>=7200s predict / >=9000s
# train) because that param is the sandbox TOTAL LIFETIME, not a per-call limit.
#
# All software here is MIT-licensed (scGPT, GEARS) -> commercial-use safe.
# ============================================================================
set -euo pipefail

ENV_PREFIX="${ENV_PREFIX:-/workspace/scgpt_env}"
PY="$ENV_PREFIX/bin/python"

if [ -x "$PY" ] && "$PY" -c "import scgpt, gears, torch" 2>/dev/null; then
  echo "[setup] env already present at $ENV_PREFIX — skipping."
  "$PY" -c "import torch; print('[setup] torch', torch.__version__, 'cuda', torch.cuda.is_available())"
  exit 0
fi

echo "[setup] creating conda env at $ENV_PREFIX (python=3.10)..."
conda create -y -p "$ENV_PREFIX" python=3.10 >/tmp/env_create.log 2>&1

echo "[setup] installing torch 2.1.0 (cu121)..."
$PY -m pip install -q torch==2.1.0 torchvision==0.16.0 torchtext==0.16.0 \
    --index-url https://download.pytorch.org/whl/cu121

echo "[setup] installing torch-geometric stack..."
$PY -m pip install -q "numpy<2" torch-geometric==2.4.0
$PY -m pip install -q torch-scatter torch-sparse torch-cluster \
    -f https://data.pyg.org/whl/torch-2.1.0+cu121.html

echo "[setup] installing GEARS + scGPT (no-deps to avoid version churn)..."
$PY -m pip install -q "cell-gears==0.0.2" --no-deps
$PY -m pip install -q scgpt==0.2.4 --no-deps

echo "[setup] installing scientific deps..."
$PY -m pip install -q "scanpy==1.9.8" "anndata==0.10.8" "numba>=0.57" "leidenalg>=0.9" \
    "umap-learn>=0.5.3" scikit-misc "pandas<2.2" dcor "datasets>=2.3.0,<3.0.0" \
    ipython gdown huggingface_hub

echo "[setup] verifying imports..."
$PY - <<'PYEOF'
import torch, numpy, pandas
from gears import PertData
from scgpt.model import TransformerGenerator
print("[setup] OK: torch", torch.__version__, "| cuda", torch.cuda.is_available(),
      "| numpy", numpy.__version__, "| pandas", pandas.__version__)
PYEOF
echo "[setup] DONE."
