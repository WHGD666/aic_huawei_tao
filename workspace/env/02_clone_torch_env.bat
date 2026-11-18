@echo off
chcp 65001 >nul
REM ============================================================
REM  02_clone_torch_env.bat -- (可选) 把 D:\anaconda\envs\weixing
REM  (python3.10 + torch2.5.1+cu121) 克隆到 F 盘，用于本地 GNN/布局实验
REM  体积约 6-8 GB；如果只做 harness 开发可以跳过
REM ============================================================
call "%~dp000_setenv.bat"

set SRC_ENV=D:\anaconda\envs\weixing
set ENV_PREFIX=%TAO_ENV%\conda\tao-eda

if exist "%ENV_PREFIX%\python.exe" (
  echo [02] env already exists: %ENV_PREFIX%
  goto :verify
)
echo [02] cloning %SRC_ENV% to %ENV_PREFIX%  (this takes a while)
"%CONDA_EXE%" create -y --prefix "%ENV_PREFIX%" --clone "%SRC_ENV%"
if errorlevel 1 ( echo [02] clone FAILED & exit /b 1 )

:verify
"%ENV_PREFIX%\python.exe" -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available(),torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
echo [02] done.
