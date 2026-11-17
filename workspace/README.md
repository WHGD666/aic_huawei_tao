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
│   ├── evaluator.py          Mock 评估器 + 官方 JSON 接口适配器 + LogDirEvaluator（直接吃原始日志目录）
│   ├── openroad_parse.py     OpenROAD/OpenSTA 报告解析：wns/tns、关键路径块、GRT 拥塞表、repair 统计、失败探测、ps→ns
│   ├── profile.py            评估日志 → "问题画像"（slack 直方图、top 路径、跨层比例、缓冲器统计）给 Proposer
│   ├── scoring.py            题面第六节计分公式
│   ├── analyzer.py           评估结果 → 规则摘要 → （可选）LLM 解读
│   ├── selector.py           精英集、稳健性指标、父代采样、阶段推进
│   ├── store.py              候选历史：meta.json / candidate.patch / lineage.jsonl
│   ├── budget.py             评估次数 + token + 费用的影子计量
│   └── llm.py                DeepSeek 客户端（仅 requests；支持 record/replay）
├── baseline/                 占位基线 = Open3DBench 真实源码摘录（不入库；官方初始算法到手后整体替换，见 baseline/README_BASELINE.md）
├── algorithms/{partition_placement,full_flow}/   最终 candidate.patch 输出位置
├── logs/run_<task>_<time>/   每次运行：harness.log、budget.json、candidates/、lineage_tree.txt
├── tools/                    check_env 体检 / probe_models 模型名 / calibrate_pricing 单价反推 / report 运行报告 / collect_m2 回收真实日志
├── env/0x_*.bat|sh           环境脚本（全部落 F 盘）：00 setenv / 01 conda / 02 torch / 03 官方容器 / 05 拉 Open3DBench 镜像 / 06 克隆 / 07 跑 bp_fe
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
python -m pytest tests -q                :: 32 个测试应全部通过（无 pytest 时: python tests\_runner_nopytest.py tests.test_units tests.test_parse tests.test_e2e tests.test_logdir_eval）
```

## 跑演化循环

```bat
:: mock 评估器 + 真实 LLM（30 轮约 0.2 元）；--run-dir 指定目录可中断后续跑
run.bat partition_placement --evaluator mock --llm-mode record --run-dir logs\my_run --max-rounds 10

:: 录制过的 prompt 可零费用回放
run.bat partition_placement --llm-mode replay --run-dir logs\my_run

:: 官方预算口径（题面：读取指定任务与预算）
run.bat full_flow --max-evaluations 30 --max-cost 15

:: 运行目录 -> 技术报告 Markdown
python tools\report.py logs\my_run -o logs\my_run\report.md
```

彩排样例：`logs/rehearsal/R30_pp_mock/`（真实 deepseek-flash × 真实 Open3DBench 代码 × mock 评估，28 次评估 0.19 元），报告见 `../docs/rehearsal_report_R30_mock.md`。

Linux / 官方容器内：`./run.sh partition_placement`。

接入官方评估接口（二选一，改 `config/harness.yaml` 的 `evaluator.mode`）：
- `official`：官方接口返回指标 JSON → 填 `official_cmd`（支持 `{workdir}` `{out}` 占位），基线结果存 `baseline/baseline_eval.json`；格式见 `harness/evaluator.py::OfficialEvaluator`
- `logdir`：官方接口只留下每个 `<case>/<seed>/` 的原始日志与报告 → `LogDirEvaluator` 用 `openroad_parse.py` 自己提取；基线日志放 `baseline/results/<task>/`
拿到真实日志后，把它们放进 `tests/samples/` 替换手写样例，保证 `tests/test_parse.py` 仍然通过。

## 产物与提交对应关系

| 提交要求 | 来源 |
|---|---|
| harness/ + run.sh | 本目录 `harness/`、`run.sh` |
| algorithms/*/candidate.patch | 循环结束自动写入 `algorithms/<task>/candidate.patch` + `SELECTED.json` |
| logs/（父子关系/版本/结果/失败原因） | `logs/run_*/candidates/*/meta.json`、`lineage.jsonl`、`lineage_tree.txt`、`eval.json`、`llm/*.json` |
| results/子任务/算例/种子 | 官方评估接口产物，由 evaluator 落到 `results/`（待接） |
