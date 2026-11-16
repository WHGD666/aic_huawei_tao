@echo off
REM Windows 版统一入口。用法: run.bat partition_placement [--llm-mode replay] [--max-rounds 5]
call "%~dp0env\00_setenv.bat" >nul
cd /d "%~dp0"
set TASK=%1
if "%TASK%"=="" set TASK=partition_placement
shift
set ARGS=
:loop
if "%~1"=="" goto run
set ARGS=%ARGS% %1
shift
goto loop
:run
"%TAO_PY%" -m harness.loop --task %TASK% %ARGS%
