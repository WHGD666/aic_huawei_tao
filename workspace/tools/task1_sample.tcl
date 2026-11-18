utl::set_metrics_stage "placeopt__{}"
source $::env(SCRIPTS_DIR)/load.tcl
load_design 3_4_place_resized.odb 2_floorplan.sdc "task1 sampled"
estimate_parasitics -placement
source $::env(SCRIPTS_DIR)/report_metrics.tcl
report_metrics "task1" false false
