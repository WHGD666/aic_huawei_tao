# OpenROAD 流程参考（面向赛题 1 六环节）

> **可信度说明**：我的沙箱访问不了 openroad.readthedocs.io（域名被拦），本文是按我对 OpenROAD / OpenSTA 的既有知识整理的，命令名与参数**以官方文档为准**。每节末尾给了需要你在本机核对的页面。拿到官方容器后，用 `openroad -help` 和容器里的 flow 脚本逐条校正，把差异记到本文"勘误"一节。
>
> 文档站结构（供查阅）：`https://openroad.readthedocs.io/en/latest/` → *Main → Tools*：`gpl`（global placement）、`dpl`（detailed placement）、`rsz`（resizer / 时序优化）、`cts`、`grt`（global routing）、`pdn`、`sta`（OpenSTA，另有独立手册 `OpenSTA.pdf`）、`ppl`（pin placement）、`mpl`（macro placement）、`par`（partition manager）、`odb`、`gui`。

---

## 0. 赛题流程 ↔ OpenROAD 工具映射

| 赛题环节 | 任务一 | 任务二 | OpenROAD 工具 / 典型命令 | 指标采集点 |
|---|---|---|---|---|
| 网表切分 | 可改 | 可改 | 不在 OpenROAD 内（基线是 Python GNN，输出每个实例的层归属） | — |
| 双层布局 + 合法化 | 可改 | 可改 | DREAMPlace 式解析布局（Python/CUDA）→ 写 DEF → `detailed_placement` / `check_placement` | — |
| 供电网络 PDN | 固定 | 固定 | `pdngen`（`define_pdn_grid` / `add_pdn_stripe` / `add_pdn_ring` / `add_pdn_connect`） | — |
| 跨层端子 HBT 分配 | 固定 | 可改 | 可能是 Python 或 Tcl（把跨层网映射到 HBT 合法位，建模为两层各自的 pin/BTERM 或 3D via） | — |
| 时序优化 | 固定 | 可改 | `rsz`：`repair_design`、`repair_timing -setup/-hold`、`buffer_ports`、`set_dont_use` | **任务一终点**：一次 `repair_timing` 后，`estimate_parasitics -placement`，理想时钟 |
| 时钟树综合 CTS | — | 可改 | `clock_tree_synthesis`、`repair_clock_nets`、`set_propagated_clock` | — |
| 全局布线 GRT | — | 可改 | `set_routing_layers`、`set_global_routing_layer_adjustment`、`global_route`、`estimate_parasitics -global_routing` | **任务二终点**：GRT 后，传播时钟 + GRT 寄生；溢出取两层之和 |
| 静态时序分析 STA | 固定 | 固定 | `report_wns`、`report_tns`、`report_checks`、`report_check_types` | — |

两层芯片在 OpenROAD 里怎么建模（**待官方容器确认**，两种常见做法）：
1. **两个独立 OpenROAD 设计**（上层 / 下层各一套 LEF/DEF），HBT 在两层各建为 BTERM/pin，跨层时序由评估器在"连通的双层时序视图"里拼接；
2. **一个叠层设计**：技术 LEF 把两层金属堆叠成一套（如 M1..M10 + 键合层 + M1'..M10'），HBT 建模为穿过键合层的特殊 via/cut，STA 天然连通。
题面说 "在连通的双层时序视图中采集结果"，更像做法 1 + 评估器拼接或做法 2。确认后更新本节和 `harness/openroad_parse.py`。

---

## 1. 读入与建库

```tcl
read_lef  $tech_lef ; read_lef $cell_lef
read_liberty $lib               ;# 可多次
read_def  $def                  ;# 或 read_verilog + link_design <top>
read_sdc  $sdc
set_wire_rc -signal -layer M3   ;# 布局阶段寄生估算用的单位长度 RC（也可 -resistance/-capacitance 直接给）
set_wire_rc -clock  -layer M5
```

`set_wire_rc` 决定 `estimate_parasitics -placement` 的线延迟模型——**跨工艺时这个层名必须随 PDK 改**（ng45: metal1..metal10；asap7: M1..M9）。

---

## 2. 布局（gpl / dpl）

> 赛题基线布局是 DREAMPlace 式 Python 实现，OpenROAD 的 `global_placement` 大概率只在参考流程里出现；但合法化/检查很可能用 OpenROAD。

```tcl
global_placement -density 0.7 -timing_driven -routability_driven \
                 -pad_left 2 -pad_right 2 -overflow 0.1 -skip_initial_place
set_placement_padding -global -left 2 -right 2    ;# 给后续缓冲器留空间
detailed_placement -max_displacement {200 200}    ;# 单位：site 数 / 行数（按文档核实）
optimize_mirroring
check_placement -verbose                          ;# 任何 overlap / off-site 都是非法 → 候选无效
improve_placement                                 ;# 可选，局部交换降线长
```

关键参数与方向（DREAMPlace 侧有同名概念）：

| 旋钮 | 含义 | 增大的效果 |
|---|---|---|
| `density` / 目标密度 | 每个 bin 允许的利用率 | 更紧凑、线长↓，但拥塞↑、缓冲器没地方插 → GRT 溢出↑ |
| `-timing_driven` / 网权重 | 关键网线长项加权 | 关键路径↓（WNS/TNS↓），非关键网变长 |
| `-routability_driven` | 拥塞反馈膨胀单元 | 溢出↓，线长略↑ |
| padding | 单元左右留白 | 缓冲器/尺寸放大的空间↑，密度实际↑ |
| `-overflow` | 收敛判据 | 越小越慢、越均匀 |

DREAMPlace 对应项：`target_density`、`density_weight`（λ 初值与增长率 `density_weight_increase`?）、`gamma`（WA 线长平滑系数）、`net_weight`、`legalize_flag`、`detailed_place_flag`、`stop_overflow`、`iteration`。三维变体（文献[6] 双层线长模型）还会有**层间线长权重 / 跨层惩罚**。

核对页面：*Tools → gpl (Global Placement)*、*dpl (Detailed Placement)*。

---

## 3. 时序优化（rsz）——任务一的终点所在

```tcl
estimate_parasitics -placement            ;# 用 set_wire_rc + 布局线长估 RC
report_wns ; report_tns                   ;# 优化前基线
set_dont_use {CLKBUF_* DLY*}              ;# 可改：允许/禁止的单元
repair_design -max_wire_length 500 -slew_margin 10 -cap_margin 10   ;# 修 DRV：slew/cap/fanout/长线
repair_timing -setup -setup_margin 0.05 -repair_tns 100 \
              -max_utilization 85 -max_buffer_percent 20 \
              [-skip_pin_swap] [-skip_gate_cloning] [-skip_buffering] [-sequence ...]
repair_timing -hold -hold_margin 0.02 -allow_setup_violations
estimate_parasitics -placement
report_wns ; report_tns
```

`repair_timing -setup` 内部手段：**上/下调尺寸（resize）、插缓冲（rebuffer）、引脚交换（pin swap）、门克隆（clone）、分裂负载（split load）**——题面"可改进关键路径的修复顺序、缓冲器插入位置、单元尺寸选择及引脚交换策略"正对应这些，参数或源码（若开放）都可演化。

| 旋钮 | 方向 |
|---|---|
| `-repair_tns N`（修复 TNS 端点的百分比） | ↑ 修更多端点，TNS↓ 更多，但缓冲器↑、面积↑、拥塞↑、runtime↑ |
| `-setup_margin` | ↑ 过修保护 GRT 后退化，但也可能过度插缓冲 |
| `-max_utilization` | 缓冲器插入的密度上限；太低会提前停 |
| `-max_buffer_percent` | 缓冲器数量上限 |
| `-skip_*` | 关掉某类手段做消融 |
| `set_dont_use` | 库单元选择范围 |

**任务一采样点**："在且仅在一次时序优化后"、"布局估算的互连寄生参数和理想时钟"。意味着评估器固定跑一次 `repair_timing`，之后 `estimate_parasitics -placement` + `report_wns/tns`，不做 `set_propagated_clock`。我们只能通过"更好的切分/布局"来让这一次修复更有效——所以布局给缓冲器留空间（padding/density）与关键网拉近，是任务一最直接的杠杆。

核对页面：*Tools → rsz (Gate Resizer)*。

---

## 4. 时钟树综合（cts）

```tcl
clock_tree_synthesis -buf_list {BUF_X4 BUF_X8 BUF_X16} -root_buf BUF_X16 \
    -sink_clustering_enable -sink_clustering_size 20 -sink_clustering_max_diameter 50 \
    [-balance_levels] [-num_static_layers 1] [-distance_between_buffers 100] \
    [-branching_point_buffers_distance 150] [-clustering_exponent 4] [-clustering_unbalance_ratio 0.6] \
    [-obstruction_aware] [-apply_ndr] [-repair_clock_nets]
set_propagated_clock [all_clocks]
repair_clock_nets ; repair_clock_inverters
report_cts ; report_clock_skew ; report_clock_latency
estimate_parasitics -placement ; report_wns ; report_tns
repair_timing -hold ...            ;# CTS 后一般补一次 hold 修复
```

| 旋钮 | 方向 |
|---|---|
| `sink_clustering_size` / `max_diameter` | 簇大→缓冲器少、布线资源省，但偏斜↑、局部 skew 影响裕量 |
| `buf_list` / `root_buf` | 驱动强→latency↓、功耗↑ |
| `balance_levels` | 层级平衡→skew↓，缓冲器↑ |
| `distance_between_buffers` | 小→slew 好、缓冲器多 |
| `obstruction_aware` | 避开宏块，绕行↑ |

题面提示："切分和布局决定时钟接收端的空间分布"——两层各自有 sink 分布，CTS 在两层分别做还是统一做（**待确认**），会影响跨层 skew。

核对页面：*Tools → cts (Clock Tree Synthesis)*。

---

## 5. 全局布线（grt）——任务二的终点与溢出惩罚来源

```tcl
set_routing_layers -signal metal2-metal7 -clock metal5-metal6        ;# ng45 层名；asap7 为 M2-M7
set_global_routing_layer_adjustment metal2-metal7 0.5                ;# 0.5 = 该层容量打五折（给 DRC/电源留余量）
set_global_routing_layer_adjustment metal2 0.8
global_route -congestion_iterations 50 -congestion_report_file grt.rpt \
             [-critical_nets_percentage 30] [-allow_congestion] [-verbose]
estimate_parasitics -global_routing
set_propagated_clock [all_clocks]
report_wns ; report_tns
report_wire_length
```

GRT 日志末尾有拥塞汇总（格式要用真实日志核对，解析器已按两种常见形态写）：

```
[INFO GRT-0096] Final congestion report:
Layer         Resource        Demand        Usage (%)    Max H / Max V / Total Overflow
---------------------------------------------------------------------------------------
metal1        ...
...
---------------------------------------------------------------------------------------
Total         1234567         987654        80.00%       12 / 9 / 356
```

**溢出 = Total Overflow 列**；题面取**两层之和**，>300 开始罚，无上限。

| 旋钮 | 方向 |
|---|---|
| `layer_adjustment` | ↑ 容量更紧 → 溢出↑ 但更保守（实际详细布线更稳）；评测只到 GRT，**调低它会直接"消灭"溢出**——但这是改评估口径的灰色地带，题面说可改"拥塞代价"，是否允许改 adjustment **要向组委会确认** |
| `-congestion_iterations` | ↑ 更多重布线，溢出↓，runtime↑ |
| `-critical_nets_percentage` | 关键网优先路由（时序↓溢出可能↑） |
| `-allow_congestion` | 允许残留溢出而不报错（评测可能本来就开着） |
| `set_routing_layers` 范围 | 开放更多层→容量↑溢出↓；但时钟层选择影响时序 |
| `set_global_routing_region_adjustment` | 局部区域容量调整 |

核对页面：*Tools → grt (Global Routing)*。

---

## 6. 供电网络（固定，不要碰）

`pdngen` 系列。固定环节，但它**占用布线资源**：PDN 条带越密，GRT 容量越少。布局时避免把高密度区放在 PDN 条带密集处无意义（PDN 均匀）；但知道它会吃掉一部分 M4+ 资源，解释为何 `layer_adjustment` 存在。

---

## 7. STA 命令与指标提取（评估器口径）

```tcl
report_wns                 ;# 打印 "wns -0.123"
report_tns                 ;# 打印 "tns -45.678"
report_worst_slack -max    ;# "worst slack -0.123"
report_checks -path_delay max -group_count 10 -format full_clock_expanded \
              -fields {slew cap input_pins nets fanout}       ;# top-N 关键路径（问题画像用）
report_check_types -max_slew -max_capacitance -max_fanout -violators
report_clock_skew
report_design_area
```

Tcl 里取数值（写评估脚本 / 解析器时用）：
```tcl
set wns [sta::worst_slack_cmd "max"]
set tns [sta::total_negative_slack_cmd "max"]
```

**单位陷阱（跨 PDK，直接影响解析与评分）**
- OpenSTA 报告单位跟随 Liberty：**ng45 的 lib 时间单位是 ns**，**ASAP7 的 lib 时间单位是 ps**（电容 fF）。`report_wns` 在 asap7 上打印的数字是 ps。题面说 WNS/TNS 单位纳秒——评估器应做了换算，但我们自己的解析器必须读 `set_cmd_units` 或按 PDK 换算，否则 asap7 的改善比例会错一千倍。可在脚本开头统一 `set_cmd_units -time ns -capacitance pF -resistance kOhm`。
- DEF `UNITS DISTANCE MICRONS`：ng45 常见 2000，asap7 常见 1000（ORFS 的 asap7 LEF 经过 4× 缩放，有的版本是 4000）。解析坐标时读 DEF 头，不要写死。
- 行高 / site：ng45 `FreePDK45_38x28_10R_NP_162NW_34O`（1.4 µm 行高），asap7 `asap7sc7p5t`（0.27 µm）。合法化站点对齐跨工艺要按 LEF 读。
- 层名：ng45 `metal1..metal10`，asap7 `M1..M9`、`Pad`。任何写死层名的脚本在 bp_fe-asap7 上会崩 → **候选无效**。把层名做成从 LEF 读或从配置取。

核对页面：*Tools → sta*、OpenSTA 手册 `report_checks` / `set_cmd_units` 节。

---

## 8. 合法性检查清单（对应题面"物理设计合法性"）

| 要求 | 检查命令 / 方法 |
|---|---|
| 边界内、无重叠、行/站点对齐、合法朝向 | `check_placement -verbose`（返回非零即失败） |
| 跨层 HBT 在合法候选位、最小间距、不重叠 | 基线 HBT 分配脚本应自带检查；我们的 gate 可加一个几何检查（读 HBT 位置列表，pitch 网格 + 最小间距） |
| 跨层网正确连通 | 两层 DEF 的 NETS 对同一跨层网都连到对应 HBT pin；`report_checks -unconstrained` 无意外悬空 |
| 时序图完整 | `report_checks` 有路径；`check_setup`（OpenSTA）无 unclocked registers 增加 |
| 阶段完成 / 超时 | 流程脚本每阶段 `exit` 码；harness 记录 wall time |

---

## 9. 问题画像（harness 给 LLM 看的东西）应该包含

从基线报告里抽（`harness/profile.py` 实现）：
1. 每算例 WNS / TNS / 违例端点数 / slack 直方图（<-0.5, -0.5~-0.2, -0.2~-0.05, -0.05~0）；
2. top-10 关键路径：起点/终点、级数、线延迟占比、是否跨层（经过 HBT）、最长单段线；
3. 跨层网统计：切割网数、跨层网里关键网占比、HBT 利用率；
4. 密度/拥塞：每层利用率、GRT 每层 overflow（任务二）；
5. 时序优化动作统计：插了多少缓冲、resize 多少、哪些路径修不动（`repair_timing` 日志里有 "Repair timing ... inserted N buffers, resized M"）。

这些数字进 prompt 时压到 ≤ 60 行。

---

## 10. 勘误（拿到官方容器后填写）

| 本文条目 | 实际 | 修改日期 |
|---|---|---|
| | | |
