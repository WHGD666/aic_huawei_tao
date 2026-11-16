# Open3DBench 真实结构解读（来自官方仓库源码）

> 来源：`https://github.com/lamda-bbo/Open3DBench`（v1.1，commit 见 `third_party/open3dbench_ref/SOURCE_COMMIT.txt`），2026-11-17 读取。关键文件已摘录到 `third_party/open3dbench_ref/`。
> 赛题说"官方评测平台基于 Open3DBench 和 OpenROAD 开发"——组委会的容器大概率是这套东西加上一个 GNN 切分基线和评估接口。本文回答"我们到底要改什么、评估器在算什么"。**拿到官方容器后以容器为准，差异记到 §7。**

## 1. 两层芯片是怎么在 OpenROAD 里建模的（决定一切）

**一个设计、一套 tech LEF、20 层金属。** `platforms/nangate45_3D` 把 NanGate45 的 metal1–10 复制成 metal11–20，中间用一个 CUT 层 `hb_layer` 连接：

```
poly, active, metal1 … metal10, hb_layer(CUT), metal11 … metal20
   ↑ 下层（bottom die）                ↑ 上层（upper die，镜像：metal19/20 相当于 metal1/2）
```

- 标准单元库复制两份：`XXX_bottom`（引脚在 metal1）与 `XXX_upper`（引脚在 **metal19**）。**切分 = 决定每个实例用 `_bottom` 还是 `_upper` 版本的 cell master**。
- HBT 是两个 1µm×1µm 的 `CLASS CORE` 宏：`HBT_BOTIN`（BOT 引脚 metal10 输入 → TOP 引脚 metal11 输出）和 `HBT_TOPIN`（反向）。LoL 流程把 HBT 当**虚拟缓冲器**插在跨层网里，`MUSTJOIN` 让两个引脚电气连通。
- 跨层网在 DEF 里就是一条普通的 net，经过 HBT 实例；STA 天然连通，不需要评估器拼接。题面"连通的双层时序视图"就是指这个。
- `fastroute.tcl` 为 metal1–20 都做了 tracks（hbt 层的 make_tracks 被注释掉）；`MAX_ROUTING_LAYER = metal20`。所以 **GRT 的溢出表会列出 20 层**，"两层溢出之和"= 表格 `Total` 行（它本来就是全部层的和）。
- 单位：LEF `DATABASE MICRONS 2000`（待从容器 LEF 确认）；Liberty 时间单位 ns；行高 1.4µm；site `FreePDK45_38x28_10R_NP_162NW_34O`。
- PDN：`grid_strategy-M1-M4-M7.tcl`（下层）；上层 PDN 如何处理待看容器（可能镜像到 M14/M17/M20）。
- 默认 `PLACE_DENSITY = 0.30`、`CELL_PAD_* = 0`、`DONT_USE_CELLS` 禁掉了所有 `_upper` 的 BUF/CLKBUF —— **意味着 rsz 插的缓冲器全在下层**；上层关键路径要修只能靠 resize。这是任务一的一个隐藏杠杆：上层放的单元越关键，rsz 越修不动。

## 2. 评估流程（LoL，`make do-lolflow`）逐步对照题面

| 题面环节 | Open3DBench 实际步骤 | 脚本 | 说明 |
|---|---|---|---|
| 切分 + 布局 | **外部**：Place-LoL 跑 ICCAD 2022/2023 contest placer（或 tcad25），`convert_output.sh` 把结果转成一个合并 DEF：`binaries/converted_output/<variant>/<method>/<design>.def` | `Place-LoL/dreamplace/convert_output.py` | 合并 DEF 里每个 COMPONENT 的 master 已是 `_bottom`/`_upper`，HBT 实例已存在并 PLACED |
| 读入 | `read_def` 合并 DEF → `3D_placement.odb`，写 `4_1_cts.def` | `scripts_3D/read_def.tcl` | |
| 记录层归属 | 扫 DEF 的 COMPONENTS，按 master 名含 bottom/upper 记 `name_die_map.pkl` | `save_map_lol.py` | |
| 合法化 | 把合并 DEF 按层拆成 `upper.def` / `bottom.def`（HBT 不拆，TAPCELL 去掉），各自 `detailed_placement -max_displacement 300`，再把坐标写回合并 DEF | `split_cts_def_lol.py` → `lg_bottom.tcl` / `lg_upper.tcl` → `read_def_store_coord.py` | **合法化是分层做的**，displacement 上限 300（单位 site/row，待确认） |
| 载入 | `load_cts.tcl` → `my_cts.odb` → 复制为 `4_cts.odb`（**跳过了 CTS！**） | | LoL 流程 `IDEAL_CLOCK=1`，没有 CTS、没有 repair_timing；题面任务二要求 CTS，说明组委会在此基础上加了 |
| 供电网络 | 无（do-lolflow 里没有 pdn 步骤；`do-def_eval` 路径有 `do-2_6_floorplan_pdn`） | `scripts/pdn.tcl` | 题面说 PDN 固定，组委会流程里应该有 |
| 全局布线 | `global_route -allow_congestion -verbose -congestion_iterations 5`（design config 覆盖默认），`set_global_routing_layer_adjustment metal2-metal20 0.5` 被 `FASTROUTE_TCL` 覆盖为**无 adjustment**；之后 `estimate_parasitics -global_routing`；`IDEAL_CLOCK` → `set_ideal_network` + 几条 reset false path | `scripts/global_route.tcl` | 指标 `globalroute__timing__*` 写入 `5_1_grt.json` |
| 详细布线 | `do-5_2_route`（`-droute_end_iter 5`） | | 题面说全流程**不要求详细布线**，组委会应该砍掉了 |
| 填充 + 报告 | `do-6_1_fill` → `final_report.tcl`：RCX 抽取（`nangate45_3D.rules`）→ `report_metrics "finish"` → `6_report.json` | | Open3DBench 论文指标用 `finish__timing__setup__ws/tns`；**赛题任务二采样点是 GRT 后**，对应 `5_1_grt.json` 的 `globalroute__timing__setup__ws/tns`（待确认 key 名） |
| 温度 | `do-hotspot` | | 赛题不用 |

MoL 流程（`do-def_eval` / `do-pre_cts` / `do-cts_eval`）更接近题面描述：`init_odb → pdn → 3_4_place_resized (repair_design) → 3_5_place_dp → cts → route → finish`。**题面任务一的"切分→布局与合法化→PDN→HBT→时序优化→STA"顺序与 `do-def_eval` 的前半段吻合**，组委会很可能是把 LoL 的分层合法化 + MoL 的 PDN/resize 管线拼起来。

关键差异 **（必须向组委会确认）**：
1. 任务一的"一次时序优化"是 `repair_design`（resize.tcl 只有 repair_design + buffer_ports + repair_tie_fanout，**没有 repair_timing**）还是 `repair_timing -setup`？两者差别巨大：repair_design 只修 DRV（slew/cap/fanout/长线），不直接修 setup。
2. 采样用的 json key：`placeopt__timing__setup__ws/tns`（resize 阶段）还是别的。
3. 容器里 `DONT_USE_CELLS` 是否仍然禁掉所有 upper 缓冲器。

## 3. 切分 + 布局的"输入/输出契约"

**输入**（Place-LoL `test/3D_input_default/<design>.json`）：2D 的 LEF/LIB + `benchmarks/3D_input/<design>/2_2_floorplan_io.def`（floorplan 后、带 IO 的 DEF）+ `<design>_3D.v` 网表。DREAMPlace 参数：`target_density 0.4`、`density_weight 8e-5`、`gamma 4.0`、`random_seed 1000`、`iteration 1000`、`wirelength weighted_average`、`optimizer nesterov`、`partition_params {type:2, method:min_cut, iterations:100, lam:0.01}`、`macro_place_flag 1`、`legalize_flag 0`、`detailed_place_flag 0`。

**输出契约**：一个合并 DEF，要求
- 每个实例 master 为 `<cell>_bottom` 或 `<cell>_upper`（宏同理 `fakeram45_*.bottom/.upper`）
- 跨层网含 HBT 实例（`HBT_BOTIN`/`HBT_TOPIN`），HBT 已 PLACED，位置在 1µm 网格、互不重叠、间距 ≥ 1µm（`convert_input.sh ... 100` → terminal size 1µm, spacing 1µm）
- 两层各自合法（评估器会再跑 `detailed_placement -max_displacement 300`，但位移太大会失败 → 候选无效）

赛题的"切分方案"= `name_die_map`（实例→0/1），"上下层布局"= `bottom_legalized.def` / `upper_legalized.def`。

**GNN 切分基线从哪来**：Open3DBench 的 `Place-LoL/dreamplace/Partitioner.py` 是 min-cut + 模拟退火（宏级）；`Place-MoL/src/partition/GNN/GNN_partition.py` 是 GIN 对比学习 + KMeans + 面积平衡修复（也是宏级，`graph_builder` 把所有标准单元聚成一个"cell node"）。**题面说基线是 TP-GNN 思路的单元级 GNN 切分**——与仓库里的都不同，是组委会新写的。但 MoL 的 GNN 代码结构（`GINNetwork` 编码 → 对比损失 → KMeans 二分 → `_refine_partition_by_embedding_distance` 面积修复）很可能是基线的骨架，`docs/knob_catalog.md` 的 P1/P2/P5 直接对应其 `_build_node_features`（现在只有 area/width/height/num_connected/num_pins）、`contrastive_loss`（边权被忽略：注释写明 "weights w are ignored (set to 1)"）、`_refine_partition_by_embedding_distance`（按嵌入距离翻转，与时序无关）。

**DREAMPlace 时序驱动已内置**：`ops/timing/`（OpenTimer 后端）+ `timing_opt_flag` + `net_weighting_scheme adam|lilith` + `max_net_weight` + `momentum_decay_factor`。基线 json 里 `timing_opt_flag` 没开。打开它需要 `lib_input`/`sdc_input`/`verilog_input`（json 里有 lib 和 verilog，SDC 要加）。**这是任务一最便宜的高价值探针：L1 其实已经实现好了，只是没启用。**

## 4. 对我们 harness 的直接影响

| 之前的假设 | 真实情况 | 改动 |
|---|---|---|
| 两个独立设计 / 评估器拼时序 | 一个设计 20 层金属，HBT 是 CORE 宏 | `openroad_parse` 的跨层判定：路径经过 `HBT_BOTIN`/`HBT_TOPIN` 实例或 net 名含 hbt；GRT 表 Total 行直接就是两层之和 |
| 白名单路径猜测 | 切分/布局在 Python（DREAMPlace fork + Partitioner），流程在 Tcl/Makefile | `whitelist_*.yaml` 待填真实路径；任务二的 CTS/GRT 旋钮大多是 **Makefile 环境变量**（`CTS_CLUSTER_SIZE`、`CTS_BUF_DISTANCE`、`GLOBAL_ROUTE_ARGS`、`TNS_END_PERCENT`、`SETUP_SLACK_MARGIN`、`PLACE_DENSITY`、`CELL_PAD_*`、`DONT_USE_CELLS`）→ 候选可能是改 `config.mk` 而不是 Tcl |
| 指标从 report_wns 文本抓 | ORFS 风格：每阶段写 `<stage>.json`，key 形如 `globalroute__timing__setup__ws`、`finish__timing__setup__tns` | `LogDirEvaluator` 加 json 优先、文本兜底（已做文本；json 待加） |
| 溢出要自己算两层相加 | `Total` 行 | 已匹配 |
| rsz 日志格式 | 见 `openroad_parse.py` 注释（已按 OpenROAD 源码校正） | 已改 |

## 5. 容器与数据

- 评估镜像 `shiyunqi/open3dbench:eval`（OpenROAD + HotSpot），布局镜像 `shiyunqi/open3dbench:place`（DREAMPlace/CUDA）。**两个镜像**。组委会可能合成一个。
- 基准数据不在 git 里：`benchmarks_lol.tar.gz`、`binaries.tar.gz`（Google Drive）。组委会会提供 bp_fe/bp/ariane133/swerv_wrapper 的 ng45 版本；**asap7 版本仓库里没有**，是组委会新做的——`bp_fe-asap7` 的 3D 平台（层名 M1–M9 + 镜像、HBT 宏尺寸、site）我们完全不知道，这正是"跨工艺"考点。所有层名/单位/site 必须从 LEF 读。
- 本机能跑的最小实验：`docker pull shiyunqi/open3dbench:eval` + 仓库自带的 `converted_output` DEF（如果 Google Drive 包能下载）→ `./run_lol_iccad2023.sh default tcad25` 跑一个 bp_fe → 拿到真实日志格式与耗时。

## 6. 下一步（替换 PLAN §8 的部分待确认项）

1. `docker pull shiyunqi/open3dbench:eval`（先迁 Docker 磁盘到 F 盘），本机跑一次 bp_fe LoL 评估，记录每阶段耗时与 `5_1_grt.json` / `6_report.json` 的 key
2. 等官方容器：对照本文 §2 的差异表逐条确认，重点是"一次时序优化"到底是什么
3. 白名单改成真实路径；`knob_catalog` 补 Makefile 变量类旋钮

## 7. 与官方容器的差异（拿到后填）

| 本文条目 | 官方容器实际 | 日期 |
|---|---|---|
| | | |
