utl::set_metrics_stage "placeopt__{}"
source $::env(SCRIPTS_DIR)/load.tcl
load_design 3_4_place_resized.odb 2_floorplan.sdc "task1 repair_timing"
estimate_parasitics -placement
set_ideal_network [all_clocks]
repair_timing -setup -repair_tns $::env(TNS_END_PERCENT)
estimate_parasitics -placement
source $::env(SCRIPTS_DIR)/report_metrics.tcl
report_metrics "task1 repair_timing" false false
write_db $::env(RESULTS_DIR)/3_4b_task1_repaired.odb
