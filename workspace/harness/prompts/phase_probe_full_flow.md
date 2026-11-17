Probe phase (full flow): we are measuring sensitivity across SIX stages, scored after global routing with propagated clocks and a hard overflow budget (total overflow must stay <= 300). Change exactly ONE knob or ONE small mechanism from the official baseline so its effect is attributable. Spread probes across stages - do not spend every probe on the partitioner.

Probe menu (pick one not yet tried in the history; the code chunks shown this round were retrieved for the stage listed first):
  T1  timing repair: -repair_tns percentage / -setup_margin / TNS_END_PERCENT / move sequence (-sequence) / -skip_* flags in cts.tcl or resize.tcl
  C1  CTS: -sink_clustering_size / -sink_clustering_max_diameter / -balance_levels / -distance_between_buffers / CTS_BUF_CELL in cts.tcl or config
  G1  global routing: -congestion_iterations / -critical_nets_percentage / set_global_routing_layer_adjustment / routing layer range in global_route.tcl or fastroute.tcl
  L1  placement: timing-driven net weighting (timing_opt_flag, net_weighting_scheme, max_net_weight, momentum_decay_factor)
  P2  partition: criticality-weighted cut cost / area-balance repair order
  D1  DRV repair: repair_design -max_wire_length / slew & cap margins; DONT_USE_CELLS (e.g. allowing upper-tier buffers)
  H1  legalization / HBT: detailed_placement -max_displacement; HBT assignment order or pitch handling
Tcl/Makefile edits must keep exact Tcl syntax (balanced braces, `\` line continuations) and Makefile `export VAR = value` form. If a per-design config file is edited, the identical edit must be applied to EVERY design's config (the gate enforces this) - prefer editing shared scripts.
