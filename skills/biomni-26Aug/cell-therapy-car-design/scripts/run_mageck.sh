#!/usr/bin/env bash
# MAGeCK count + test (primary NTC-normalized + median-norm sensitivity) for a
# CFSE proliferation screen. Treatment = dividing (CFSE-low), control = non-dividing.
# See references/screen_reanalysis.md. Requires MAGeCK (tested v0.5.9.5).
#
# Usage: edit the paths/labels below, then:  bash run_mageck.sh
set -euo pipefail

# --- Config (edit these) ---
LIB=/workspace/screen/mageck/pilot_library.csv        # sgRNA,sequence,gene (NO header)
NTC=/workspace/screen/mageck/ntc_guides.txt           # one NTC sgRNA id per line
PROC=/workspace/screen/fastq_proc                     # RC-corrected bare-20nt-spacer FASTQ
OUT=/workspace/screen/mageck
# sample order below MUST match the --sample-label order
FQ=("$PROC/D1_Div.fastq.gz" "$PROC/D1_NonDiv.fastq.gz" "$PROC/D2_Div.fastq.gz" "$PROC/D2_NonDiv.fastq.gz")
LABELS="D1_Div,D1_NonDiv,D2_Div,D2_NonDiv"
TREAT="D1_Div,D2_Div"       # CFSE-low = dividing = treatment
CTRL="D1_NonDiv,D2_NonDiv"  # CFSE-high = non-dividing = control

cd "$OUT"

# --- Count (auto-detect 20nt guide length; do NOT set --trim-5/--sgrna-len for bare spacers) ---
mageck count \
  --list-seq "$LIB" \
  --fastq "${FQ[@]}" \
  --sample-label "$LABELS" \
  --output-prefix pilot

# --- Test: primary (NTC normalization + null model, per STAR Methods) ---
mageck test \
  --count-table pilot.count.txt \
  --treatment-id "$TREAT" \
  --control-id "$CTRL" \
  --control-sgrna "$NTC" \
  --norm-method control \
  --output-prefix pilot_div_vs_nondiv

# --- Test: sensitivity (median-ratio normalization) ---
mageck test \
  --count-table pilot.count.txt \
  --treatment-id "$TREAT" \
  --control-id "$CTRL" \
  --norm-method median \
  --output-prefix pilot_div_vs_nondiv_medianNorm

echo "Done. Key outputs:"
echo "  pilot.countsummary.txt              (QC: mapping %, zero-counts, Gini)"
echo "  pilot_div_vs_nondiv.gene_summary.txt (hits: pos|=brakes, neg|=essential)"
echo "  pilot_div_vs_nondiv.sgrna_summary.txt"
echo "Validate: CBLB/CD5/PTEN should be top pos|rank; CD3D/LCP2/ITK top neg|rank."
