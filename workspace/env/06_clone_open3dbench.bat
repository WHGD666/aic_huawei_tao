@echo off
REM ============================================================
REM  06_clone_open3dbench.bat -- 把 Open3DBench 仓库完整克隆到 third_party/（含 converted_output DEF，约 400MB+）
REM  我这边已经做了一份不含大文件的摘录在 third_party/open3dbench_ref/；这里是完整版，供容器挂载运行
REM ============================================================
call "%~dp000_setenv.bat"
set DST=%TAO_ROOT%\third_party\Open3DBench
if exist "%DST%\.git" (
  echo [06] already cloned, pulling
  cd /d "%DST%" && git pull --ff-only
) else (
  git clone --depth 1 https://github.com/lamda-bbo/Open3DBench.git "%DST%" || exit /b 1
)
echo [06] done -> %DST%
echo [06] Benchmarks (LEF/LIB/DEF inputs) are NOT in git. Download from the Google Drive links in
echo      %DST%\Place-LoL\README.md  and  %DST%\OpenROAD-3D\README.md  and extract as instructed.
echo      If Google Drive is unreachable, ask the organizers' container for the same data.
