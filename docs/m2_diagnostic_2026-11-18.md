# M2 本机诊断记录（2026-11-18）

这是公开 Open3DBench 的 `bp_fe-ng45` / `tcad25` 诊断，**不是组委会评估结果，也不是任务一或任务二的基线分数**。

## 输入与复现

- 源码：Open3DBench `e6d23148beed101b7741960f8aa967544259b0df`，出处 `third_party/open3dbench_ref/SOURCE_COMMIT.txt`。
- 镜像：`shiyunqi/open3dbench:eval`，本机 image ID `sha256:32ab6667ca2a0708a6462b44a07cf6092828c3263f8a606c0184f31490e02235`。
- 数据：Place-LoL README 中的 `binaries.tar.gz`，SHA256 `B7409BF46C99E7A2AD5CDF9ED807C1ADE5A787D6CE33865B15FBC3FD7129AD9B`；使用 `binaries/converted_output/default/tcad25/bp_fe.def`。压缩包内的 202 个普通文件已校验解出；Windows 未创建的 12 个相对符号链接不参与本次 DEF 输入。
- 运行：`workspace/env/07_run_lol_bpfe.bat` 执行 `make DESIGN_CONFIG=designs/nangate45_3D/bp_fe_top/config_lol.mk do-lolflow`；`cd workspace; python tools/collect_m2.py` 收集日志。原始输出保存在本机忽略目录 `workspace/tests/samples/real_bp_fe/` 和 `workspace/logs/m2_bpfe_lol.log`，不入 Git。
- 首次运行脚本在 `do-lolflow` 结束后额外调用了公开仓库的 `do-hotspot`，后者因缺 `HotSpot/examples/thermal/run.sh` 失败。现已把脚本限定为 OpenROAD LoL 后端；该版本未重复跑 47 分钟的全流程。温度分析不属于题面计分环节。

## 真实观察

| 环节 | 观察 |
|---|---|
| LoL OpenROAD 完整流程 | 从启动到 HotSpot 报错共 `47m06.692s`；其中全局布线 `19.71s`、详细布线 `44m52.99s`、最终报告 `57.46s`。任务二若截在 GRT，可省去本次最耗时的详细布线；还须用官方流程实测单次耗时。 |
| `5_1_grt.json` | 只有 `globalroute__flow__warnings__count` 和 `globalroute__flow__errors__count`，没有 WNS/TNS；GRT log 的 20 层拥塞表 `Total` 溢出为 `0.0`。 |
| `6_report.json` | `finish__timing__setup__ws=-0.864202`、`finish__timing__setup__tns=-368.99`，单位按 NanGate45 Liberty 为 ns；这是详细布线后的诊断数值。 |
| 单独 GRT STA 采样 | 在 `5_1_grt.odb` 上载入 `4_cts.sdc`、估计全局布线寄生后，得 `globalroute__timing__setup__ws=-1.27487`、`tns=-1027.72`；采样产物 `grt_sampled.json`。采样脚本现为 `workspace/tools/task2_sample.tcl`。 |
| 本地任务一截断适配器 | `harness-prefix`（读 DEF、分层合法化）→ PDN → `repair_design` → 只读 STA 采样，得 `placeopt__timing__setup__ws=-2.21902`、`tns=-2112.76`。**同一次运行的 PDN 日志包含 `[ERROR PDN-0179] Unable to repair all channels.`，因此整次运行无效，这些时序数值只用于校验解析器。** |
| 本地任务二截断适配器 | `harness-prefix` → PDN → 布局优化 → CTS → GRT → 只读 STA 采样，单次约 `81s`，`globalroute__timing__setup__ws=-1.56829`、`tns=-1256.21`、总溢出 `0.0`。同样因 `PDN-0179` **无效**；与上表的公开 LoL 路径不同，数值也不可互换。 |
| 可选 `repair_timing -setup` 路径 | 接任务一 `repair_design` 后继续修 2862 个 setup 违例端点，单次约 `249s`，采样 `ws=-1.08447`、`tns=-984.049`。仍因前面的 `PDN-0179` **无效**；是否符合任务一的“一次时序优化”须由组委会确认。 |

本机原始文件 SHA256：`5_1_grt.json` `1C699A602876C2DD36D58D8DA76B709B62B2CA7D37864D6800025BDF650B26EC`；`5_1_grt.log` `4C85C30471040FA88EC3E976A5F05B37012065466E5E586FF152952329466DD9`；`6_report.json` `907B96625F4F8C1EFA64B05E8E371FFD4A75A3392CCB8D977EAFFECA53595A54`；`grt_sampled.json` `F0ACEDACB6DA788CCFF0B453D5ACB53FF17D318F4A55E11C37FFB8579BE516BF`；任务一 `placeopt.json` `EA57BB0EB5B901D9E8D0FE90A17A745132639617D359EBC2B04A2F9BB4101460`；任务二 `globalroute.json` `4540126B9CF786B74042524A9F10E56F5BBF52726C1617B14009DFDA00D3C52B`；可选 `repair_timing` 的 `placeopt.json` `DD07C1BB09DCFBF8023E4F303974B35D7D92F7FD7AC354C521B6386580B10FAB`。

## 适配器校正

`evaluate_open3dbench.py` 过去从普通 shell 直接调用 `read_def.tcl`，缺 `TECH_LEF` 等 Makefile 环境，立即失败。现在通过 `open3dbench_eval.mk` 在上游 Makefile 环境执行，并在两个采样点额外写时序 JSON。它还逐阶段扫描日志；即使上游 Make 命令通过 `tee` 返回 0，只要 PDN 等阶段有 `[ERROR]`，单次结果与 CLI 都返回失败。`LogDirEvaluator` 同样扫描每个阶段日志。

## 下一门禁

公开流程与题面流程有差异，特别是任务一的“时序优化”定义、PDN 和任务二 CTS。真正的评估须由组委会容器与接口确定输入、白名单、采样阶段和有效性，再做小预算试跑；这些诊断数值不得填入正式成绩表。
