# 技术报告 —— 面向 3D 芯片物理设计的多阶段算法协同演化（赛题 1）

> 状态：**骨架 + 已有内容填充**（2026-11-18）。标 `【待官方运行】` 的表格与数字在官方容器/评估接口到手、正式跑完后填。
> 结构按赛题附录"技术方案大纲"六节。参赛编号 / 队名 / 成员：`【待填】`

---

## 一、算法概述

### 1.1 问题

双层逻辑折叠（Logic-on-Logic）3D 芯片的物理设计：网表中的每个实例要决定归属上层还是下层（切分），再在各自层内确定坐标（布局），跨层连接经混合键合端子（HBT）落地；后续固定或可改的环节依次为供电网络、HBT 分配、时序优化、（全流程另含）时钟树综合与全局布线，最终以静态时序分析的 WNS/TNS 计分。赛题的真正评测对象不是某个算法，而是一个**智能体运行框架（harness）**：组委会从统一的初始算法出发，用指定基座模型（DeepSeek-V4.1-Flash）在 30 次候选评估、15 元 API 费的预算内重跑 harness，以其自行演化出来的算法的时序改善比例打分。

### 1.2 已有主流方法

- **切分**：超图 min-cut / 多层次划分（hMETIS 一类）；TP-GNN [4] 用图神经网络学节点表示再分层并修复面积平衡；Open3DBench [3] 的 LoL 基线用 min-cut + 模拟退火（宏级），MoL 分支有 GIN 对比学习 + KMeans + 嵌入距离平衡修复。
- **布局**：解析式布局（ePlace/RePlAce 系）以平滑线长 + 密度惩罚做梯度优化，DREAMPlace [5] 用深度学习框架加速；Open3DBench 的 DREAMPlace fork 已内置时序驱动网权重（OpenTimer 后端，adams/lilith 方案），双层解析布局研究见 [6]。
- **后端**：OpenROAD [7]（rsz 时序修复、TritonCTS、FastRoute）；Open3DBench 用 NanGate45 复制金属层构造 20 层"NanGate45_3D"平台，HBT 建模为 1µm CORE 宏作为虚拟缓冲器。
- **算法演化**：AlphaEvolve [8]（LLM 提出代码修改 + 评估器打分 + 进化式候选库）、Darwin Gödel Machine [9]（智能体改自身代码）。两者假设评估便宜；本赛题一次评估 = 9 次完整物理设计流程，预算 30 次，是**极端评估稀缺**场景。

### 1.3 本方案的定位

一个**预算感知、假设驱动、带免费本地门禁**的 AlphaEvolve-lite：不做大种群随机搜索，而是（a）用领域知识把搜索空间组织成"旋钮菜单"，（b）每一次昂贵评估之前用零成本的静态/语义检查拦住必然无效的候选，（c）每一次评估之后把日志压成结构化"问题画像"喂回模型，（d）用跨算例稳健性而非均值选择父代以抵抗过拟合。

---

## 二、实现方案

### 2.1 动机与设计原则

| 约束（来自题面） | 推导出的设计 |
|---|---|
| 30 次评估、每次 9 个完整流程 | 评估是唯一稀缺资源；LLM 调用（实测 30 轮 ≈ 0.2 元）几乎免费 → 大量使用 LLM 做"提出 + 修复 + 分析"，但每次评估必须有明确假设 |
| 候选任一 run 无效即整体无效 | 本地门禁（路径/语法/未定义名/Tcl 括号/Makefile 形式/去重）在提交前拦截；无效候选不进精英集 |
| 验证集结果禁止用于选择、决赛有隐藏算例 | 选择指标 = 跨训练算例 mean − σ；门禁把"按算例名/工艺名分支"与"只改一个算例的配置"判为错误 |
| TNS 占 60% | 提示词与旋钮菜单优先"消除大量中等违例"的机制（时序驱动网权重、关键网切割代价） |
| 同分比 API 费 | 代码片段按需检索（≤ 6k 字符）、固定前缀吃缓存、thinking 关闭、费用自适应缩放 |
| 提交 logs/ 须含父子关系/版本/结果/失败原因 | 候选一拿到 id 即落 `meta.json` + `lineage.jsonl`；崩溃路径 `finally` 兜底 |

### 2.2 工作流程

```
init: 读取基线评估（或花 1 次评估跑基线）→ 问题画像（WNS/TNS、slack 直方图、top 路径跨层比例、
      repair_timing 动作统计、GRT 溢出、切分统计）≤ 60 行
loop while budget_ok:
  phase = probe(6) | structural(14) | combine(8) | final(2，用 reserve)   ← 按已用评估数推进
  parent = None（probe）| softmax(精英集, 温度随阶段降低)（其它）
  work   = git apply(parent.patch) 到干净基线文件            ← 不复制父代目录，评估器产物不会被继承
  ① Proposer : system(领域知识 + 白名单大纲) + user(阶段指引 + 问题画像 + 历史摘要(含 Analyzer 结论)
               + 父代 patch + 检索到的代码片段) → HYPOTHESIS / EXPECTED / KEYWORDS / SEARCH-REPLACE 块
               检索焦点：probe 阶段按旋钮菜单轮换；有父代时父代改过的行强制进展示窗口
  ② Apply    : SEARCH 唯一精确匹配，原子写入；precheck 拒绝"既没展示又不在真实代码里"的 SEARCH（编造）
  ③ Gate     : 白名单/禁改 → 算例名、PDK 分支 → per_design 一致性 → 语法（py/tcl/yaml/json/mk）
               → 静态（只报新引入）→ 去重（hunk 指纹）→ [可选冒烟]；失败则带反馈再给模型 1 次修复机会
  ④ Evaluate : 官方接口（或 tools/evaluate_open3dbench.py）→ 9 个 run 的指标 + 日志；严格计 1 次
  ⑤ Analyze  : 规则摘要（每算例 ΔWNS/ΔTNS/溢出、失败类型）+ LLM ≤120 词解读（区分"无效"与"假设不成立"）
  ⑥ Select   : 候选 = 评估通过 ∧ 全部有效 ∧ (full_flow: 最大溢出 ≤ 300)；稳健分 = mean − σ；
               血统淘汰（仅真实评估的失败计数，gate 失败不算）；精英集 top-4
final: 导出最优 candidate.patch（可 git apply 到官方基线）+ SELECTED.json + summary.json(code_version)
```

### 2.3 提示词组织

- `system.md`：角色、计分公式、硬规则（只改展示过的代码 / 一次一个可解释改动 / 禁特判 / per_design 一致）、输出协议、领域知识块（`knowledge.md`：20 层金属平台、HBT 宏、采样点、泛化杠杆、合法性杀手、跨 PDK 陷阱）、白名单符号大纲。固定前缀，命中缓存。
- `propose.md`：阶段指引（`phase_probe[_full_flow].md` 旋钮菜单 / `phase_structural[_full_flow].md` / `phase_combine.md` / `phase_final.md`）+ 问题画像 + 历史 + 父代 patch + 代码片段。
- `fix.md`：带门禁反馈的单次修复。
- Analyzer 提示：固定模板，≤120 词。

### 2.4 工具调用与反馈分析

见 `harness/openroad_parse.py`（按 OpenROAD 源码校正的 rsz 进度表、GRT 拥塞表 "Total Congestion"、ORFS 指标 json key `<stage>__timing__setup__ws/wns/tns`、失败探测、ps→ns）与 `harness/profile.py`。

### 2.5 候选选择

`scoring.py` 严格按题面第六节实现（含溢出惩罚无上限）；`selector.py` 的稳健分 mean−σ；full_flow 溢出硬门槛；final 阶段只做保守微调。

### 2.6 对 harness 演化出的算法的解释

`【待官方运行】` 以下为 mock 评估器彩排中演化出的最优候选（`docs/rehearsal_best_candidate.patch`，分数无物理意义，仅展示机制可解释性）：对 DREAMPlace fork 的 `TimingOpt.update_net_weights` 做了三处改动——把动量衰减系数钳到 ≤ 0.5（让关键性信号对当前布局更敏感，避免少数早期关键网垄断权重累积）、把高扇出忽略阈值减半并设下限 16（让中等扇出网也获得时序权重，它们合起来承载大量中等违例）、接受 "adam" 作为 "adams" 的别名。血统：c007（开启时序驱动网权重）→ c011（衰减钳制）→ c018 → c021；combine 阶段对权重上限做 ×0.5 / ×2 微调并被 final 阶段确认。这条链与 §2.1 "TNS 优先"的设计一致。

---

## 三、实验与创新点

### 3.1 实验设置

- 基座模型 `deepseek-flash`（= DeepSeek-V4.1-Flash），thinking 关闭，temperature 0.7/0.2/0.1（propose/fix/analyze）
- 预算 30 次评估 / 15 元（本地 12 元留余量）
- 算例：训练集 bp_fe-ng45、bp-ng45、ariane133-ng45；验证集 swerv_wrapper-ng45、bp_fe-asap7；种子 1001/1002/1003

### 3.2 各算例完整指标 `【待官方运行】`

| 任务 | 算例 | 种子 | 基线 WNS | 最终 WNS | ΔWNS% | 基线 TNS | 最终 TNS | ΔTNS% | 溢出 | 得分 |
|---|---|---|---|---|---|---|---|---|---|---|
| 切分布局 | bp_fe-ng45 | 1001 | | | | | | | — | |
| … | | | | | | | | | | |
| 全流程 | … | | | | | | | | | |

初赛成绩 = 80% × 切分布局 + 20% × 全流程 = `【待填】`

### 3.3 演化效率

`【待官方运行】` 用 `tools/report.py` 输出"第 k 次评估后的最优稳健分"曲线。

彩排（真实 LLM × 真实 Open3DBench 代码 × mock 评估器，仅验证 harness 行为）：31 轮 / 30 次评估全部用满 / 1 次门禁拦截 / 6 次无效 / 0.17 元；血统树显示 probe→structural→combine→final 的推进与"组合已验证机制"的行为。修复审核清单前的同配置彩排为 37 轮 / 28 次评估 / 9 次门禁拦截——**precheck + 父代改动区域进展示窗口** 把门禁失败从 24% 降到 3%。

### 3.4 消融 `【待官方运行；可先用 mock 做行为消融】`

| 部件 | 关闭后预期影响 | 结果 |
|---|---|---|
| precheck（SEARCH 必须来自展示代码） | 门禁失败率 ↑（模型编造上游代码） | mock：24% → 3% |
| 父代改动行强制进展示窗口 | combine 阶段 SEARCH 对不上 | mock：combine 阶段 gate_failed 4/5 → 0 |
| 稳健分 mean−σ vs mean | 验证集/隐藏算例退化 | |
| 问题画像 vs 只给 WNS/TNS | 探针方向质量 | |
| 旋钮菜单轮换 vs 自由检索 | 探针覆盖面（6 次探针是否看了 6 个区域） | |
| Analyzer LLM 解读入史 | structural 阶段假设质量 | |

### 3.5 创新点（区分已有方法与团队贡献）

| 已有方法 | 本方案的贡献 |
|---|---|
| AlphaEvolve 的"LLM 改代码 + 评估 + 进化库" | 面向**评估极稀缺**的三段式预算分配（探针/结构/组合 + final 保留），旋钮菜单驱动的探针轮换 |
| SEARCH/REPLACE 编辑协议 | **precheck**：以"展示过的代码 ∪ 真实文件唯一匹配"为合法 SEARCH 集合，零成本拦编造；父代工作副本叠加 + 改动行窗口 |
| 静态检查 | 只报候选新引入的问题；Tcl/Makefile/YAML/JSON 形式检查；**算例名 / PDK 分支门禁** 与 **per_design 一致性** 把题面"不得特判"变成可执行规则 |
| 进化选择 | 稳健分 mean−σ、溢出硬门槛、血统淘汰只计真实评估、hunk 指纹去重 |
| 日志 | 候选在拿到 id 的瞬间落盘；`summary.json` 记录 harness commit 与基线白名单文件哈希 |

---

## 四、问题与思考

### 4.1 未改善的算例 `【待官方运行】`

### 4.2 跨算例与跨工艺适应

- bp_fe-asap7 平台（层名 M1–M9 镜像、ps 单位、不同 site/行高）完全不在训练反馈里；harness 侧用三道防线：提示词明令从 LEF/Liberty 读参数、门禁拒绝按 PDK 名分支、`detect_time_unit` 只认 Liberty `time_unit`。
- 仍是风险：候选若隐式依赖 ng45 的数值尺度（如密度权重初值、HBT 1µm pitch），在 asap7 上可能退化；final 阶段的保守微调无法发现这一点。

### 4.3 搜索稳定性

- API 随机性：组委会多次复跑取最优；harness 自身对父代采样用固定种子。
- 单次评估噪声：三种子平均；稳健分用 σ 压制。
- 已知局限：30 次评估下 structural 阶段只有 14 次，机制组合空间远未穷尽；探针只改一处，交互效应看不到。

### 4.4 方法局限

- 门禁的"算例名"规则是文本级的，改变量名就能绕过——它防的是模型无意的特判，不防恶意。
- `per_design` 一致性要求"added 行集合相同"，对合理的按算例缩放参数（如按面积比例）也会拒绝；这是刻意保守。
- Mock 评估器的分数与物理无关，只用于验证 harness 行为；所有"有效"结论要等官方运行。

---

## 五、过程进度与团队分工

| 日期 | 版本 / 里程碑 | 预算消耗 |
|---|---|---|
| 2026-11-17 | M0 harness 骨架 + mock + 25 测试 | 0 |
| 2026-11-17 | 联网核实 deepseek-flash（thinking/缓存/字段）、Open3DBench 真实结构、OpenROAD 日志格式 | 0.02 元 |
| 2026-11-17 | 真实代码基线、ORFS json、30 轮彩排 v1（28 评估，9 门禁拦截） | 0.19 元 |
| 2026-11-18 | 对抗式审核 23 项全部修复；30 轮彩排 v2（30 评估，1 门禁拦截）；full_flow 10 评估彩排 | 0.27 元 |
| `【待填】` | M2 真实流程耗时 / M4 官方接口小预算真跑 / M5 正式 30 次 | |

成员分工 `【待填】`。外部工具：Claude（Anthropic）辅助编写 harness 代码、文档与测试；DeepSeek-V4.1-Flash 为 harness 的基座模型。

---

## 六、复现与参考资源

- 复现入口：`README.md`（根）→ `workspace/README.md`；`workspace/run.sh <task>`；提交清单 `docs/submission_checklist.md`
- 代码版本：每次运行的 `summary.json["code_version"]`（harness git HEAD、基线白名单文件 sha256、模型名）
- 论文 / 开源代码 / 工具（名称 · 版本 · 许可证 · 使用位置）`【许可证待核对】`：
  - Open3DBench（lamda-bbo/Open3DBench v1.1，commit 见 `third_party/open3dbench_ref/SOURCE_COMMIT.txt`）—— 基线占位与评估流程参考；`workspace/baseline/`、`tools/evaluate_open3dbench.py`
  - OpenROAD（The-OpenROAD-Project，commit 615e34e 的 `src/*/README.md` 与 `src/Metrics.tcl`，BSD-3）—— 命令签名与指标 key 核对；`docs/openroad_reference.md`、`harness/openroad_parse.py`
  - DREAMPlace（limbo-018/DREAMPlace，BSD-3；经 Open3DBench fork）—— 布局基线
  - 文献 [1]–[9] 见赛题参考文献；本报告引用处已标注
  - DeepSeek API（api.deepseek.com，模型 `deepseek-flash`）
