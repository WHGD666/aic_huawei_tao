# 赛题 1 总体规划 —— 面向 3D 芯片物理设计的多阶段算法协同演化

> 版本 v0.4 · 2026-11-17 · 状态：**harness 在真实代码上完成 30 轮彩排；等待官方容器与基准数据**
> 本文件是项目的"总控文档"：题目理解、策略、harness 设计、环境、目录、里程碑、风险、待确认事项。后续所有改动以此为准并持续更新。
>
> 配套文档：**`docs/open3dbench_notes.md`（真实平台结构、评估流程、输入输出契约 —— 先读这个）**、`docs/openroad_reference.md`（六环节 ↔ OpenROAD 命令/参数，§10 勘误表已按源码校正）、`docs/knob_catalog.md`（演化旋钮目录）、`docs/submission_checklist.md`（提交清单）、`workspace/README.md`（怎么跑）。
> GitHub：https://github.com/WHGD666/aic_huawei_tao （根目录 `git_sync.bat` 推送）

---

## 0. 一句话理解

**官方评测的不是我们调好的算法，而是我们写的 harness 本身。** 组委会拿我们提交的 harness，从官方初始算法出发、用 DeepSeek-V4.1-Flash、在 30 次候选评估 + 15 元 API 的预算内重跑一遍，用它"自己演化出来的算法"的 WNS/TNS 改善比例打分。所以这是一道 **"预算极度受限的 LLM 代码演化系统"** 题，EDA 知识是用来让每一次昂贵的评估都押对方向的。

---

## 1. 题目拆解（已彻底读完 md / pdf / zip，三者内容一致）

### 1.1 两项任务

| | 任务一：切分 + 布局协同 | 任务二：全流程 |
|---|---|---|
| 可改环节 | 网表切分（GNN，TP-GNN 思路）、双层解析布局（DREAMPlace 式） | 切分、布局、HBT 跨层端子分配、时序优化、CTS、全局布线（6 环节） |
| 固定环节 | PDN、HBT 分配、时序优化、STA | PDN |
| 采样点 | **一次时序优化后**；布局估算寄生 + 理想时钟 | **双层全局布线后**；传播时钟 + GRT 估算寄生 |
| 得分 | 40%·WNS改善 + 60%·TNS改善 | 40%·WNS改善 + 60%·TNS改善 − 溢出惩罚 |
| 溢出惩罚 | 无 | 两层溢出之和 > 300 时：10%×(溢出−300)/300，**无上限** |

改善比例 = (初始违例量 − 候选违例量) / 初始违例量，按"同算例同种子"对官方初始算法比较，**可为负**。

### 1.2 权重与阶段

- 初赛 = 80% 任务一 + 20% 任务二，按 **5 个算例**（3 训练 + 2 验证）等权加和；不设答辩；最多 6 队晋级 → **初赛资源压倒性投任务一，任务二只求稳定不拖后腿**
- 决赛算法成绩 = 20% 任务一 + 80% 任务二，按 **7 个算例**（5 公开 + 2 隐藏）等权加和；再以本届最高算法原始成绩归一化为 100 分
- 决赛总分 = 50% 算法（百分制）+ 50% 答辩（百分制；harness 创新 45 / 实验论证 30 / 可复现 10 / 问答 15）
- 同分规则：**初赛**先比任务一得分；**决赛**先比决赛算法成绩、再比任务二得分；仍同分比实际 API 费用（少者胜）；晋级临界位置仍无法区分由组委会复核裁定

### 1.3 数据

- 训练集：`bp_fe-ng45`、`bp-ng45`、`ariane133-ng45` —— 每次候选评估只在训练集上跑，是 harness 决策依据
- 验证集：`swerv_wrapper-ng45`、`bp_fe-asap7`（同电路跨工艺，考泛化）—— 输入是公开的，但**正式演化运行中其结果禁止用于修改或选择候选**
- 决赛另有 2 个隐藏算例 → **任何按算例名特判的做法都是违规且无效的**
- 种子 1001/1002/1003，先每种子打分，再取均值，再算例等权加和

### 1.4 预算（最硬的约束）

- 单任务单次运行：**≤ 30 次候选评估**，**≤ 15 元** API 费（非高峰价），任一触顶即停
- 1 次候选评估 = 3 训练算例 × 3 种子 = **9 次完整物理设计流程**。单次流程耗时原文未给（我的估计是十分钟到小时级，**待 M2 实测**），30 次评估的墙钟时间可能是天级，实际可用候选数可能远不到 30
- 外层官方循环负责计量，harness 不能修改、绕过或重置计量
- 所有模型调用必须经官方代理、使用指定的 DeepSeek-V4.1-Flash，**不得更换或混用其他基模**（开发阶段用公开 API 调试可以，正式运行只认官方代理）

### 1.5 合法性与隔离（无效 = 候选作废）

布局：边界内、无重叠、行/站点对齐、合法朝向；HBT：合法候选位、最小间距、跨层连通。指标缺失 / 非法数值 / 连通性破坏 / 时序图不完整 / 阶段失败 / 超时 → 该次运行**无效**。**最终候选在所有必测算例+种子上必须全部有效**（原文没说无效记 0 分，而是整个候选不合格）。所以"提交评估前的本地检查"直接等价于分数。

隔离边界（十一(一)）：候选只能改允许的设计决策；**不得修改**评估算法、指标提取、计分程序、参考数据、预算记录、违例定义与有效性判定；**输入网表、工艺、时钟约束等输入数据也不得篡改**。正式运行中**不得人工干预**算法生成与选择；候选确定后要能在不人工修改输入或结果的条件下完成两项任务的执行。

### 1.6 交付物

```
参赛编号_赛题 1 初赛.zip        （原文写法；决赛同理，上传百度网盘，链接设永久有效、自动填充提取码）
├── README.md                      运行命令 / 目录用途 / 复现步骤
├── requirements.txt               仅当需要容器外额外依赖
├── harness/                       最终版 harness 源码 + run.sh 统一入口（提示模板、运行配置一并冻结）
├── algorithms/
│   ├── partition_placement/candidate.patch   （标准 git diff + 新增源码）
│   └── full_flow/candidate.patch
├── <物理设计结果>/<子任务>/<算例>/<种子>/   切分方案、上下层布局、指标、时序报告（全流程另附布线结果）
│                                  ↑ 顶层目录名原文未规定，我暂用 results/
├── logs/                          演化记录：父子关系、代码版本、评估结果、失败原因
└── 技术报告（按附录大纲）；决赛另加答辩 PPT
```

材料里还必须：注明所用论文 / 开源代码 / 外部工具的**使用位置、版本和许可证**（十一(三)、十四）。

---

## 2. 得分策略（先想清楚再花评估次数）

1. **TNS 是主战场（60%）。** TNS 汇总所有负 slack 端点，通常比 WNS 容易撬动一个数量级；WNS 往往被一两条硬路径钉死。优先目标：减少大量中等违例路径，而非死磕最差路径。
2. **时序关键性要进切分目标。** 基线 GNN 切分只看连接结构 + 单元特征；把 STA 得到的 slack / 关键路径信息做成网权重或节点特征，让关键路径上的单元"要么同层相邻，要么垂直相邻"，这是题面"解题思路"直接点名的方向。
3. **布局侧：关键网加权 + 跨层网建模。** DREAMPlace 的线长项按网权重缩放，给时序关键网更高权重；双层密度要分别控制，给后续缓冲器留空间。
4. **全流程里任何"用绕线换时序"都可能被溢出惩罚反噬。** 惩罚无上限，初赛阶段任务二的候选以"不增加溢出"为硬门槛。
5. **泛化优先于单算例极致。** 验证集结果不可用于选择、决赛有隐藏算例，harness 的选择逻辑要惩罚"三个训练算例上方差大"的候选（例如用 min 或 mean−σ 作为选择指标，而不是纯 mean）。
6. **省 API 费是显性目标**（同分比费用）：源码按需检索而非全文塞入；日志做摘要/差分；提示词固定前缀在前、变化内容在后（若官方代理按缓存命中打折则直接省钱，**代理计费方式待确认**）。

---

## 3. Harness 设计（核心交付）

### 3.1 定位

一个 **预算感知、假设驱动、带静态门禁** 的 AlphaEvolve-lite：不做大规模种群搜索（30 次预算不允许），而是"少量可解释的算法假设 + 每次评估尽量多榨取信息 + 失败在本地拦截"。

### 3.2 演化循环（题面要求的六步映射）

```
┌──────────────────────────────────────────────────────────────────┐
│  init: 读取官方基线 → 建立代码索引(白名单文件/函数级分块)              │
│        读取基线评估报告 → 生成"问题画像"(关键路径/跨层网/拥塞摘要)     │
└──────────────────────────────────────────────────────────────────┘
loop while budget_ok:
  ① 提出修改  Proposer(LLM)  : 输入 = 问题画像 + 历史摘要 + 目标函数片段
                               输出 = {假设, 预期影响, 修改目标文件, SEARCH/REPLACE 块}
  ② 生成代码  Applier        : 在 git worktree 应用修改 → commit → candidate_id
  ③ 检查代码  Gate(本地,免费) : 白名单路径 ∧ 语法/静态检查 ∧ 禁改评估器
                               ∧ 微型冒烟(最小算例/单种子/截断迭代,若允许离线跑)
                               失败 → 把错误回传 LLM 做 1 次修复，再失败则弃
  ④ 提交评估  Evaluator      : 调官方评估接口 → 9 次流程 → 指标 JSON + 阶段日志
  ⑤ 分析反馈  Analyzer       : 规则提取(WNS/TNS/溢出/HBT 数/运行时/失败阶段) 
                               + LLM 摘要(≤300 tokens) → 写入候选元数据
  ⑥ 选择方案  Selector       : 精英集(top-k) + 稳健性指标(跨算例 min/mean−σ)
                               + 血统淘汰(连续 2 次失败即弃) → 选下一轮父代
end
final: 导出最佳候选 candidate.patch + results/ + logs/ (父子关系图)
```

### 3.3 关键模块（已实现，见 `workspace/harness/`）

| 模块 | 职责 | 备注 |
|---|---|---|
| `budget.py` | 本地影子计量：评估次数、token（含 reasoning）、费用（价格表可配） | 官方计量为准，本地只做提前刹车 |
| `codebase.py` | 白名单文件索引、类按方法分块、关键字检索 + 命中窗口截取、父代工作副本叠加、`patch_changed_lines` | 控制 prompt 长度的核心；每轮代码片段 ≤ 6k 字符 |
| `proposer.py` | 组织提示词（system + knowledge + outline / propose + phase guide / fix），解析 SEARCH/REPLACE（宽松版），**precheck** 拒绝未展示代码 | 小模型对 unified diff 格式错误率高，SEARCH/REPLACE 更稳 |
| `gate.py` | 路径白名单、禁改模式（词边界正则）、py_compile / Tcl 括号 / YAML / JSON / Makefile 语法、pyflakes 或内置未定义名检查（只报新引入）、可选冒烟 | 免费、快；彩排里拦下 9/37 |
| `evaluator.py` | `MockEvaluator` / `OfficialEvaluator`（JSON 接口）/ `LogDirEvaluator`（原始日志目录，json 优先） | 接口形式待官方容器确认 |
| `openroad_parse.py` + `profile.py` | wns/tns、关键路径块（跨层判定）、GRT 拥塞表、rsz 统计与进度表、ORFS json、失败探测、ps→ns；问题画像 ≤ 60 行 | 格式按 OpenROAD 源码校正，待真实日志复核 |
| `analyzer.py` | 规则摘要 + LLM ≤120 词解读（区分"无效"与"假设不成立"） | 摘要缓存，避免重复付费 |
| `selector.py` | 精英集、稳健性指标（mean−σ）、父代 softmax 采样、血统淘汰、阶段推进、溢出硬门槛、历史摘要 | 稳健性指标防过拟合 |
| `store.py` | `candidates/<id>/{meta.json, candidate.patch, eval.json, llm/*.json}` + `lineage.jsonl` | 直接产出 logs/ 要求的父子关系 |
| `llm.py` | DeepSeek 客户端（仅 `requests`）、thinking 开关、重试、record/replay | 走官方代理只需改 base_url（待确认兼容性） |
| `loop.py` | 六步主循环、probe 关键字轮换、费用自适应、容错、`summary.json`、导出 `algorithms/<task>/candidate.patch` | CLI 读取任务与预算（题面 §七） |
| `tools/report.py` | 运行目录 → 技术报告 Markdown | 答辩"实验论证"素材 |

### 3.4 30 次评估的分配（任务一）

| 阶段 | 评估次数 | 做什么 |
|---|---|---|
| 0 | 0 | 基线参考值由官方提供（**待确认**是否需自己跑） |
| A 敏感性探针 | 5–6 | 单旋钮改动：布局时序权重、密度目标、切分平衡容差、GNN 学习目标系数……每次只改一处，建立"哪个旋钮对 TNS 敏感"的认识 |
| B 结构性假设 | 12–14 | 基于 A 的结论做真正的算法改动：关键性加权切分目标、slack 驱动的网权重、跨层网感知的平衡修复、密度/线长自适应 |
| C 组合与微调 | 6–8 | 把 B 里独立有效的部件做"交叉"，微调系数 |
| 保留 | 2 | 最终候选复核 / 应急 |

任务二沿用同一 harness，只是白名单扩大到 6 个环节，并把溢出作为硬门槛加入 selector。

### 3.5 费用控制（15 元正式 / 10 元开发）

- 单轮软上限 0.35 元（`config/harness.yaml: per_round_cost_cap_cny`）：Proposer 一次（≈ 6k in / 2k out）+ Analyzer 一次（≈ 3k in / 0.4k out）+ 修复最多一次
- 提示词结构固定"系统提示 + 代码片段"在前、"历史/反馈"在后 —— 若代理按缓存命中打折可省 input 费（待确认）
- 开发阶段用 **mock evaluator + 录制回放的 LLM 响应** 调试循环，真实 API 只用于提示词质量验证
- 开发预算分配（10 元）：连通性测试 0.1 / 提示词迭代 ≤ 3 / 一次完整彩排（30 轮 × mock 评估）≤ 4 / 预留 ≥ 2
- harness 每次调用都记录 `prompt_tokens / completion_tokens / cache_hit_tokens`，费用由 `config/pricing.yaml` 算出，官方单价确认后只改这一个文件
- 本地计分口径与官方差异（必须在报告中注明）：基线违例量为 0 时原文公式分母为 0，本地约定"候选也为 0 记 0，否则记 −1"；任一 run 无效则整个候选记 −∞（不参与选择）

### 3.6 自演化（可选加分项，答辩 45 分"创新"的素材）

先做人工设计的 v1；v1 稳定后，把 harness 自己的提示模板放进"可演化白名单"，用小规模离线实验（mock 评估）让智能体改进提示词与选择策略，记录消融结果。最终只提交一个固定版本，题面允许。

---

## 4. 环境现状（已探查）

### 4.1 本机（Windows，无管理员权限，C 盘零写入）

| 项 | 发现 | 用途 |
|---|---|---|
| F 盘 | **349 GB 可用** | 本项目全部环境与数据的家 |
| D 盘 | 仅 26 GB 可用（91% 已满） | 不再往 D 放大东西 |
| `D:\anaconda` | base Python 3.13；可用 `conda.exe` | 用它创建 **prefix 在 F 盘** 的环境 |
| `D:\anaconda\envs\weixing` | Python 3.10 + **torch 2.5.1+cu121** + networkx/numpy/scipy | 可 `--clone` 到 F 盘作为本地 GNN/DREAMPlace 实验底座 |
| `D:\anaconda\envs\vocs` | Python 3.10 + torch 2.5.1 (cu118) + torchvision/torchaudio | 备选 |
| Docker Desktop (WSL2 后端) | 数据盘 `D:\DockerDesktopData\DockerDesktopWSL\disk\docker_data.vhdx` 4.3 GB | **跑 OpenROAD / 官方容器的唯一途径**；需把磁盘镜像位置迁到 F |
| GPU | 驱动目录含 `NVGFE / NVVGA`（NVIDIA）+ AMD 核显；型号/显存待 `nvidia-smi` 确认 | 本地 DREAMPlace 实验可走 CUDA；**官方评测是否给 GPU 原文未说，待确认** |
| Git | 沙箱有 git 2.34；本机 Git 安装位置待确认（`D:\Git` 只有 study 目录） | harness 用 git 管候选 |
| 已有 Open3DBench / OpenROAD / DREAMPlace | **未找到** | 需从官方容器或 GitHub 获取 |

### 4.2 我这边的沙箱（Linux）

Ubuntu 22.04 / Python 3.10 / 无 torch / 无 GPU / 3.9 GB 空间。
网络（2026-11-17 你放行后）：**`api.deepseek.com`、`github.com`（含 git clone）、`pypi.org` 可达**；`openroad.readthedocs.io` 被 Cloudflare 挑战拦（改从 OpenROAD GitHub 仓库的 `src/*/README.md` 读，内容一样）；`raw.githubusercontent.com`、`api.github.com`、`api-docs.deepseek.com` 仍被拦。
→ 能做：写代码、跑 mock 测试、**调 DeepSeek API**、**克隆仓库读源码**。不能做：装 torch、跑 EDA 流程、git push（沙箱里 push 没凭据，仍用你本机 `git_sync.bat`）。

### 4.3 DeepSeek API（官方直连，已实测）

- key 在 `workspace/.env`（gitignore）；余额 9.78 元（2026-11-17 02:20）
- 账号可见模型：**`deepseek-flash`**（= 题面 V4.1-Flash；`deepseek-chat` 是它的别名，返回 model 字段仍是 deepseek-flash）、`deepseek-v4-pro`
- **`deepseek-flash` 默认开 thinking**：返回 `reasoning_content`，`reasoning_tokens` 计入 `completion_tokens` 收费，且 `max_tokens` 小时 content 为空。harness 默认发 `"thinking": {"type": "disabled"}`（`DEEPSEEK_THINKING=1` 可开）
- 缓存命中真实存在：同前缀第二次调用 `prompt_cache_hit_tokens=512`（按 64-token 块对齐）→ 提示词"固定前缀在前"的设计有效
- usage 字段：`prompt_tokens / completion_tokens / prompt_cache_hit_tokens / prompt_cache_miss_tokens / completion_tokens_details.reasoning_tokens`
- 单价：定价页打不开，`config/pricing.yaml` 是估计值；`tools/calibrate_pricing.py` 可用余额差反推（花约 0.05 元）
- 实测一次完整 propose（system 991 tok + user 603 tok → 205 tok 输出）：2.1 秒，约 0.002 元，格式 100% 符合协议，gate 通过，patch 可 `git apply`。按此估算 30 轮 × (propose + analyze + 偶尔 fix) ≈ **0.3–0.5 元**，远低于 15 元上限——预算压力主要在评估次数而非 API 费

### 4.4 Git 工作流

- 仓库根 = `F:\huawei_aic_tao\task1`，远端 `origin = https://github.com/WHGD666/aic_huawei_tao.git`，分支 `main`
- 我在沙箱里 commit（无法 push）；你在本机双击 `git_sync.bat "说明"` 完成 add/commit/pull --rebase/push
- 不入库：`env/`、`third_party/`、`workspace/.env`、`workspace/{logs,results,candidates,baseline}/`（都在根 `.gitignore`）。最终用于提交的 logs/results 由人工挑一份放进 `submission/` 再入库

---

## 5. 环境安装清单（全部落在 `F:\huawei_aic_tao\task1\`）

```
F:\huawei_aic_tao\task1\
├── 1\                         赛题原文（已有）
├── PLAN.md                    本文件
├── env\                       ← 所有环境相关，C 盘零写入
│   ├── conda\tao\             harness 轻量环境（python 3.10 + requests + pyyaml + pytest …）
│   ├── conda\tao-eda\         （可选）clone 自 weixing：torch cu121，用于本地 GNN 实验
│   ├── conda-pkgs\            CONDA_PKGS_DIRS
│   ├── pip-cache\             PIP_CACHE_DIR
│   ├── docker\                Docker Desktop 磁盘镜像新位置（GUI 里改）
│   ├── cache\                 TORCH_HOME / HF_HOME / XDG_CACHE_HOME
│   └── tmp\                   TMP / TEMP
├── workspace\                 ← 代码仓（git init）
│   ├── README.md
│   ├── .env                   API key（gitignore）
│   ├── .gitignore
│   ├── run.sh / run.bat
│   ├── config\                harness.yaml / pricing.yaml / whitelist_*.yaml
│   ├── harness\               §3.3 的模块
│   ├── baseline\              官方初始算法（只读拷贝，待获取）
│   ├── algorithms\{partition_placement,full_flow}\
│   ├── results\ logs\         运行产物
│   ├── tools\                 probe_models.py 等辅助脚本
│   └── tests\                 mock 模式单元测试
└── third_party\               Open3DBench / OpenROAD 源码或镜像导出（待获取）
```

### 5.1 安装步骤（按顺序，脚本已放在 `workspace\env\`）

1. **`env\00_setenv.bat`** —— 把 CONDA_PKGS_DIRS / PIP_CACHE_DIR / TMP / TORCH_HOME 等指向 F 盘；每次开终端先 `call` 它
2. **`env\01_create_conda.bat`** —— `conda create --prefix F:\...\env\conda\tao python=3.10`，然后 `pip install -r requirements-harness.txt`
3. **`env\02_clone_torch_env.bat`**（可选，约 6–8 GB）—— `conda create --prefix ...\tao-eda --clone weixing`
4. **Docker Desktop 磁盘迁移（GUI 手动）** —— Settings → Resources → Advanced → Disk image location → `F:\huawei_aic_tao\task1\env\docker`，Apply & Restart。D 盘只剩 26 GB，官方容器（PyTorch + DREAMPlace + OpenROAD）很可能 10–20 GB，不迁必爆
5. **拿到官方容器后** —— `docker load` 或 `docker pull`，`env\03_docker_run.bat` 会把 `workspace\` 挂进容器并开 GPU
6. **若官方容器迟迟未到** —— 备选：在容器里从 GitHub 拉 Open3DBench（含 OpenROAD 与 DREAMPlace 的构建脚本）自行构建，脚本见 `env\04_build_open3dbench.sh`（需要你本机有外网）

---

## 6. 里程碑

| # | 里程碑 | 产出 | 门禁 |
|---|---|---|---|
| M0 | 准备（本轮） | PLAN.md、目录骨架、env 脚本、LLM 客户端、mock 评估器 | 你审核通过 |
| M1 | 环境可用 | conda 环境建好；DeepSeek 连通（1 次调用）；Docker 迁到 F 并能跑 hello-world + GPU | `tools/check_env.py` 全绿 |
| M2 | 基线可跑 | 官方容器内跑通 1 个算例 1 个种子的任务一全流程；记录耗时、指标格式、日志格式 | 有 `baseline_report.json` |
| M3 | harness v1（mock） | 六步循环在 mock 评估器 + 回放 LLM 下跑满 30 轮不崩；logs/ 结构符合提交要求 | pytest 通过 |
| M4 | harness v1（真实） | 接官方评估接口，小预算（3–5 次评估）真跑；修 gate 漏网的失败类型 | 至少 1 个候选有效且不劣于基线 |
| M5 | 任务一冲分 | 完整 30 次预算运行 ≥ 2 次（取最优）；形成 candidate.patch | 训练集均分 > 0 |
| M6 | 任务二跑通 | 白名单扩到 6 环节，溢出门槛生效 | 全部有效、溢出 ≤ 300 |
| M7 | 初赛打包 | README / patch / results / logs / 技术报告 | 对照 §1.6 清单逐项勾 |

---

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 官方容器 / 评估接口迟迟拿不到 | M2 以后全部阻塞 | 先用 mock 把 harness 做完（M3）；并行准备 Open3DBench 自建 |
| 单次评估太慢（9 次流程） | 30 次预算跑不完 | gate 冒烟测试拦错；候选"批量"概念——一次评估可携带多个独立小改动做消融？（视接口而定） |
| DeepSeek-Flash 写代码能力弱、diff 格式错 | 候选大量无效 | SEARCH/REPLACE 格式 + 一次自动修复 + 函数级片段而非全文 |
| 过拟合训练集 | 验证集 / 隐藏算例掉分 | 选择指标用 min / mean−σ；禁止算例名进 prompt |
| D 盘爆满 | Docker 挂 | 立刻迁移磁盘镜像到 F |
| API 费超 15 元 | 官方循环直接停 | 影子计量 + 每轮 ≤ 0.3 元上限 + KV-cache 友好的提示词 |
| 随机性（API 与 EDA 流程） | 复跑结果波动 | 组委会多次复跑取最优；我们本地也多跑取稳健版本 |

---

## 8. 需要你确认 / 提供的事项

**已解决（2026-11-17）**：模型名 = `deepseek-flash`；API 可达；Open3DBench 仓库 = `lamda-bbo/Open3DBench`，镜像 `shiyunqi/open3dbench:eval` / `:place`；两层建模方式已确认（见 `docs/open3dbench_notes.md`）。

**仍待你 / 组委会**：
1. **官方开发容器**与**评估接口文档**（如何提交候选、返回什么、本地冒烟是否计入 30 次）
2. 任务一的"一次时序优化"是 `repair_design` 还是 `repair_timing -setup`？采样 json key 是什么？（`open3dbench_notes.md` §2 差异表第 1、2 条，**最影响策略**）
3. 官方**初始算法基线值**是否直接给出
4. 官方模型代理的接口形式（是否 OpenAI 兼容、是否同样按缓存命中打折、thinking 是否允许关）
5. 官方评测环境是否提供 GPU
6. **Docker Desktop 磁盘迁到 `F:\huawei_aic_tao\task1\env\docker`**（GUI 操作），然后跑 `workspace\env\05_pull_open3dbench.bat`
7. 本机 `nvidia-smi` 输出；`where git`
8. 基线违例量为 0 时改善比例如何定义（向组委会答疑提问）
9. 是否允许修改 GRT 的 layer adjustment / Makefile 级参数（`DONT_USE_CELLS`、`PLACE_DENSITY`、`CTS_*`、`GLOBAL_ROUTE_ARGS`）——这些是最便宜的旋钮，但可能被视为"改评估口径"

---

## 9. 进度记录

### 2026-11-17 · M0 完成
- 赛题 md / pdf / zip 全文精读，三份一致；独立 agent 对照原文复核规划并修正 6 处错误
- 本机 D 盘环境盘点：anaconda + 两个带 torch 的 env、Docker Desktop (WSL2)、NVIDIA 驱动痕迹；沙箱无网无 torch
- `workspace/`：harness 骨架（loop / proposer / gate / evaluator / scoring / selector / store / budget / llm）+ mock 评估器 + fake baseline
- `harness/openroad_parse.py` + `profile.py`：OpenROAD 报告解析（wns/tns、关键路径块、GRT 拥塞表、repair 统计、失败探测、ps→ns）与问题画像；`LogDirEvaluator` 可直接吃原始日志目录
- `harness/prompts/knowledge.md`：注入系统提示的领域知识块
- `docs/`：OpenROAD 参考、旋钮目录、提交清单
- 25 个测试全绿；git 仓库根建在项目根，已配远端

### 2026-11-17 晚 · 联网后的核实
- DeepSeek：模型 `deepseek-flash`，thinking 默认开（已在 llm.py 关掉并记录 reasoning_tokens），缓存命中生效；一次真实 propose 0.002 元、格式全对、patch 可 git apply
- Open3DBench 源码精读（`lamda-bbo/Open3DBench` v1.1）：一个设计 20 层金属、`_bottom/_upper` 双套 cell、HBT 为 CORE 宏；LoL 评估流程 = 读合并 DEF → 分层 detailed_placement → GRT → DRT → finish；DREAMPlace fork 已内置时序驱动网权重但基线没开；MoL 里有 GIN 对比学习切分（宏级）
- OpenROAD 源码核对：`repair_timing` 完整选项（含 `-sequence` 修复顺序）、rsz 日志与进度表真实格式、GRT 表头 "Total Congestion"——解析器与样例已更新
- 新增 `docs/open3dbench_notes.md`、`env/05-07_*.bat`（拉镜像 / 克隆 / 本机跑 bp_fe）、`tools/calibrate_pricing.py`
- 摘录 `third_party/open3dbench_ref/`（15 MB，不入库）

### 2026-11-17 深夜 · 真实代码上的 harness 验证与 30 轮彩排
- `workspace/baseline/` 用 Open3DBench 真实源码（DREAMPlace fork + GIN 切分 + 流程 Tcl/Makefile）搭成占位基线（不入库）；两份白名单改为真实路径；门禁加 .mk 语法检查
- `openroad_parse.parse_orfs_metrics`：ORFS 指标 json（`<stage>__timing__setup__ws/wns/tns`，key 名已从 OpenROAD `src/Metrics.tcl` 核实）；`LogDirEvaluator` json 优先、文本兜底，任务一取 `placeopt` 阶段、任务二取 `globalroute`
- harness 强化：
  - **precheck**：SEARCH 必须逐字出现在本轮展示过的代码片段里，否则不花评估直接要求修复（模型会凭记忆编造上游 DREAMPlace 的代码，这条把首次通过率从 1/5 提到 5/6）
  - **父代叠加**：有父代时展示父代工作副本的代码，并把父代 patch 改过的行强制排进展示窗口（否则 combine 阶段 SEARCH 必然对不上基线）
  - 类按方法分块 + 命中窗口截取；每文件最多 3 块；probe 阶段按旋钮菜单轮换检索焦点
  - 静态检查只报候选**新引入**的问题（基线自带的 `__file__` 等不背锅）
  - full_flow 溢出 > 300 不入精英集；历史摘要带 Analyzer 结论、gate_failed 标为"未测试"而非"假设失败"；费用自适应缩放代码片段；CLI `--max-evaluations/--max-cost/--evaluator/--run-dir`；单轮崩溃不中断；`summary.json`
  - `tools/report.py`：运行目录 → 技术报告用 Markdown（预算、得分曲线、候选表、基线 vs 最优、失败分布、血统树）
- **30 轮彩排**（真实 deepseek-flash × 真实代码 × mock 评估器，`docs/rehearsal_report_R30_mock.md`）：37 候选 / 28 次评估（+2 保留）/ **0.19 元** / 76 次调用；gate 拦 9、无效 8、有效 20；最优 c024 +0.715（mock 分数无物理意义，但血统树显示 structural→combine 的"组合已验证机制"行为符合设计）。彩排暴露并修掉的问题：基线自带 undefined name 误杀、父代改动区域不在展示窗口、围栏/加粗导致的协议解析失败
- `third_party/Open3DBench/`（319 MB，不入库）：完整流程代码 + 平台 LEF/LIB + 网表；**基准 DEF/布局输入不在 git**，需从 Place-LoL/README.md 的 Google Drive 链接下载 `binaries.tar.gz`（沙箱不可达，要你本机下）
- `env/07_run_lol_bpfe.bat` + `tools/collect_m2.py`：一键跑 bp_fe 评估并回收真实日志/json 校正解析器

### 下一步（按优先级）
1. 你本机：`05_pull_open3dbench.bat` → 下载 `binaries.tar.gz` 解压到 `third_party\Open3DBench\Place-LoL\binaries\` → `07_run_lol_bpfe.bat` → `python tools\collect_m2.py`（M2：真实耗时 + 真实 json key）
2. 向组委会问 §8 第 2、9 条（"一次时序优化"是 repair_design 还是 repair_timing；Makefile 变量是否可改）
3. 拿到官方容器：对照 `docs/open3dbench_notes.md` §2 差异表，填真实白名单，接评估接口，M4 小预算真跑
4. 决赛向：harness 自演化（让智能体改 prompts/ 与 selector 参数，用 mock 评估离线做消融）
