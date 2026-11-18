@echo off
REM ============================================================
REM  07_run_lol_bpfe.bat -- M2：在 open3dbench:eval 容器里跑一次 bp_fe 的 LoL 后端评估
REM  目的：拿到真实的阶段耗时、日志与 json 指标格式，替换 tests/samples/ 并校正解析器
REM
REM  前置：
REM    1) Docker Desktop 磁盘已迁到 F 盘；05_pull_open3dbench.bat 已拉 shiyunqi/open3dbench:eval
REM    2) third_party\Open3DBench 已存在（我已放好代码；也可用 06_clone 重新克隆）
REM    3) 数据：从 Google Drive 下载并解压到指定位置（git 里没有）：
REM         binaries.tar.gz   -> third_party\Open3DBench\Place-LoL\binaries\
REM            链接见 third_party\Open3DBench\Place-LoL\README.md（含 converted_output\default\tcad25\bp_fe.def）
REM         benchmarks_lol.tar.gz -> third_party\Open3DBench\Place-LoL\benchmarks\   （仅复现布局时需要，M2 不需要）
REM ============================================================
call "%~dp000_setenv.bat"
set O3=%TAO_ROOT%\third_party\Open3DBench
set DEF=%O3%\Place-LoL\binaries\converted_output\default\tcad25\bp_fe.def

if not exist "%O3%\OpenROAD-3D\flow\Makefile" ( echo [07] third_party\Open3DBench missing. Run 06_clone_open3dbench.bat & exit /b 1 )
if not exist "%DEF%" (
  echo [07] input DEF not found: %DEF%
  echo [07] Download binaries.tar.gz from the Google Drive link in:
  echo        %O3%\Place-LoL\README.md
  echo      and extract so that the path above exists. Then rerun.
  exit /b 1
)
docker image inspect shiyunqi/open3dbench:eval >nul 2>&1 || ( echo [07] image missing; run 05_pull_open3dbench.bat & exit /b 1 )

REM 只跑 bp_fe：生成一个裁剪版 run 脚本（Linux 换行）
set RUN1=%O3%\OpenROAD-3D\flow\run_lol_bpfe_only.sh
powershell -NoProfile -Command ^
  "$s = Get-Content -Raw '%O3%\OpenROAD-3D\flow\run_lol_iccad2023.sh';" ^
  "$s = $s -replace '(?m)^run_task +\"(?!bp_fe_top).*\r?\n', '';" ^
  "$s = $s -replace \"`r`n\", \"`n\";" ^
  "[IO.File]::WriteAllText('%RUN1%', $s)"

if not exist "%TAO_WS%\logs" mkdir "%TAO_WS%\logs"
echo [07] starting container; full log -> %TAO_WS%\logs\m2_bpfe_lol.log
docker run --rm -it ^
  -v "%O3%\OpenROAD-3D":/workspace/OpenROAD-3D ^
  -v "%O3%\Place-LoL":/workspace/Place-LoL ^
  -v "%TAO_WS%":/workspace/harness_ws ^
  shiyunqi/open3dbench:eval ^
  bash -lc "set -o pipefail; export PLACE_LOL_ROOT=/workspace/Place-LoL && cd /workspace/OpenROAD-3D/flow && chmod +x run_lol_bpfe_only.sh && (time ./run_lol_bpfe_only.sh default tcad25) 2>&1 | tee /workspace/harness_ws/logs/m2_bpfe_lol.log; rc=$?; echo FLOW_EXIT=$rc | tee -a /workspace/harness_ws/logs/m2_bpfe_lol.log; exit $rc"
if errorlevel 1 ( echo [07] flow failed. Check %TAO_WS%\logs\m2_bpfe_lol.log & exit /b 1 )

echo.
echo [07] done. Now collect the real artifacts for parser calibration:
echo      python "%TAO_WS%\tools\collect_m2.py"

REM ---- 可选：用 harness 的评估适配器对"空候选"（= 基线）跑一次任务一截断流程，验证适配器与 LogDirEvaluator ----
REM docker run --rm -it -v "%O3%":/workspace/Open3DBench -v "%TAO_WS%":/workspace/harness_ws shiyunqi/open3dbench:eval ^
REM   bash -lc "cd /workspace/harness_ws && mkdir -p /tmp/empty && python3 tools/evaluate_open3dbench.py --workdir /tmp/empty --out baseline/results/partition_placement --task partition_placement --cases bp_fe-ng45 --seeds 1001 --o3 /workspace/Open3DBench --method tcad25 --inplace"
