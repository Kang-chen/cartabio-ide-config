#!/usr/bin/env Rscript
# camera_endpoint.R -- Stage 5 (competitive gene-set test) of
# signature-response-enrichment.
#
# CAMERA (Wu & Smyth) competitive gene-set test at the treatment ENDPOINT, contrasting
# non-responders vs responders. CAMERA accounts for inter-gene correlation, which makes
# it a stringent complement to the change-from-baseline dGSVA test.
#
# CAVEAT to state in the report: this is the ABSOLUTE on-treatment contrast, so signal is
# partly CONFOUNDED with residual disease activity (non-responders are sicker at endpoint).
# Present it as supportive, not as the sole headline.
#
# AUTO-SKIP: if a cohort has <2 samples per group at the endpoint, or a set maps <2 genes,
# skip gracefully and record why.
#
# INPUT:
#   --expr        endpoint expression matrix CSV (genes x samples), log-scale.
#                 (Provide only endpoint-timepoint samples, or pass full matrix + --meta.)
#   --meta        sample meta CSV: sample_id, timepoint, patient_id
#   --response    response CSV: patient_id, response_group (R/NR)
#   --signatures  JSON {"SET": [genes...]} (context sets may be appended by the caller)
#   --endpoint-tp endpoint timepoint value (e.g. "12")
#   --out         CAMERA results CSV (gene_set, NGenes, Direction, PValue, FDR)
#
# USAGE:
#   Rscript camera_endpoint.R --expr expr.csv --meta meta.csv --response resp.csv \
#       --signatures sigs.json --endpoint-tp 12 --out camera_endpoint.csv

suppressWarnings(suppressMessages({
  library(optparse); library(jsonlite); library(limma)
}))

opt <- parse_args(OptionParser(option_list = list(
  make_option("--expr", type = "character"),
  make_option("--meta", type = "character"),
  make_option("--response", type = "character"),
  make_option("--signatures", type = "character"),
  make_option("--endpoint-tp", type = "character", dest = "endpoint_tp"),
  make_option("--out", type = "character")
)))

expr <- as.matrix(read.csv(opt$expr, row.names = 1, check.names = FALSE))
mode(expr) <- "numeric"
meta <- read.csv(opt$meta, stringsAsFactors = FALSE)
resp <- read.csv(opt$response, stringsAsFactors = FALSE)

# endpoint samples only
end_samp <- meta$sample_id[as.character(meta$timepoint) == as.character(opt$endpoint_tp)]
end_samp <- intersect(end_samp, colnames(expr))
m <- meta[meta$sample_id %in% end_samp, ]
m <- merge(m, resp[, c("patient_id", "response_group")], by = "patient_id")
m <- m[match(end_samp, m$sample_id), ]
grp <- factor(m$response_group, levels = c("R", "NR"))  # NR is the tested-up level
E <- expr[, end_samp, drop = FALSE]

if (sum(grp == "NR") < 2 || sum(grp == "R") < 2) {
  message("[camera] SKIP: <2 samples per group at endpoint. ",
          sprintf("(NR=%d, R=%d)", sum(grp == "NR"), sum(grp == "R")))
  write.csv(data.frame(gene_set = character(0), NGenes = integer(0),
                       Direction = character(0), PValue = numeric(0), FDR = numeric(0)),
            opt$out, row.names = FALSE)
  quit(status = 0)
}

design <- model.matrix(~grp)  # grpNR coefficient = NR vs R
feat <- toupper(rownames(E))
sigs <- fromJSON(opt$signatures, simplifyVector = FALSE)
idx <- limma::ids2indices(lapply(sigs, function(g) toupper(unlist(g))), feat)
idx <- idx[vapply(idx, length, 0L) >= 2]                # need >=2 mapped genes
if (length(idx) == 0) {
  message("[camera] SKIP: no set maps >=2 genes.")
  write.csv(data.frame(gene_set = character(0)), opt$out, row.names = FALSE); quit(status = 0)
}

res <- limma::camera(E, idx, design, contrast = 2)      # coef 2 = grpNR
res$gene_set <- rownames(res)
res$FDR <- p.adjust(res$PValue, method = "BH")
out <- res[, c("gene_set", "NGenes", "Direction", "PValue", "FDR")]
write.csv(out, opt$out, row.names = FALSE)
message(sprintf("[camera] endpoint NR-vs-R, %d sets tested -> %s", nrow(out), opt$out))
message("[camera] NOTE: absolute endpoint contrast is partly confounded by residual ",
        "disease activity; report as supportive.")
