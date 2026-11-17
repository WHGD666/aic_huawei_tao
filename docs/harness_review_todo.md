# Harness 代码审核清单（2026-11-17 深夜，独立 agent 对抗式审核）

> 状态：**已修 23/23**（2026-11-18，commit 见 git log）。保留本文作为审核记录；#14 为集成项需官方接口确认。
> 审核对象：`workspace/harness/*`、`config/harness.yaml`、白名单。审核视角：预算泄漏、血统正确性、规则合规（不改评估器 / 不特判算例名 / 最终候选全有效 / logs 可追溯）。

## HIGH

| # | 位置 | 问题 | 修法 | 状态 |
|---|---|---|---|---|
| 1 | `loop.py` run 循环 / `budget.py` can_evaluate / `harness.yaml` reserve=2 | **30 次评估里 2 次永远用不掉**：`can_evaluate()` 总是扣 reserve，没人用 `keep_reserve=False`；`phase_for(28)=="final"` 永远到不了，"final" 阶段是死代码 | `reserve_evaluations: 0` 且 phase_plan 总和 = 30；或循环结束后显式加一个用 reserve 的 final 阶段（对最优候选做 1–2 次微调） | ☑ |
| 2 | `loop.py` `_prepare_work` + `_changed_files_in` / `evaluator.py` OfficialEvaluator | **评估器产物污染父代工作副本并被子代继承**：`eval_result.json`、`__pycache__`、日志落在 work/ 里 → 子代 `_changed_files_in` 把它们当改动文件 → 白名单拒绝 → 所有非基线子代 gate_failed → 父代被拉黑 → structural/combine 退化成基线探针。mock 不写 workdir 所以测试没发现 | `_changed_files_in` 只收 `cb._is_allowed(rel)` 的文件；评估前删 `out`；或评估用 work 的临时拷贝 | ☑ |
| 3 | `harness.yaml` evaluator.mode=mock / official_cmd 空 | 默认配置是 mock；若评委切 official 但 cmd 空，`"".format()` 跑空命令，28 次评估全无效全扣费 | `Harness.__init__` 里 mode≠mock 且 cmd 空直接 fail-fast；提交版默认 `official` | ☑ |
| 4 | `codebase.py` git_diff | 硬依赖 `git` 可执行文件，无则 `FileNotFoundError` → 3 次连续崩溃 → 0 评估退出但 LLM 费已花 | 启动时 `shutil.which("git")` 检查；或 `difflib.unified_diff` 兜底（带 a/ b/ 头） | ☑ |

## MED

| # | 位置 | 问题 | 修法 | 状态 |
|---|---|---|---|---|
| 5 | `loop.py` round / `selector.record_outcome` | gate_failed 的子代也会给父代记失败，两次格式错误就把好父代永久拉黑；elite_size=4 全拉黑后 `choose_parent` 返回 None，structural 轮默默从基线出发 | `record_outcome` 里 `if child.status == "gate_failed": return`；`_fail_streak` 持久化（见 #11） | ☑ |
| 6 | `loop.py` round | 无 patch 去重：REPLACE==SEARCH 或与父代/已有候选 patch 相同仍会消耗一次评估 | 对 patch 做 hash，与已有候选相同或相对父代无变化 → 记 gate_failed "no effective change" | ☑ |
| 7 | `loop.py` round | `new_id()` 之后、`store.create()` 之前崩溃（fix 的 `_chat`、git、读文件）→ 无 meta.json/血统事件，propose 的费用无归属；评估后 `charge_evaluation()` 与 `store.update()` 之间崩溃 → 评估已扣、候选卡在 proposed | `new_id()` 后立即 create（status=proposed）；propose 响应一拿到就 save_llm；评估后段 `try/finally: store.update(meta)` | ☑ |
| 8 | `loop.py` run | 连续 gate_failed 没有上限，`consecutive_errors` 只数异常；模型持续违反协议会一直烧 LLM 费不评估 | 默认 `max_rounds = 3 × max_evaluations`；连续 N 次 gate_failed 停 | ☑ |
| 9 | 白名单 + `gate._check_forbidden` | **规则 (b) 未被门禁强制**：白名单放开了按算例的文件（`3D_input_default/*.json`、`designs/nangate45_3D/*/config*.mk`、`fastroute.tcl`），改其中一个就是按算例特判；禁改模式只 warn 且无算例名模式；`history_digest` 还把算例名喂给模型 | 新增 error 级检查：新增行含 `evaluator.cases` 的任何 token 或 `design_name ==` 字面量 → 拒绝；按算例文件要么从白名单删掉，要么要求对所有算例目录做**完全相同**的改动 | ☑ |
| 10 | `loop.py` `_prepare_work` | 父代 work 目录缺失（被删/部分恢复）时静默建空目录、回退基线，但 meta 仍记 parent=父代 → 血统撒谎 | 用父代 `candidate.patch` `git apply` 重建；或直接 raise | ☑ |
| 11 | `selector.py` `_fail_streak` | 内存态，`--run-dir` 续跑后丢失 | `Selector.__init__` 按 `store.all()` 的 created_at 顺序重放 | ☑ |
| 12 | `pricing.yaml` / `can_call_llm` | 单价是估计值，真实更高则本地少算、可能超 15 元；客户端超时后服务端仍完成的 completion 不会被本地记账 | 正式跑前 `calibrate_pricing.py` 校准；`max_cost_cny` 留余量（如 12） | ☑ |
| 13 | `loop.py` 评估后 | `all_valid` 只看 `candidate_score`（按基线 key 迭代）；评估器返回的额外失败 run 被忽略；`res.all_valid` 从未使用 | `meta.all_valid = score.all_valid and res.all_valid` | ☑ |
| 14 | 集成 | `work/<cid>` 是稀疏覆盖（只有改过的文件），却作为 `{workdir}` 传给 official_cmd；若官方接口要完整树，每次评估都无效 | **拿到官方接口文档后确认**：接受 patch / 覆盖 / 完整树 | ⏳ 待官方接口 |

## LOW

| # | 位置 | 问题 | 修法 | 状态 |
|---|---|---|---|---|
| 15 | `store.new_id` | 按计数生成，删一个目录就会撞 id 覆盖 | `max(int(id[1:]))+1` | ☑ |
| 16 | `codebase.py` write_text / subprocess | Windows 下 LF→CRLF（整文件 diff）、git 输出按本地码页解码（中文乱码 → git apply 失败） | `write_text(..., newline="")`；subprocess `encoding="utf-8"` | ☑ |
| 17 | `loop.py` run | `KeyboardInterrupt` 跳过 `finalize()` → 无 summary/lineage_tree | `try/finally` | ☑ |
| 18 | `codebase.apply_edits` | 无路径穿越保护（`file=../../harness/budget.py`），目前靠 precheck 挡；SEARCH 未命中时 `continue` 但其它 edit 仍写入 → gate_failed 候选的 patch 记录了半应用状态 | 拒绝 `..`/绝对路径；任一 SEARCH 失败则整体不写 | ☑ |
| 19 | `proposer.precheck` | 依赖 `last_chunks` 与 fix 轮的 build_messages 恰好一致（目前确定性成立，脆弱） | `build_messages` 返回 chunks，显式传给 precheck | ☑ |
| 20 | `proposer.EDIT_RE` | SEARCH/REPLACE 内部若有顶格的 `===`/`>>>`（RST 下划线、doctest）会截断 | 分隔符要求整行恰为 `===` | ☑ |
| 21 | `evaluator.py` mock 表 / `openroad_parse.detect_time_unit` 的 asap7 启发 / `harness.yaml` cases | 算例名只出现在 harness 侧，不影响候选代码，但评委 grep 会问 | 单位只从 `set_cmd_units`/Liberty 推断；mock 表改成通用 | ☑ |
| 22 | `prompts/phase_final.md` | 不存在；reserve 置 0 后 final 阶段提示为空 | 写一个"只做最优候选的保守微调"指引 | ☑ |
| 23 | `summary.json` | 规则 (f) "代码版本"：未记录基线树 hash 与 harness commit | finalize 时写入 `baseline_sha256`（白名单文件内容哈希）与 `git rev-parse HEAD` | ☑ |

## 审核确认无问题的点

- `evaluator.evaluate()` 与 `charge_evaluation()` 严格配对（含异常路径）；所有 LLM 调用经 `_chat` → `can_call_llm()`；429/5xx 重试不计费
- `choose_parent`/`best()` 只从 `status=="evaluated" and all_valid` 里选，invalid/gate_failed 永远当不了父代或最优
- `candidate.patch` 是"基线 → 子代"的累计 diff，`git apply -p1` 于基线根可用；`a/ b/` 头强制重写
- LLM 无法新建/删除文件；glob 匹配 `fullmatch` 锚定
- `budget.json` 续跑可恢复；`new_id` 正常情况下不撞；`harness.log`/`lineage.jsonl` 追加写
- 提示词明文禁止算例名特判；`PROBE_KEYWORDS` 是符号名
- `candidate_score` 任一基线 key 缺失/无效 → `-inf`


## 修复说明（2026-11-18）

- **#1** reserve 改为"主循环后的 final 阶段"真正用掉：`run()` 结束主循环后若 `reserve>0` 且有最优候选，`selector.force_phase="final"`，用 `can_evaluate(keep_reserve=False)` 做保守微调；`prompts/phase_final.md` 新增；phase_plan 与 max_evaluations 不一致时自动按比例缩放并记 WARNING
- **#2** `_prepare_work` 不再复制父代目录，而是把父代 `candidate.patch` 用 `git apply` 打到干净基线文件上（只含白名单文件）；`_changed_files_in` 只收 `cb._is_allowed()` 的文件。测试 `test_work_dir_ignores_evaluator_artifacts` 模拟评估器往 work/ 写 json 与 pyc
- **#3** `Harness.__init__` 对 `mode in (official, logdir)` 且 cmd 为空抛 `ConfigError`，CLI 返回码 2；`--official-cmd` 可命令行覆盖
- **#4** `git_diff` 无 git 时走 `_difflib_diff`（输出带 `diff --git` 头，经 `git apply` 验证可用）；loop 记录 `have_git`
- **#5** `record_outcome` 对 `gate_failed` 直接返回
- **#6** `patch_fingerprint`（只看 hunk 内容，忽略 index/mode/行号）去重；与父代/基线无差异也拒绝；去重信息作为 fix 反馈
- **#7** `new_id()` 后立即 `store.create(meta)`（status=proposed），propose/fix 响应即时 `save_llm`；评估后段 `try/finally: store.update`
- **#8** `max_rounds` 默认 `3×max_evaluations`；`budget.max_consecutive_gate_failures`（默认 6）连续门禁失败即停
- **#9** `Gate(case_names=...)`：新增行含算例/电路名 token（含短名在字符串字面量里）→ 错误；按 PDK 名分支 → 错误；注释提及 → 警告。白名单新增 `per_design` 组，同组文件改动必须完全一致（added-lines 集合比较）
- **#10** 父代 work 缺失时用 patch 重建；patch 打不上且 work 也没有 → raise（不再静默回退基线）
- **#11** `Selector.__init__` 重放 store 恢复 `_fail_streak`
- **#12** `max_cost_cny` 默认 12（留 20%）；calibrate_pricing 仍需正式跑前执行
- **#13** `meta.all_valid = score.all_valid and res.all_valid`
- **#15** `new_id` 取 max+1
- **#16** `write_text(newline="")`、subprocess `encoding="utf-8"`；git_diff 去掉 `old mode/new mode/index` 行（挂载盘 chmod 不稳定导致同内容 patch 不同）
- **#17** `run()` 外层 `try/finally: finalize()`，`KeyboardInterrupt` 也落 summary
- **#18** `apply_edits` 原子写入 + 路径穿越拒绝；precheck 也拒
- **#19** `build_messages` 返回 `(msgs, chunks)`，显式传给 `precheck`；precheck 放宽：SEARCH 若逐字存在于当前真实文件（唯一匹配）即放行，只拦"既没展示又不在真实代码里"的编造
- **#20** `EDIT_RE` 分隔符要求整行 `===` / `>>>`
- **#21** `detect_time_unit` 不再按 `asap7` 字样猜 ps，只认 Liberty `time_unit` / `set_cmd_units`
- **#22** `prompts/phase_final.md`
- **#23** `summary.json["code_version"]`：harness git HEAD、白名单文件内容 sha256、模型名、评估器模式
