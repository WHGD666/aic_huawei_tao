@echo off
REM ============================================================
REM  00_setenv.bat  -- 把所有缓存/临时目录指向 F 盘（C 盘零写入）
REM  用法: 每次打开新的 cmd 先执行  call F:\huawei_aic_tao\task1\workspace\env\00_setenv.bat
REM ============================================================
set TAO_ROOT=F:\huawei_aic_tao\task1
set TAO_ENV=%TAO_ROOT%\env
set TAO_WS=%TAO_ROOT%\workspace

REM --- conda / pip 缓存 ---
set CONDA_PKGS_DIRS=%TAO_ENV%\conda-pkgs
set CONDA_ENVS_PATH=%TAO_ENV%\conda
set PIP_CACHE_DIR=%TAO_ENV%\pip-cache
set PIP_NO_WARN_SCRIPT_LOCATION=1

REM --- 各类模型/框架缓存 ---
set TORCH_HOME=%TAO_ENV%\cache\torch
set HF_HOME=%TAO_ENV%\cache\huggingface
set XDG_CACHE_HOME=%TAO_ENV%\cache
set MPLCONFIGDIR=%TAO_ENV%\cache\matplotlib

REM --- 临时目录 ---
set TMP=%TAO_ENV%\tmp
set TEMP=%TAO_ENV%\tmp

REM --- 既有 conda 可执行文件（D 盘 anaconda，仅读取） ---
set CONDA_EXE=D:\anaconda\Scripts\conda.exe
set PATH=D:\anaconda;D:\anaconda\Scripts;D:\anaconda\Library\bin;%PATH%

REM --- 本项目 python 环境（创建后生效） ---
set TAO_PY=%TAO_ENV%\conda\tao\python.exe
set PYTHONPATH=%TAO_WS%

for %%d in ("%CONDA_PKGS_DIRS%" "%PIP_CACHE_DIR%" "%TORCH_HOME%" "%HF_HOME%" "%MPLCONFIGDIR%" "%TMP%") do if not exist %%d mkdir %%d

echo [setenv] TAO_ROOT=%TAO_ROOT%
echo [setenv] conda pkgs  -> %CONDA_PKGS_DIRS%
echo [setenv] pip cache   -> %PIP_CACHE_DIR%
echo [setenv] TMP/TEMP    -> %TMP%
echo [setenv] project py  -> %TAO_PY%
