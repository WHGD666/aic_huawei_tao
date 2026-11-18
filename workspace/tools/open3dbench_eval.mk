# Local diagnostic stages, loaded after the upstream Open3DBench Makefile.
# The upstream Makefile owns the design/platform environment; do not run its
# OpenROAD Tcl entry points directly from an unconfigured shell.
HARNESS_TOOLS_DIR := $(dir $(lastword $(MAKEFILE_LIST)))

.PHONY: harness-prefix harness-task1 harness-task1-repair-timing harness-task2
harness-prefix:
	mkdir -p $(RESULTS_DIR) $(LOG_DIR) $(REPORTS_DIR)
	cp $(SDC_FILE) $(RESULTS_DIR)/1_synth.sdc
	$(OPENROAD_EXE) -exit scripts_3D/read_def.tcl
	python3 scripts_3D/save_map_lol.py
	python3 scripts_3D/split_cts_def_lol.py
	$(OPENROAD_EXE) -exit scripts_3D/lg_bottom.tcl
	$(OPENROAD_EXE) -exit scripts_3D/lg_upper.tcl
	python3 scripts_3D/read_def_store_coord.py
	$(OPENROAD_EXE) -exit scripts_3D/load_cts.tcl

harness-task1:
	cp $(RESULTS_DIR)/my_cts.odb $(RESULTS_DIR)/2_5_floorplan_tapcell.odb
	$(UNSET_AND_MAKE) do-2_6_floorplan_pdn do-2_floorplan
	cp $(RESULTS_DIR)/1_synth.sdc $(RESULTS_DIR)/2_floorplan.sdc
	cp $(RESULTS_DIR)/2_floorplan.odb $(RESULTS_DIR)/3_3_place_gp.odb
	$(UNSET_AND_MAKE) do-3_4_place_resized
	$(OPENROAD_EXE) -exit $(HARNESS_TOOLS_DIR)/task1_sample.tcl -metrics $(LOG_DIR)/3_4_sampled.json

harness-task1-repair-timing: harness-task1
	$(OPENROAD_EXE) -exit $(HARNESS_TOOLS_DIR)/task1_repair_timing.tcl -metrics $(LOG_DIR)/3_4b_task1_repaired.json

harness-task2:
	cp $(RESULTS_DIR)/my_cts.odb $(RESULTS_DIR)/2_5_floorplan_tapcell.odb
	$(UNSET_AND_MAKE) do-2_6_floorplan_pdn do-2_floorplan
	cp $(RESULTS_DIR)/1_synth.sdc $(RESULTS_DIR)/2_floorplan.sdc
	cp $(RESULTS_DIR)/2_floorplan.odb $(RESULTS_DIR)/3_3_place_gp.odb
	$(UNSET_AND_MAKE) do-3_4_place_resized do-3_5_place_dp do-3_place do-3_place.sdc do-cts do-5_1_grt
	$(OPENROAD_EXE) -exit $(HARNESS_TOOLS_DIR)/task2_sample.tcl -metrics $(LOG_DIR)/5_1_sampled.json
