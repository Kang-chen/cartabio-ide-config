# =============================================================================
# 04_trajectory_clustering.R
# Clinical score trajectory clustering (PASI or equivalent)
#
# Purpose:
#   Cluster patients by their longitudinal clinical score trajectory using
#   Dynamic Time Warping (DTW) distance + Ward.D2 hierarchical clustering.
#   Identifies the clinically critical "Flare→Clear" subtype (T2 worsening
#   followed by T3/1yr resolution) that would be misclassified as non-responder
#   under a standard 12-week endpoint.
#
# Method:
#   1. Compute pairwise DTW distance matrix (dtw package)
#   2. Hierarchical clustering with Ward.D2 linkage
#   3. Optimal k by average silhouette width (cluster package)
#   4. Label clusters by trajectory shape
#   5. Flag Flare→Clear patients (T2 > T1 AND T3/1yr < T1)
#
# Inputs:
#   score_table  - data.frame: patient_id, timepoint, score (PASI or equivalent)
#   params       - list of analysis parameters
#
# Outputs:
#   trajectory_clusters.csv  - patient-level cluster assignments
#   Returns:                 - list with $cluster_table, $hclust_obj, $dtw_dist
# =============================================================================

suppressPackageStartupMessages({
  library(dplyr)
  library(tidyr)
})

# -----------------------------------------------------------------------------
# Main function
# -----------------------------------------------------------------------------

run_trajectory_clustering <- function(
    score_table,
    params = list()
) {
  p <- modifyList(
    list(
      patient_col      = "patient_id",
      timepoint_col    = "timepoint",
      score_col        = "PASI",
      timepoints       = NULL,          # NULL = auto-detect from data
      k_range          = 2:7,           # range of k to evaluate
      k_fixed          = NULL,          # override silhouette; set k directly
      linkage          = "ward.D2",
      flare_ratio      = 1.2,           # T2/T1 ratio threshold for "flare"
      clear_ratio      = 0.5,           # T3/T1 ratio threshold for "clear"
      use_dtw          = TRUE,          # FALSE = Euclidean distance
      cluster_labels   = NULL           # named vector: cluster_id → label
    ),
    params
  )

  cat("=== PASI Trajectory Clustering ===\n")

  # --- Validate input ---
  required_cols <- c(p$patient_col, p$timepoint_col, p$score_col)
  missing_cols <- setdiff(required_cols, colnames(score_table))
  if (length(missing_cols) > 0) {
    stop(sprintf("Missing columns in score_table: %s",
                 paste(missing_cols, collapse = ", ")))
  }

  # --- Determine timepoints ---
  if (is.null(p$timepoints)) {
    p$timepoints <- sort(unique(score_table[[p$timepoint_col]]))
    cat(sprintf("Auto-detected timepoints: %s\n",
                paste(p$timepoints, collapse = ", ")))
  }

  # --- Pivot to wide format ---
  score_wide <- score_table %>%
    filter(.data[[p$timepoint_col]] %in% p$timepoints) %>%
    select(all_of(c(p$patient_col, p$timepoint_col, p$score_col))) %>%
    pivot_wider(
      names_from  = all_of(p$timepoint_col),
      values_from = all_of(p$score_col),
      values_fn   = mean   # average if duplicate entries
    )

  # Keep only patients with data at ≥2 timepoints
  tp_cols <- intersect(p$timepoints, colnames(score_wide))
  n_obs <- rowSums(!is.na(score_wide[, tp_cols, drop = FALSE]))
  score_wide <- score_wide[n_obs >= 2, ]

  cat(sprintf("Patients with ≥2 timepoints: %d\n", nrow(score_wide)))
  if (nrow(score_wide) < 4) {
    stop("Need at least 4 patients for clustering.")
  }

  # --- Build trajectory matrix ---
  traj_mat <- as.matrix(score_wide[, tp_cols, drop = FALSE])
  rownames(traj_mat) <- score_wide[[p$patient_col]]

  # Impute missing timepoints with linear interpolation per patient
  traj_mat <- t(apply(traj_mat, 1, function(x) {
    if (any(is.na(x)) && !all(is.na(x))) {
      x <- approx(seq_along(x), x, seq_along(x))$y
    }
    x
  }))

  # --- Compute distance matrix ---
  if (p$use_dtw && requireNamespace("dtw", quietly = TRUE)) {
    cat("Computing DTW distance matrix...\n")
    dist_mat <- dtw::dtwDist(traj_mat)
  } else {
    if (p$use_dtw) {
      cat("dtw package not available — falling back to Euclidean distance.\n")
      cat("Install with: install.packages('dtw')\n")
    }
    dist_mat <- dist(traj_mat, method = "euclidean")
  }

  # --- Hierarchical clustering ---
  hc <- hclust(as.dist(dist_mat), method = p$linkage)

  # --- Optimal k by silhouette ---
  if (!is.null(p$k_fixed)) {
    best_k <- p$k_fixed
    cat(sprintf("Using fixed k=%d\n", best_k))
  } else {
    sil_scores <- sapply(p$k_range, function(k) {
      if (k >= nrow(traj_mat)) return(NA_real_)
      cl <- cutree(hc, k = k)
      if (requireNamespace("cluster", quietly = TRUE)) {
        sil <- cluster::silhouette(cl, as.dist(dist_mat))
        mean(sil[, "sil_width"])
      } else {
        NA_real_
      }
    })
    names(sil_scores) <- p$k_range

    if (all(is.na(sil_scores))) {
      best_k <- 3
      cat("cluster package not available — defaulting to k=3.\n")
      cat("Install with: install.packages('cluster')\n")
    } else {
      best_k <- p$k_range[which.max(sil_scores)]
      cat(sprintf("Silhouette scores: %s\n",
                  paste(sprintf("k%d=%.3f", p$k_range, sil_scores), collapse = ", ")))
      cat(sprintf("Optimal k: %d (silhouette=%.3f)\n",
                  best_k, max(sil_scores, na.rm = TRUE)))
    }
  }

  # --- Cut tree ---
  cluster_ids <- cutree(hc, k = best_k)

  # --- Build cluster table ---
  cluster_table <- score_wide %>%
    mutate(cluster_id = cluster_ids[.data[[p$patient_col]]])

  # --- Characterise each cluster ---
  cluster_chars <- cluster_table %>%
    group_by(cluster_id) %>%
    summarise(
      n_patients    = n(),
      across(all_of(tp_cols), ~ mean(.x, na.rm = TRUE), .names = "mean_{.col}"),
      .groups = "drop"
    )

  # --- Auto-label clusters by trajectory shape ---
  cluster_table <- .label_clusters(
    cluster_table, cluster_chars, tp_cols,
    p$patient_col, p$flare_ratio, p$clear_ratio,
    p$cluster_labels
  )

  # --- Flag Flare→Clear patients ---
  cluster_table <- .flag_flare_clear(
    cluster_table, tp_cols, p$flare_ratio, p$clear_ratio
  )

  # --- Summary ---
  cat("\nCluster summary:\n")
  summary_tbl <- cluster_table %>%
    group_by(cluster_id, cluster_label) %>%
    summarise(n = n(), .groups = "drop") %>%
    arrange(cluster_id)
  print(summary_tbl, n = Inf)

  n_flare <- sum(cluster_table$flare_clear_flag, na.rm = TRUE)
  cat(sprintf("\nFlare→Clear patients: %d\n", n_flare))
  if (n_flare > 0) {
    fc_pts <- cluster_table[[p$patient_col]][cluster_table$flare_clear_flag]
    cat(sprintf("  Patient IDs: %s\n", paste(fc_pts, collapse = ", ")))
    cat("  CLINICAL NOTE: These patients show T2 worsening then resolution.\n")
    cat("  Do NOT discontinue treatment at 12 weeks based on T2 PASI alone.\n")
  }

  cat("\n✓ Trajectory clustering complete.\n")
  list(
    cluster_table = cluster_table,
    cluster_chars = cluster_chars,
    hclust_obj    = hc,
    dtw_dist      = dist_mat,
    best_k        = best_k,
    tp_cols       = tp_cols
  )
}


# -----------------------------------------------------------------------------
# Helper: auto-label clusters by trajectory shape
# -----------------------------------------------------------------------------

.label_clusters <- function(cluster_table, cluster_chars, tp_cols,
                             patient_col, flare_ratio, clear_ratio,
                             user_labels) {
  if (!is.null(user_labels)) {
    cluster_table$cluster_label <- user_labels[as.character(cluster_table$cluster_id)]
    return(cluster_table)
  }

  mean_cols <- paste0("mean_", tp_cols)
  labels <- sapply(cluster_chars$cluster_id, function(cid) {
    row <- cluster_chars[cluster_chars$cluster_id == cid, ]
    means <- as.numeric(row[, mean_cols])
    names(means) <- tp_cols

    first_tp <- means[1]
    last_tp  <- means[length(means)]

    # Need at least 3 timepoints for shape classification
    if (length(means) < 3) {
      return(if (last_tp < first_tp * clear_ratio) "Responder" else "Non-responder")
    }

    mid_tp <- means[2]

    flare  <- !is.na(mid_tp) && !is.na(first_tp) && mid_tp > first_tp * flare_ratio
    clear  <- !is.na(last_tp) && !is.na(first_tp) && last_tp < first_tp * clear_ratio
    improve <- !is.na(last_tp) && !is.na(first_tp) && last_tp < first_tp * 0.75

    if (flare && clear)   return("Flare_to_Clear")
    if (!flare && clear)  return("Stable_to_Clear")
    if (improve)          return("Late_Responder")
    if (last_tp > first_tp * 0.9) return("Non_responder")
    return("Partial_responder")
  })

  cluster_table$cluster_label <- labels[as.character(cluster_table$cluster_id)]
  cluster_table
}


# -----------------------------------------------------------------------------
# Helper: flag individual Flare→Clear patients
# -----------------------------------------------------------------------------

.flag_flare_clear <- function(cluster_table, tp_cols, flare_ratio, clear_ratio) {
  if (length(tp_cols) < 3) {
    cluster_table$flare_clear_flag <- FALSE
    return(cluster_table)
  }

  first_col <- tp_cols[1]
  mid_col   <- tp_cols[2]
  last_col  <- tp_cols[length(tp_cols)]

  cluster_table$flare_clear_flag <- with(cluster_table, {
    first <- .data[[first_col]]
    mid   <- .data[[mid_col]]
    last  <- .data[[last_col]]
    !is.na(first) & !is.na(mid) & !is.na(last) &
      (mid > first * flare_ratio) &
      (last < first * clear_ratio)
  })

  cluster_table
}
