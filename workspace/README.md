# 赛题 1 工作区 —— Agent Harness for 3D-IC Physical Design Evolution

> 总体规划、题目解读与里程碑见上一级目录的 **`../PLAN.md`**。本文件只讲"怎么跑"。

## 目录

```
workspace/
├── run.sh / run.bat          统一启动入口（提交要求）
├── .env                      DEEPSEEK_API_KEY / BASE_URL / MODEL（已 gitignore）
├── config/
│   ├── harness.yaml          预算、搜索、评估器、门禁配置
│   ├── pricing.yaml          token 单价（元/百万）→ 本地影子计费
│   └── whitelist_*.yaml      两项任务各自允许修改的文件 glob
├── harness/                  演化循环实现
│   ├── loop.py               主循环：提出→生成→检查→评估→分析→选择
│   ├── proposer.py + prompts/ 提示词组织、SEARCH/REPLACE 解析
│   ├── codebase.py           白名单代码索引、函数级分块、片段检索、补丁生成
│   ├── gate.py               免费本地门禁（路径/禁改/语法/未定义名/冒烟）
│   ├── evaluator.py          Mock 评估器 + 官方评估接口适配器（待接）
│   ├── scoring.py            题面第六节计分公式
│   ├── analyzer.py           评估结果 → 规则摘要 → （可选）LLM 解读
│   ├── selector.py           精英集、稳健性指标、父代采样、阶段推进
│   ├── store.py              候选历史：meta.json / candidate.patch / lineage.jsonl
│   ├── budget.py             评估次数 + token + 费用的影子计量
│   └── llm.py                DeepSeek 客户端（仅 requests；支持 record/replay）
├── baseline/                 官方初始算法（只读拷贝，待获取）
├── algorithms/{partition_placement,full_flow}/   最终 candidate.patch 输出位置
├── logs/run_<task>_<time>/   每次运行：harness.log、budget.json、candidates/、lineage_tree.txt
├── tools/check_env.py        环境体检；tools/probe_models.py 列模型 + 1 次最小调用
├── env/0x_*.bat|sh           环境脚本（全部落 F 盘）
└── tests/                    fake_baseline + FakeLLM + MockEvaluator 的单元/端到端测试
```

## 首次使用（Windows）

```bat
call F:\huawei_aic_tao\task1\workspace\env\00_setenv.bat    :: 把缓存/临时目录指到 F 盘
F:\huawei_aic_tao\task1\workspace\env\01_create_conda.bat   :: 建 python3.10 环境到 F:\...\env\conda\tao
conda activate F:\huawei_aic_tao\task1\env\conda\tao
cd /d F:\huawei_aic_tao\task1\workspace
python tools\check_env.py --net          :: 全绿 + deepseek /models 可达
python tools\probe_models.py             :: 看模型名，填回 .env 的 DEEPSEEK_MODEL
python -m pytest tests -q                :: 15 个测试应全部通过
```

## 跑演化循环

```bat
:: mock 评估器 + 真实 LLM（会花钱，先用 --max-rounds 限次）
run.bat partition_placement --max-rounds 3

:: 录制一次后可零费用回放调试
run.bat partition_placement --llm-mode record --max-rounds 3
run.bat partition_placement --llm-mode replay --max-rounds 3

:: 全流程任务
run.bat full_flow
```

Linux / 官方容器内：`./run.sh partition_placement`。

接入官方评估接口：把 `config/harness.yaml` 的 `evaluator.mode` 改为 `official`，填 `official_cmd`（支持 `{workdir}` `{out}` 占位），并把官方基线结果存到 `baseline/baseline_eval.json`；格式见 `harness/evaluator.py::OfficialEvaluator`。

## 产物与提交对应关系

| 提交要求 | 来源 |
|---|---|
| harness/ + run.sh | 本目录 `harness/`、`run.sh` |
| algorithms/*/candidate.patch | 循环结束自动写入 `algorithms/<task>/candidate.patch` + `SELECTED.json` |
| logs/（父子关系/版本/结果/失败原因） | `logs/run_*/candidates/*/meta.json`、`lineage.jsonl`、`lineage_tree.txt`、`eval.json`、`llm/*.json` |
| results/子任务/算例/种子 | 官方评估接口产物，由 evaluator 落到 `results/`（待接） |
