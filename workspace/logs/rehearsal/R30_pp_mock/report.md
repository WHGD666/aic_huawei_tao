# Harness run report — `R2`

## 预算消耗

| 评估次数 | API 费用 (CNY) | LLM 调用 | prompt tok (cache hit) | completion tok | 候选总数 | 有效评估 | gate 拦截 | 无效 |
|---|---|---|---|---|---|---|---|---|
| 30 | 0.170 | 66 | 209505 (94848) | 23004 | 31 | 24 | 1 | 6 |

## 演化效率：第 k 次评估后的最优稳健分 / 总分

| k | 候选 | 本轮总分 | 本轮稳健分 | 累计最优总分 | 累计最优稳健分 |
|---|---|---|---|---|---|
| 1 | c002 | -0.107 | -0.107 | -0.107 | -0.107 |
| 2 | c003 | +0.188 | -0.007 | +0.188 | -0.007 |
| 3 | c004 | -0.077 | -0.077 | +0.188 | -0.007 |
| 4 | c005 | -0.030 | -0.074 | +0.188 | -0.007 |
| 5 | c006 | -0.121 | -0.083 | +0.188 | -0.007 |
| 6 | c007 | +0.423 | +0.099 | +0.423 | +0.099 |
| 7 | c008 | +0.039 | -0.020 | +0.423 | +0.099 |
| 8 | c009 | — | — | +0.423 | +0.099 |
| 9 | c010 | +0.312 | +0.081 | +0.423 | +0.099 |
| 10 | c011 | +0.453 | +0.098 | +0.453 | +0.099 |
| 11 | c012 | — | — | +0.453 | +0.099 |
| 12 | c013 | +0.619 | +0.190 | +0.619 | +0.190 |
| 13 | c014 | +0.136 | +0.031 | +0.619 | +0.190 |
| 14 | c015 | +0.404 | +0.057 | +0.619 | +0.190 |
| 15 | c016 | +0.412 | +0.105 | +0.619 | +0.190 |
| 16 | c017 | — | — | +0.619 | +0.190 |
| 17 | c018 | +0.505 | +0.060 | +0.619 | +0.190 |
| 18 | c019 | +0.323 | +0.081 | +0.619 | +0.190 |
| 19 | c020 | — | — | +0.619 | +0.190 |
| 20 | c021 | +0.947 | +0.278 | +0.947 | +0.278 |
| 21 | c022 | +0.848 | +0.173 | +0.947 | +0.278 |
| 22 | c023 | +0.773 | +0.219 | +0.947 | +0.278 |
| 23 | c024 | +0.744 | +0.208 | +0.947 | +0.278 |
| 24 | c025 | +0.894 | +0.254 | +0.947 | +0.278 |
| 25 | c026 | +0.761 | +0.237 | +0.947 | +0.278 |
| 26 | c027 | +0.876 | +0.272 | +0.947 | +0.278 |
| 27 | c028 | — | — | +0.947 | +0.278 |
| 28 | c029 | +0.774 | +0.215 | +0.947 | +0.278 |
| 29 | c030 | +0.826 | +0.230 | +0.947 | +0.278 |
| 30 | c031 | — | — | +0.947 | +0.278 |

## 候选明细

| id | 父代 | 阶段 | 状态 | 总分 | 稳健分 | ariane133-ng45 | bp-ng45 | bp_fe-ng45 | 改动文件 | 假设 |
|---|---|---|---|---|---|---|---|---|---|---|
| c001 | base | probe | gate_failed | — | — | — | — | — |  | The baseline contrastive partition loss ignores edge weights, so timing-critical nets get cut across tiers; we |
| c002 | base | probe | evaluated | -0.107 | -0.107 | -0.136 | +0.004 | +0.025 | graph_cut.py | The swap-gain computation in the graph-cut partitioner recomputes cut contributions from raw adjacency but dou |
| c003 | base | probe | evaluated | +0.188 | -0.007 | +0.076 | +0.141 | -0.028 | PlaceObj.py | Lowering the density-weight growth aggressiveness in the overflow-based update (clamping `density_weight_new`  |
| c004 | base | probe | evaluated | -0.077 | -0.077 | -0.009 | +0.027 | -0.095 | GNN_partition.py | The area-balance tolerance of the partitioner (area_balance_error=0.1) is quite tight, forcing the partitioner |
| c005 | base | probe | evaluated | -0.030 | -0.074 | -0.061 | -0.048 | +0.080 | PlaceObj.py | The wirelength model's gamma schedule is the single strongest lever on how tightly cells cluster (small gamma  |
| c006 | base | probe | evaluated | -0.121 | -0.083 | -0.097 | -0.031 | +0.007 | GNN_partition.py | The GNN partitioner is under-trained at the fixed 100 epochs / lr=1e-3 with a contrastive objective that never |
| c007 | base | probe | evaluated | +0.423 | +0.099 | +0.194 | +0.140 | +0.090 | timing.py | Enabling timing-driven net weighting is the baseline-most lever (P-L1) and is currently off; turning it on wit |
| c008 | c004 | structural | evaluated | +0.039 | -0.020 | +0.059 | -0.013 | -0.007 | GNN_partition.py | The best single probe was enabling timing-driven net weighting (c007, +0.423). Building on c004's looser area  |
| c009 | c008 | structural | invalid | — | — | — | — | — | GNN_partition.py | Building on c007 (timing-driven net weighting enabled, +0.423), the partitioner currently only balances total  |
| c010 | c003 | structural | evaluated | +0.312 | +0.081 | +0.088 | +0.088 | +0.136 | PlaceObj.py | The parent (c003) clamps the per-region density weight at 5 and slows its growth, which eases buffer insertion |
| c011 | c007 | structural | evaluated | +0.453 | +0.098 | +0.200 | +0.077 | +0.176 | timing.py | The parent (c007) enables timing-driven net weighting but relies on the default `momentum_decay_factor` for th |
| c012 | c011 | structural | invalid | — | — | — | — | — | timing.py | Building on c011 (timing-driven weighting + decay<=0.5), lower the momentum decay further to 0.25 so criticali |
| c013 | c011 | structural | evaluated | +0.619 | +0.190 | +0.212 | +0.184 | +0.223 | timing.py | Building on c011 (timing-driven weighting + decay<=0.5, score +0.453), the remaining lever is the *upper bound |
| c014 | c010 | structural | evaluated | +0.136 | +0.031 | +0.062 | +0.027 | +0.047 | PlaceObj.py | Building on c013 (timing-driven weighting + decay<=0.5 + max_net_weight=8, score +0.619), the per-region densi |
| c015 | c013 | final | evaluated | +0.404 | +0.057 | +0.025 | +0.185 | +0.194 | timing.py | Building on c013 (score +0.619, decay<=0.5, max_net_weight<=8), raise the finite weight cap modestly from 8 to |
| c016 | c013 | final | evaluated | +0.412 | +0.105 | +0.122 | +0.108 | +0.182 | timing.py | The best candidate (c013) clamps the per-net weight cap at 8.0; c015 already tested relaxing it to 11.2, so in |
| c017 | c016 | final | invalid | — | — | — | — | — | timing.py | The best candidate (c016, cap 5.6) is TNS-heavy; nudge the per-net weight cap modestly upward (x1.4 -> 7.84) t |
| c018 | c016 | final | evaluated | +0.505 | +0.060 | +0.087 | +0.096 | +0.322 | timing.py | Building on c016 (cap 5.6, TNS-heavy, score +0.412), modestly tighten the cap by x0.7 to 3.92 so weighting spr |
| c019 | c007 | final | evaluated | +0.323 | +0.081 | +0.075 | +0.107 | +0.141 | timing.py | Building on c018 (cap 3.92, TNS-heavy, score +0.505, best so far), the cap is already the only active lever in |
| c020 | c011 | final | invalid | — | — | +0.160 | +0.096 | +0.148 | timing.py | Building on the validated best candidate c018 (decay<=0.5, max_net_weight cap 3.92), the only active knob is t |
| c021 | c011 | structural | evaluated | +0.947 | +0.278 | +0.365 | +0.308 | +0.274 | timing.py | The best parent (c011/c018 lineage) already clamps momentum decay to 0.5 and caps per-net weight; the remainin |
| c022 | c021 | combine | evaluated | +0.848 | +0.173 | +0.270 | +0.155 | +0.423 | timing.py | c021 (decay<=0.5, ignore_net_degree halved, adam scheme) is the best robust candidate; combining it with a *mo |
| c023 | c021 | combine | evaluated | +0.773 | +0.219 | +0.219 | +0.311 | +0.244 | timing.py | The best parent c021 (decay<=0.5, ignore_net_degree halved, adam scheme) is robust; combining it with a modest |
| c024 | c023 | combine | evaluated | +0.744 | +0.208 | +0.200 | +0.299 | +0.245 | timing.py | c021 (decay<=0.5, ignore_net_degree halved, adam scheme) is the strongest robust parent; combining it with the |
| c025 | c024 | combine | evaluated | +0.894 | +0.254 | +0.245 | +0.354 | +0.295 | timing.py | c024 (decay<=0.5, ignore_net_degree halved, adam scheme, per-net weight cap 7.84) is the strongest robust cand |
| c026 | c023 | combine | evaluated | +0.761 | +0.237 | +0.267 | +0.231 | +0.264 | timing.py | c025 (decay<=0.5, ignore_net_degree halved, adam scheme, per-net weight cap 15.68) is the strongest robust can |
| c027 | c023 | combine | evaluated | +0.876 | +0.272 | +0.302 | +0.309 | +0.265 | timing.py | c025 (per-net weight cap 15.68) is the strongest robust parent; modestly relaxing the cap by x2 to ~31.36 lets |
| c028 | c027 | combine | invalid | — | — | — | — | — | timing.py | c027's cap of 31.36 is the strongest robust parent; a modest x0.5 tightening to 15.68 (the c025 value) combine |
| c029 | c025 | combine | evaluated | +0.774 | +0.215 | +0.311 | +0.258 | +0.205 | timing.py | c025's cap of 15.68 is the strongest robust parent (robust +0.254, min +0.245); a modest x0.5 tightening to 7. |
| c030 | c027 | final | evaluated | +0.826 | +0.230 | +0.326 | +0.217 | +0.283 | timing.py | c027 (cap 31.36) is the strongest robust parent; a modest x0.7 tightening of the per-net weight cap to ~21.95  |
| c031 | c025 | final | invalid | — | — | — | — | — | timing.py | The best parent (c025, cap 15.68, decay<=0.5, halved ignore_net_degree) is strongest; a conservative x1.4 rela |

## 基线 vs 最优候选 `c021`（每算例每种子）

| case/seed | base WNS | best WNS | ΔWNS% | base TNS | best TNS | ΔTNS% | overflow |
|---|---|---|---|---|---|---|---|
| ariane133-ng45/1001 | -0.824 | -0.676 | +17.9 | -978.5 | -506.4 | +48.3 | 161 |
| ariane133-ng45/1002 | -0.776 | -0.629 | +19.0 | -921.5 | -672.1 | +27.1 | 104 |
| ariane133-ng45/1003 | -0.800 | -0.498 | +37.8 | -950.0 | -403.6 | +57.5 | 139 |
| bp-ng45/1001 | -0.567 | -0.401 | +29.2 | -494.4 | -256.7 | +48.1 | 78 |
| bp-ng45/1002 | -0.533 | -0.439 | +17.6 | -465.6 | -302.1 | +35.1 | 136 |
| bp-ng45/1003 | -0.550 | -0.411 | +25.3 | -480.0 | -371.4 | +22.6 | 179 |
| bp_fe-ng45/1001 | -0.360 | -0.249 | +30.9 | -123.6 | -93.8 | +24.1 | 132 |
| bp_fe-ng45/1002 | -0.339 | -0.271 | +20.1 | -116.4 | -70.7 | +39.2 | 110 |
| bp_fe-ng45/1003 | -0.350 | -0.261 | +25.6 | -120.0 | -93.1 | +22.4 | 118 |

## 失败原因分布

| 原因 | 次数 |
|---|---|
| legalization overlap | 11 |
| stage timeout | 7 |
| KeyError in partition | 7 |
| HBT min-spacing violation | 6 |
| check 'file=' header line, a '<<<SEARCH' line, a ' | 1 |

## 血统树

```
c001 [gate_failed]   --    The baseline contrastive partition loss ignores edge weights
c002 [  evaluated] -0.107  The swap-gain computation in the graph-cut partitioner recom
c003 [  evaluated] +0.188  Lowering the density-weight growth aggressiveness in the ove
  c010 [  evaluated] +0.312  The parent (c003) clamps the per-region density weight at 5 
    c014 [  evaluated] +0.136  Building on c013 (timing-driven weighting + decay<=0.5 + max
c004 [  evaluated] -0.077  The area-balance tolerance of the partitioner (area_balance_
  c008 [  evaluated] +0.039  The best single probe was enabling timing-driven net weighti
    c009 [    invalid]   --    Building on c007 (timing-driven net weighting enabled, +0.42
c005 [  evaluated] -0.030  The wirelength model's gamma schedule is the single stronges
c006 [  evaluated] -0.121  The GNN partitioner is under-trained at the fixed 100 epochs
c007 [  evaluated] +0.423  Enabling timing-driven net weighting is the baseline-most le
  c011 [  evaluated] +0.453  The parent (c007) enables timing-driven net weighting but re
    c012 [    invalid]   --    Building on c011 (timing-driven weighting + decay<=0.5), low
    c013 [  evaluated] +0.619  Building on c011 (timing-driven weighting + decay<=0.5, scor
      c015 [  evaluated] +0.404  Building on c013 (score +0.619, decay<=0.5, max_net_weight<=
      c016 [  evaluated] +0.412  The best candidate (c013) clamps the per-net weight cap at 8
        c017 [    invalid]   --    The best candidate (c016, cap 5.6) is TNS-heavy; nudge the p
        c018 [  evaluated] +0.505  Building on c016 (cap 5.6, TNS-heavy, score +0.412), modestl
    c020 [    invalid]   --    Building on the validated best candidate c018 (decay<=0.5, m
    c021 [  evaluated] +0.947  The best parent (c011/c018 lineage) already clamps momentum 
      c022 [  evaluated] +0.848  c021 (decay<=0.5, ignore_net_degree halved, adam scheme) is 
      c023 [  evaluated] +0.773  The best parent c021 (decay<=0.5, ignore_net_degree halved, 
        c024 [  evaluated] +0.744  c021 (decay<=0.5, ignore_net_degree halved, adam scheme) is 
          c025 [  evaluated] +0.894  c024 (decay<=0.5, ignore_net_degree halved, adam scheme, per
            c029 [  evaluated] +0.774  c025's cap of 15.68 is the strongest robust parent (robust +
            c031 [    invalid]   --    The best parent (c025, cap 15.68, decay<=0.5, halved ignore_
        c026 [  evaluated] +0.761  c025 (decay<=0.5, ignore_net_degree halved, adam scheme, per
        c027 [  evaluated] +0.876  c025 (per-net weight cap 15.68) is the strongest robust pare
          c028 [    invalid]   --    c027's cap of 31.36 is the strongest robust parent; a modest
          c030 [  evaluated] +0.826  c027 (cap 31.36) is the strongest robust parent; a modest x0
  c019 [  evaluated] +0.323  Building on c018 (cap 3.92, TNS-heavy, score +0.505, best so
```

## Analyzer 摘录（最优链）

- **c007** (probe, +0.423): Enabling timing-driven net weighting is the baseline-most lever (P-L1) and is currently off; turning it on with the default `adam`-style scheme lets the analytical placer pull critical nets together more strongly, which should reduce many moderate violations (TNS) across all circuits without changing partition/legality.
  - The hypothesis held: all three cases are valid and improve, with dWNS and dTNS both positive (e.g., ariane133-ng45 +19.2%/+19.3%, bp-ng45 +12.2%/+15.0%, bp_fe-ng45 +9.4%/+8.7%), consistent with timing-driven net weighting pulling critical nets together and reducing moderate violations without legality issues. Overflow remains nonzero (~103–146), so congestion is a secondary concern. Next step: sweep the net-weighting strength/`adam` parameters and confirm overflow stays bounded, then check whether gains persist at higher utilization.
- **c011** (structural, +0.453): The parent (c007) enables timing-driven net weighting but relies on the default `momentum_decay_factor` for the Adam-style criticality momentum; lowering it makes the criticality signal track the *current* placement more responsively each iteration, so many moderately-critical nets keep meaningful weight instead of being dominated by a few early-identified critical nets — spreading buffer-friendly weight across more violating paths and reducing TNS.
  - The hypothesis held: all three cases improved, with dWNS +10.8–15.6% and dTNS +4.9–25.9%, robust score +0.098 and min_case +0.077. The mechanism fits—lower momentum decay lets criticality track current placement, spreading weight to moderately-critical nets rather than a few early nets, reducing TNS (largest gain on ariane133, +25.9%). Overflow ~90–127 suggests routing pressure worth watching. Next step: sweep `momentum_decay_factor` across a small grid (e.g., 0.5×, 0.25× default) on these cases to confirm monotonic benefit and check overflow/routability doesn't degrade.
- **c021** (structural, +0.947): The best parent (c011/c018 lineage) already clamps momentum decay to 0.5 and caps per-net weight; the remaining TNS lever inside `update_net_weights` is the *degree threshold* used to ignore high-fanout nets — lowering the effective `ignore_net_degree` cap so that moderate-fanout nets (which carry many moderate violations) also receive timing weight, while still skipping true clock/reset high-fanout nets, should spread repair effort and reduce TNS broadly.
  - Hypothesis held: all three cases improved, with dTNS gains (+28–45%) exceeding dWNS (+24–26%), consistent with spreading repair to moderate-fanout nets. bp-ng45 and ariane133-ng45 moved most; bp_fe-ng45 least, plausibly because its violation mix is dominated by true high-fanout nets still skipped. Overflow ~120–135 is stable, so no legality concern. Next step: sweep the effective `ignore_net_degree` cap (e.g., 200/150/100) to find where moderate-fanout weighting saturates before clock/reset nets leak in, and confirm TNS gains persist without overflow regression.