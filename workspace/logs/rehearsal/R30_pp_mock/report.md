# Harness run report — `R30`

## 预算消耗

| 评估次数 | API 费用 (CNY) | LLM 调用 | prompt tok (cache hit) | completion tok | 候选总数 | 有效评估 | gate 拦截 | 无效 |
|---|---|---|---|---|---|---|---|---|
| 28 | 0.190 | 76 | 279020 (162944) | 28736 | 37 | 20 | 9 | 8 |

## 演化效率：第 k 次评估后的最优稳健分 / 总分

| k | 候选 | 本轮总分 | 本轮稳健分 | 累计最优总分 | 累计最优稳健分 |
|---|---|---|---|---|---|
| 1 | c003 | — | — | — | — |
| 2 | c004 | -0.049 | -0.020 | -0.049 | -0.020 |
| 3 | c005 | — | — | -0.049 | -0.020 |
| 4 | c006 | -0.074 | -0.061 | -0.049 | -0.020 |
| 5 | c007 | +0.490 | +0.126 | +0.490 | +0.126 |
| 6 | c009 | +0.457 | +0.084 | +0.490 | +0.126 |
| 7 | c010 | +0.590 | +0.181 | +0.590 | +0.181 |
| 8 | c011 | +0.487 | +0.138 | +0.590 | +0.181 |
| 9 | c012 | +0.588 | +0.170 | +0.590 | +0.181 |
| 10 | c013 | +0.574 | +0.135 | +0.590 | +0.181 |
| 11 | c014 | — | — | +0.590 | +0.181 |
| 12 | c015 | +0.270 | +0.057 | +0.590 | +0.181 |
| 13 | c016 | +0.509 | +0.149 | +0.590 | +0.181 |
| 14 | c018 | — | — | +0.590 | +0.181 |
| 15 | c020 | +0.516 | +0.095 | +0.590 | +0.181 |
| 16 | c021 | +0.592 | +0.178 | +0.592 | +0.181 |
| 17 | c022 | — | — | +0.592 | +0.181 |
| 18 | c023 | +0.574 | +0.188 | +0.592 | +0.188 |
| 19 | c024 | +0.715 | +0.211 | +0.715 | +0.211 |
| 20 | c025 | +0.650 | +0.172 | +0.715 | +0.211 |
| 21 | c026 | +0.591 | +0.148 | +0.715 | +0.211 |
| 22 | c027 | +0.574 | +0.188 | +0.715 | +0.211 |
| 23 | c031 | — | — | +0.715 | +0.211 |
| 24 | c033 | — | — | +0.715 | +0.211 |
| 25 | c034 | +0.600 | +0.186 | +0.715 | +0.211 |
| 26 | c035 | +0.512 | +0.141 | +0.715 | +0.211 |
| 27 | c036 | +0.587 | +0.146 | +0.715 | +0.211 |
| 28 | c037 | — | — | +0.715 | +0.211 |

## 候选明细

| id | 父代 | 阶段 | 状态 | 总分 | 稳健分 | ariane133-ng45 | bp-ng45 | bp_fe-ng45 | 改动文件 | 假设 |
|---|---|---|---|---|---|---|---|---|---|---|
| c001 | base | probe | gate_failed | — | — | — | — | — |  | Enabling timing-driven net weighting in the analytical placer (timing_opt_flag) makes the placer pull timing-c |
| c002 | base | probe | gate_failed | — | — | — | — | — |  | The graph-cut partitioner's `_compute_swap_gain` weights every cut edge equally (uniform adjacency), so the pa |
| c003 | base | probe | invalid | — | — | — | — | — | PlaceObj.py | The density-weight update rule (hpwl-based) uses a fixed multiplicative band [LOWER_PCOF, UPPER_PCOF] around a |
| c004 | base | probe | evaluated | -0.049 | -0.020 | -0.011 | -0.019 | -0.018 | GNN_partition.py | The graph-cut partitioner's area-balance test uses a single global tolerance `area_balance_error` applied to ` |
| c005 | base | probe | invalid | — | — | — | — | — | PlaceObj.py | The gamma schedule in `update_gamma` is extremely aggressive (coef = 10^((overflow-0.1)*20/9-1)); at overflow≈ |
| c006 | base | probe | evaluated | -0.074 | -0.061 | -0.013 | +0.012 | -0.074 | GNN_partition.py | The baseline GNN partitioner trains on a contrastive loss whose negative pairs are sampled at random and whose |
| c007 | base | probe | evaluated | +0.490 | +0.126 | +0.111 | +0.182 | +0.196 | timing.py | The baseline timing-driven net-weight update caps weights at `max_net_weight` and uses the default `net_weight |
| c008 | base | probe | gate_failed | — | — | — | — | — |  | The graph-cut partitioner's swap-gain is computed with unweighted adjacency, so timing-critical cut edges cost |
| c009 | base | probe | evaluated | +0.457 | +0.084 | +0.083 | +0.244 | +0.129 | PlaceObj.py | The density-weight multiplicative update band `[LOWER_PCOF, UPPER_PCOF]` and its HPWL-ratio exponent control h |
| c010 | c006 | structural | evaluated | +0.590 | +0.181 | +0.203 | +0.212 | +0.175 | GNN_partition.py | The parent's best result came from giving the timing-driven placer broader net weighting (lilith) and a looser |
| c011 | c009 | structural | evaluated | +0.487 | +0.138 | +0.131 | +0.191 | +0.165 | PlaceObj.py | The parent's relaxed density-weight floor (0.5*LOWER_PCOF) helps wirelength/timing but the *asymmetric* band s |
| c012 | c011 | structural | evaluated | +0.588 | +0.170 | +0.214 | +0.214 | +0.160 | PlaceObj.py | The parent's density-weight "damp toward 1 early" is a blunt instrument — it uniformly weakens the density gra |
| c013 | c012 | structural | evaluated | +0.574 | +0.135 | +0.112 | +0.226 | +0.236 | PlaceObj.py | The parent's density-weight relaxations all act on the *global* density weight, but the real TNS cost comes fr |
| c014 | c011 | structural | invalid | — | — | — | — | — | PlaceObj.py | The parent relaxes the *global* density weight but the per-bin density penalty is still driven by a single sca |
| c015 | c013 | structural | evaluated | +0.270 | +0.057 | +0.044 | +0.118 | +0.108 | PlaceObj.py | The parent's density relaxation is gated only on *global* overflow, but the TNS cost comes from cells being pu |
| c016 | c013 | structural | evaluated | +0.509 | +0.149 | +0.141 | +0.186 | +0.182 | PlaceObj.py | The parent's density relaxation is gated only on global overflow and worst-region density, but the TNS/WNS cos |
| c017 | c012 | structural | gate_failed | — | — | — | — | — | PlaceObj.py | The parent relaxes the density weight only when *global* overflow is not rising, but the TNS cost comes from a |
| c018 | c012 | structural | invalid | — | — | — | — | — | PlaceObj.py | The parent relaxes the density weight only when *global* overflow is not rising, but TNS cost comes from *loca |
| c019 | c016 | structural | gate_failed | — | — | — | — | — | PlaceObj.py | The parent gates density relaxation on global overflow, but the real TNS cost is congestion-driven detours tha |
| c020 | c016 | structural | evaluated | +0.516 | +0.095 | +0.198 | +0.250 | +0.068 | PlaceObj.py | The parent's density-weight relaxations all act on the *global* density weight scalar, but the TNS cost comes  |
| c021 | c011 | structural | evaluated | +0.592 | +0.178 | +0.171 | +0.205 | +0.217 | PlaceObj.py | The parent's density relaxation is a single global scalar, but the TNS cost is driven by *regional* density sp |
| c022 | c010 | structural | invalid | — | — | — | — | — | GNN_partition.py | The parent's contrastive loss already down-weights non-critical edges (via `_edge_timing_weights`), but the *a |
| c023 | c010 | structural | evaluated | +0.574 | +0.188 | +0.196 | +0.188 | +0.190 | GNN_partition.py | The parent's timing-weighted contrastive loss pulls critical neighbors together, but the *area-balance repair* |
| c024 | c010 | structural | evaluated | +0.715 | +0.211 | +0.271 | +0.203 | +0.240 | GNN_partition.py | The GNN contrastive loss uses a single global edge-weight tensor, but the *area-balance repair* then flips nod |
| c025 | c021 | structural | evaluated | +0.650 | +0.172 | +0.209 | +0.166 | +0.275 | PlaceObj.py | The parent caps *how high* the per-bin density weight can grow (4x median) but leaves the masked_scatter updat |
| c026 | c023 | combine | evaluated | +0.591 | +0.148 | +0.128 | +0.229 | +0.234 | GNN_partition.py | Combine the two best independent mechanisms — c024's criticality-aware area-balance repair (which protects cri |
| c027 | c023 | combine | evaluated | +0.574 | +0.188 | +0.196 | +0.188 | +0.190 | GNN_partition.py | c024's criticality-aware area repair is the strongest independent mechanism (score +0.715, robust +0.211) and  |
| c028 | c010 | combine | gate_failed | — | — | — | — | — | GNN_partition.py | The parent (c023/c027) already uses criticality-weighted contrastive loss and criticality-aware area repair; t |
| c029 | c010 | combine | gate_failed | — | — | — | — | — | GNN_partition.py | Combine c023/c027's proven criticality-weighted contrastive loss (already in parent) with a modest fine-tune o |
| c030 | c010 | combine | gate_failed | — | — | — | — | — | GNN_partition.py | Fine-tuning the best candidate (criticality-weighted contrastive loss + criticality-aware area repair) by mode |
| c031 | c023 | combine | invalid | — | — | — | — | — | GNN_partition.py | Recent gate failures suggest the parent patch's `_macro_criticality` attribute path is fragile; make the criti |
| c032 | c024 | combine | gate_failed | — | — | — | — | — | GNN_partition.py | The parent's criticality-weighted contrastive loss is sound, but the fragile `_macro_criticality` attribute pa |
| c033 | c023 | combine | invalid | — | — | — | — | — | GNN_partition.py | The parent's criticality-aware area repair shrinks critical macros' *effective* area by a fixed 3.0 coefficien |
| c034 | c024 | combine | evaluated | +0.600 | +0.186 | +0.195 | +0.186 | +0.219 | GNN_partition.py | The best validated candidate (c024, score +0.715, robust +0.211) combined criticality-weighted contrastive los |
| c035 | c034 | combine | evaluated | +0.512 | +0.141 | +0.206 | +0.171 | +0.135 | GNN_partition.py | The parent's criticality penalty in the area-balance repair uses `max(1.0, area)` which over-weights large mac |
| c036 | c027 | combine | evaluated | +0.587 | +0.146 | +0.196 | +0.256 | +0.135 | GNN_partition.py | c024 (score +0.715) validated criticality-weighted contrastive loss + criticality-aware area repair; c034's x2 |
| c037 | c027 | combine | invalid | — | — | — | — | — | GNN_partition.py | The parent's criticality-aware area repair uses a fixed shrink factor 3.0 on critical macros' effective area;  |

## 基线 vs 最优候选 `c024`（每算例每种子）

| case/seed | base WNS | best WNS | ΔWNS% | base TNS | best TNS | ΔTNS% | overflow |
|---|---|---|---|---|---|---|---|
| ariane133-ng45/1001 | -0.824 | -0.633 | +23.1 | -978.5 | -517.0 | +47.2 | 107 |
| ariane133-ng45/1002 | -0.776 | -0.835 | -7.6 | -921.5 | -666.9 | +27.6 | 110 |
| ariane133-ng45/1003 | -0.800 | -0.587 | +26.7 | -950.0 | -639.4 | +32.7 | 45 |
| bp-ng45/1001 | -0.567 | -0.478 | +15.5 | -494.4 | -353.6 | +28.5 | 127 |
| bp-ng45/1002 | -0.533 | -0.471 | +11.8 | -465.6 | -365.4 | +21.5 | 157 |
| bp-ng45/1003 | -0.550 | -0.541 | +1.7 | -480.0 | -324.5 | +32.4 | 157 |
| bp_fe-ng45/1001 | -0.360 | -0.356 | +1.2 | -123.6 | -60.4 | +51.2 | 173 |
| bp_fe-ng45/1002 | -0.339 | -0.287 | +15.6 | -116.4 | -107.0 | +8.0 | 111 |
| bp_fe-ng45/1003 | -0.350 | -0.309 | +11.8 | -120.0 | -69.6 | +42.0 | 113 |

## 失败原因分布

| 原因 | 次数 |
|---|---|
| stage timeout | 14 |
| KeyError in partition | 13 |
| legalization overlap | 11 |
| HBT min-spacing violation | 10 |
| SEARCH block does not appear verbatim in the shown | 6 |
| no files changed | 2 |
| check 'file=' header line, a '<<<SEARCH' line, a ' | 1 |

## 血统树

```
c001 [gate_failed]   --    Enabling timing-driven net weighting in the analytical place
c002 [gate_failed]   --    The graph-cut partitioner's `_compute_swap_gain` weights eve
c003 [    invalid]   --    The density-weight update rule (hpwl-based) uses a fixed mul
c004 [  evaluated] -0.049  The graph-cut partitioner's area-balance test uses a single 
c005 [    invalid]   --    The gamma schedule in `update_gamma` is extremely aggressive
c006 [  evaluated] -0.074  The baseline GNN partitioner trains on a contrastive loss wh
  c010 [  evaluated] +0.590  The parent's best result came from giving the timing-driven 
    c022 [    invalid]   --    The parent's contrastive loss already down-weights non-criti
    c023 [  evaluated] +0.574  The parent's timing-weighted contrastive loss pulls critical
      c026 [  evaluated] +0.591  Combine the two best independent mechanisms — c024's critica
      c027 [  evaluated] +0.574  c024's criticality-aware area repair is the strongest indepe
        c036 [  evaluated] +0.587  c024 (score +0.715) validated criticality-weighted contrasti
        c037 [    invalid]   --    The parent's criticality-aware area repair uses a fixed shri
      c031 [    invalid]   --    Recent gate failures suggest the parent patch's `_macro_crit
      c033 [    invalid]   --    The parent's criticality-aware area repair shrinks critical 
    c024 [  evaluated] +0.715  The GNN contrastive loss uses a single global edge-weight te
      c032 [gate_failed]   --    The parent's criticality-weighted contrastive loss is sound,
      c034 [  evaluated] +0.600  The best validated candidate (c024, score +0.715, robust +0.
        c035 [  evaluated] +0.512  The parent's criticality penalty in the area-balance repair 
    c028 [gate_failed]   --    The parent (c023/c027) already uses criticality-weighted con
    c029 [gate_failed]   --    Combine c023/c027's proven criticality-weighted contrastive 
    c030 [gate_failed]   --    Fine-tuning the best candidate (criticality-weighted contras
c007 [  evaluated] +0.490  The baseline timing-driven net-weight update caps weights at
c008 [gate_failed]   --    The graph-cut partitioner's swap-gain is computed with unwei
c009 [  evaluated] +0.457  The density-weight multiplicative update band `[LOWER_PCOF, 
  c011 [  evaluated] +0.487  The parent's relaxed density-weight floor (0.5*LOWER_PCOF) h
    c012 [  evaluated] +0.588  The parent's density-weight "damp toward 1 early" is a blunt
      c013 [  evaluated] +0.574  The parent's density-weight relaxations all act on the *glob
        c015 [  evaluated] +0.270  The parent's density relaxation is gated only on *global* ov
        c016 [  evaluated] +0.509  The parent's density relaxation is gated only on global over
          c019 [gate_failed]   --    The parent gates density relaxation on global overflow, but 
          c020 [  evaluated] +0.516  The parent's density-weight relaxations all act on the *glob
      c017 [gate_failed]   --    The parent relaxes the density weight only when *global* ove
      c018 [    invalid]   --    The parent relaxes the density weight only when *global* ove
    c014 [    invalid]   --    The parent relaxes the *global* density weight but the per-b
    c021 [  evaluated] +0.592  The parent's density relaxation is a single global scalar, b
      c025 [  evaluated] +0.650  The parent caps *how high* the per-bin density weight can gr
```

## Analyzer 摘录（最优链）

- **c006** (probe, -0.074): The baseline GNN partitioner trains on a contrastive loss whose negative pairs are sampled at random and whose positive pairs ignore edge weights; shortening the training schedule (fewer epochs) is risky, but a safer attributable probe is to make the embedding smoother/more clusterable by raising the GIN depth from 3 to 2 layers — no, instead we take a clean single-knob probe on the training schedule: reduce `num_epochs` default from 100 to 40 so the contrastive loss does not over-fit to random negative pairs (which the baseline never refreshes), yielding embeddings that better reflect local connectivity and thus fewer critical-net cuts / more balanced tiers.
  - The hypothesis did not hold: shortening training to 40 epochs slightly worsened the aggregate score (total −0.074, robust −0.061), with all cases valid. The clearest regression is bp_fe-ng45 (score −0.074, dTNS −11.2%), while bp-ng45 marginally improved (+0.012). This suggests fewer epochs underfit the contrastive objective rather than reducing overfitting to stale random negatives, degrading timing on the most sensitive case. Next step: sweep intermediate schedules (e.g., 60–80 epochs) or refresh negative pairs periodically, isolating whether the issue is schedule length or negative-sampling staleness.
- **c010** (structural, +0.590): The parent's best result came from giving the timing-driven placer broader net weighting (lilith) and a looser density-weight band; the untested complement is to make the GNN partition itself timing-aware by weighting positive (same-partition-desired) contrastive pairs by edge timing criticality, so timing-critical neighbors are pulled into the same embedding cluster and thus more likely onto the same tier (short, same-tier paths that rsz can repair with bottom-tier buffers).
  - The hypothesis held: making the GNN partition timing-aware improved all three cases (total +0.590, robust +0.181, all valid). Gains concentrate in WNS/TNS: bp-ng45 dWNS +10.3%/dTNS +28.1%, bp_fe-ng45 dWNS +21.3%, ariane133-ng45 dTNS +30.6%, with overflow modest (~105–132). Mechanism is consistent—timing-critical neighbors co-clustered onto the same tier, yielding short same-tier paths repairable by bottom-tier buffers. Next step: sweep the criticality weighting exponent (and cap) to test whether stronger timing bias further improves WNS without inflating overflow, and verify on additional seeds.
- **c024** (structural, +0.715): The GNN contrastive loss uses a single global edge-weight tensor, but the *area-balance repair* then flips nodes by pure area delta, potentially undoing critical-pair co-location; by making the swap-gain prefer moving *non-critical* (low timing-weight) nodes and penalize breaking critical nets, we keep timing-critical logic co-located while still meeting area balance — reducing TNS without WNS regressions.
  - Hypothesis held: all three cases improved, with dTNS gains (+27.5% to +36.0%) exceeding dWNS gains (+9.4% to +14.4%), consistent with the mechanism—preferring non-critical swaps preserves critical-pair co-location while area balance is still met. Overflow (~87–147) suggests some congestion cost worth monitoring. Next step: sweep the criticality penalty weight and log how many critical nets are broken per repair pass, to confirm the mechanism directly and find the TNS/overflow sweet spot.