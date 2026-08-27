#!/usr/bin/env bash
# build_ld.sh -- Build a GRCh38 LD reference genotype subset (for signed r via ld_utils.py) for one
#                region from the NYGC 30x high-coverage 1000 Genomes panel (3202 samples, phased,
#                GRCh38), restricted to an ancestry superpopulation.
#
# This script ONLY produces the region/ancestry genotype subset (a PLINK2 pgen/pvar/psam). Compute the
# signed-r matrix (oriented to the GWAS effect allele) with ld_utils.py.
#
# Panel  : ftp.1000genomes.ebi.ac.uk .../1000G_2504_high_coverage/working/20220422_3202_phased_SNV_INDEL_SV/
#          1kGP_high_coverage_Illumina.chr{N}.filtered.SNV_INDEL_SV_phased_panel.vcf.gz  (verified live)
# Samples: .../1000G_2504_high_coverage/20130606_g1k_3202_samples_ped_population.txt (col7 = superpopulation;
#          EUR=633, AFR=893, EAS=585, SAS=601, AMR=490 -- EUR count matches the validated PROX1 run).
#
# Uses tabix to stream ONLY the region (never downloads the full multi-GB chromosome VCF). Requires
# tabix+bgzip (htslib) and plink2. bcftools is NOT required.
#
# Usage:
#   build_ld.sh --region 1:213400000-214600000 --superpop EUR --out-prefix /workspace/ld_region \
#       [--snps-only] [--maf 0.005] [--panel-url-tmpl URL_WITH_{CHR}] [--pop-file URL_OR_LOCAL] \
#       [--keep-file SAMPLES.txt]
#
# --region has NO 'chr' prefix (e.g. 1:213400000-214600000). --superpop one of EUR/AFR/EAS/SAS/AMR.
# Outputs (given --out-prefix P):
#   P.samples.txt   chosen sample IDs for the superpopulation (one per line)
#   P.region.vcf.gz region subset (biallelic SNVs by default) for those samples (+ .tbi)
#   P.pgen/.pvar/.psam  PLINK2 fileset (feed the prefix P to ld_utils.py --plink-prefix)
set -euo pipefail

REGION=""; SUPERPOP=""; OUTP=""; MAF="0.005"; SNPS_ONLY=1; KEEP_FILE=""
POP_FILE="https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/data_collections/1000G_2504_high_coverage/20130606_g1k_3202_samples_ped_population.txt"
PANEL_TMPL="https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/data_collections/1000G_2504_high_coverage/working/20220422_3202_phased_SNV_INDEL_SV/1kGP_high_coverage_Illumina.chr{CHR}.filtered.SNV_INDEL_SV_phased_panel.vcf.gz"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) REGION="$2"; shift 2;;
    --superpop) SUPERPOP="$2"; shift 2;;
    --out-prefix) OUTP="$2"; shift 2;;
    --maf) MAF="$2"; shift 2;;
    --snps-only) SNPS_ONLY=1; shift;;
    --allow-indels) SNPS_ONLY=0; shift;;
    --keep-file) KEEP_FILE="$2"; shift 2;;
    --pop-file) POP_FILE="$2"; shift 2;;
    --panel-url-tmpl) PANEL_TMPL="$2"; shift 2;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done
[[ -z "$REGION" || -z "$OUTP" ]] && { echo "ERROR: --region and --out-prefix required" >&2; exit 2; }
[[ -z "$SUPERPOP" && -z "$KEEP_FILE" ]] && { echo "ERROR: --superpop or --keep-file required" >&2; exit 2; }
command -v tabix >/dev/null || { echo "ERROR: tabix (htslib) not found" >&2; exit 2; }
command -v bgzip >/dev/null || { echo "ERROR: bgzip (htslib) not found" >&2; exit 2; }
command -v plink2 >/dev/null || { echo "ERROR: plink2 not found" >&2; exit 2; }

CHR_RAW="${REGION%%:*}"
CHR_NO=$(echo "$CHR_RAW" | sed 's/^chr//I')
CHR_TABIX="chr${CHR_NO}"          # NYGC panel uses chr-prefixed contig names
SPAN="${REGION#*:}"
VCF_URL="${PANEL_TMPL/\{CHR\}/$CHR_NO}"
echo "[build_ld] region=$REGION superpop=${SUPERPOP:-<keep-file>} panel=$(basename "$VCF_URL")" >&2

mkdir -p "$(dirname "$OUTP")"

# ---- 1. sample list for the superpopulation ----
if [[ -n "$KEEP_FILE" ]]; then
  cp "$KEEP_FILE" "${OUTP}.samples.txt"
else
  POP_LOCAL="${OUTP}.pop.txt"
  if [[ -f "$POP_FILE" ]]; then cp "$POP_FILE" "$POP_LOCAL"; else curl -fsSL --max-time 120 "$POP_FILE" -o "$POP_LOCAL"; fi
  # col2 = SampleID, col7 = Superpopulation
  awk -v sp="$SUPERPOP" 'NR>1 && $7==sp {print $2}' "$POP_LOCAL" > "${OUTP}.samples.txt"
fi
NS=$(wc -l < "${OUTP}.samples.txt" | tr -d ' ')
[[ "$NS" -lt 1 ]] && { echo "ERROR: no samples for superpop=$SUPERPOP" >&2; exit 3; }
echo "[build_ld] ${NS} samples in superpopulation" >&2

# ---- 2. stream region subset with tabix (header + region records only) ----
RAW_VCF="${OUTP}.region.raw.vcf.gz"
{ tabix -H "$VCF_URL" 2>/dev/null || { echo "ERROR: tabix header fetch failed for $VCF_URL" >&2; exit 3; }; \
  tabix "$VCF_URL" "${CHR_TABIX}:${SPAN}" 2>/dev/null || { echo "ERROR: tabix region fetch failed" >&2; exit 3; }; } \
  | bgzip -c > "$RAW_VCF"
tabix -f -p vcf "$RAW_VCF"
NV=$(zcat "$RAW_VCF" | grep -vc '^#' || true)
echo "[build_ld] ${NV} panel records in region (pre-filter)" >&2
[[ "$NV" -lt 1 ]] && { echo "ERROR: region empty in panel (check chr naming / coords)" >&2; exit 3; }

# ---- 3. subset samples + variant filters -> PLINK2 pgen ----
SNVFLAG=""
[[ "$SNPS_ONLY" -eq 1 ]] && SNVFLAG="--snps-only just-acgt"
# plink2 VCF import assigns FID via --const-fid (0 for everyone); build a matching 'FID IID' keep file.
awk '{print "0\t"$1}' "${OUTP}.samples.txt" > "${OUTP}.keep.plink.txt"

plink2 --vcf "$RAW_VCF" \
  --const-fid 0 \
  --keep "${OUTP}.keep.plink.txt" \
  --max-alleles 2 --min-alleles 2 $SNVFLAG \
  --maf "$MAF" \
  --set-all-var-ids '@:#:$r:$a' --new-id-max-allele-len 100 \
  --rm-dup exclude-all \
  --make-pgen --out "$OUTP" >&2 2> "${OUTP}.plink.log" || {
    echo "ERROR: plink2 subset failed; see ${OUTP}.plink.log" >&2
    tail -20 "${OUTP}.plink.log" >&2 || true
    exit 3;
  }

NOUT=$(wc -l < "${OUTP}.pvar" 2>/dev/null || echo 0)
echo "\u2713 LD panel built: ${NS} ${SUPERPOP:-custom} samples, ~$((NOUT>0?NOUT-1:0)) variants in ${REGION}."
echo "  prefix: ${OUTP}  ->  feed to: ld_utils.py --plink-prefix ${OUTP}"
