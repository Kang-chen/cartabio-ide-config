"""Pharmacovigilance adverse-event signal detection (FAERS / OpenFDA).

Modular pipeline:
  resolve_drugs            -> validate/resolve a drug set (explicit | class | target)
  query_faers              -> pull FAERS event + label counts from OpenFDA
  compute_disproportionality -> ROR / PRR / chi2 / CI / FDR + signal flags
  annotate_signals         -> label + literature grounding, SOC, expected/novel
  generate_figures         -> bar / volcano / forest / SOC+heatmap + summary panel
  generate_report          -> Phylo-branded PDF report
  run_analysis             -> end-to-end orchestrator
"""
