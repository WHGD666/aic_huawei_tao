@echo off
REM ============================================================
REM  01_create_conda.bat -- 创建 harness 轻量环境（prefix 在 F 盘）
REM  依赖: D:\anaconda 已存在
REM ============================================================
call "%~dp000_setenv.bat"

set ENV_PREFIX=%TAO_ENV%\conda\tao
if exist "%ENV_PREFIX%\python.exe" (
  echo [01] env already exists: %ENV_PREFIX%
) else (
  echo [01] creating conda env at %ENV_PREFIX% ...
  "%CONDA_EXE%" create -y --prefix "%ENV_PREFIX%" python=3.10
  if errorlevel 1 ( echo [01] conda create FAILED & exit /b 1 )
)

echo [01] installing harness requirements ...
"%ENV_PREFIX%\python.exe" -m pip install --upgrade pip
"%ENV_PREFIX%\python.exe" -m pip install -r "%TAO_WS%\requirements-harness.txt"
if errorlevel 1 ( echo [01] pip install FAILED & exit /b 1 )

echo [01] verifying ...
"%ENV_PREFIX%\python.exe" "%TAO_WS%\tools\check_env.py"
echo [01] done. Activate with:   conda activate %ENV_PREFIX%
