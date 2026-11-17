Probe phase: we are measuring sensitivity. Change exactly ONE knob or ONE small mechanism starting from the official baseline (not from previous candidates), so that its effect is attributable. Prefer knobs that plausibly affect TNS across all circuits and are cheap to implement.

Probe menu (pick one not yet tried in the history; prefer items earlier in the list):
  P-L1  enable / strengthen timing-driven net weighting in the analytical placer (DREAMPlace: timing_opt_flag, net_weighting_scheme, max_net_weight, momentum_decay_factor, or the net-weight update rule)
  P-P2  make the partition objective penalize cutting timing-critical nets (edge weights from slack / net criticality instead of uniform weights)
  P-L3  lower target density or add placement padding so the fixed timing-repair pass has room for buffers
  P-P4  relax or tighten the area-balance tolerance of the partition
  P-L4  increase the pull between the two tier projections of a cross-tier net (cross-tier wirelength weight)
  P-P8  GNN training epochs / learning rate / embedding size (checks whether the partitioner is under-trained)
  P-L5  density weight initial value / growth rate; gamma of the weighted-average wirelength
  P-P5  area-balance repair order: flip low-degree / non-critical cells first instead of by embedding distance
If the code chunks shown do not contain the knob you want, name the symbols in KEYWORDS and propose the closest available change instead.
