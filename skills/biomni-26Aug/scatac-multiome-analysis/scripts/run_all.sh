#!/usr/bin/env bash
# One-shot driver for the scATAC-seq end-to-end skill.
# Edit CONFIG below (or pass as $1), then run:  bash run_all.sh [config.yaml]
set -euo pipefail

CONFIG="${1:-$(dirname "$0")/config.yaml}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$CONFIG" ]; then
  echo "Config not found: $CONFIG"
  echo "Copy scripts/config.example.yaml to config.yaml and edit input paths first."
  exit 1
fi

echo "==> Using config: $CONFIG"
export CONFIG   # _common.R::load_config() also honors this; config is passed positionally too

for stage in 01_ingest_qc 02_lsi_cluster 03_recall_peaks 04_annotate 05_diff_access 06_figures_tables; do
  echo ""
  echo "==================== STAGE ${stage} ===================="
  Rscript "$HERE/${stage}.R" "$CONFIG"
done

echo ""
echo "==================== REPORT ===================="
python3 "$HERE/make_report.py" --config "$CONFIG"

echo ""
echo "==> Pipeline complete. See results_dir in $CONFIG."
