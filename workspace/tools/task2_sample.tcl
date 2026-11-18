utl::set_metrics_stage "globalroute__{}"
source $::env(SCRIPTS_DIR)/load.tcl
load_design 5_1_grt.odb 4_cts.sdc "task2 sampled"
if {[info exists ::env(IDEAL_CLOCK)]} {
  set_ideal_network [all_clocks]
  foreach name {rst_ni reset_i reset_l p_clk_async_reset_i} {
    set_false_path -through $name
  }
} else {
  set_propagated_clock [all_clocks]
}
estimate_parasitics -global_routing
source $::env(SCRIPTS_DIR)/report_metrics.tcl
report_metrics "task2" false false
