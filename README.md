# aic_huawei_tao —— "韬"智能杯 EDA 创新大赛 · 赛题 1

面向 3D 芯片物理设计的多阶段算法协同演化：实现一个 **预算感知的 Agent Harness**，让 DeepSeek-V4.1-Flash 在 30 次候选评估 / 15 元 API 预算内，迭代改进 Open3DBench + OpenROAD 流程中的切分、布局（任务一）以及 HBT 分配、时序优化、CTS、全局布线（任务二）算法，以 WNS/TNS 改善比例计分。

| 入口 | 说明 |
|---|---|
| [`PLAN.md`](PLAN.md) | **总控文档**：题目拆解、得分策略、harness 设计、环境、里程碑、风险、待确认事项 |
| [`docs/`](docs/) | OpenROAD 流程参考、旋钮目录、提交清单 |
| [`workspace/`](workspace/) | harness 代码仓（README 讲怎么跑） |
| `1/` | 赛题原文（md / pdf / zip） |
| `env/` `third_party/` | 本机环境与第三方源码，**不入库**，由 `workspace/env/*.bat` 重建 |

## 快速开始（Windows，所有东西落 F 盘）

```bat
call workspace\env\00_setenv.bat
workspace\env\01_create_conda.bat
conda activate F:\huawei_aic_tao\task1\env\conda\tao
cd workspace
python tools\check_env.py --net
python -m pytest tests -q
```

## 协作方式

- 本地工作在 `F:\huawei_aic_tao\task1`，推送用根目录 `git_sync.bat "说明"`（会先检查 `.env` 没被跟踪）
- `workspace/.env` 保存 API key，永不入库
- 大的运行产物（logs/results/candidates/baseline）默认不入库；最终用于提交的那一份手工挑选后再放进 `submission/`
