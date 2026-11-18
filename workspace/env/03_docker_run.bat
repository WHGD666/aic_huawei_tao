@echo off
chcp 65001 >nul
REM ============================================================
REM  03_docker_run.bat -- 启动官方开发容器，把 workspace 挂进去
REM  前置: 1) Docker Desktop 磁盘镜像已迁到 F:\huawei_aic_tao\task1\env\docker
REM        2) 已 docker load / pull 官方镜像，并把镜像名填到下面 IMAGE
REM ============================================================
call "%~dp000_setenv.bat"

REM TODO: 拿到官方镜像后修改
set IMAGE=open3dbench:official
set CONTAINER=tao-dev

docker image inspect %IMAGE% >nul 2>&1
if errorlevel 1 (
  echo [03] image %IMAGE% not found. Load it first:  docker load -i xxx.tar
  exit /b 1
)

docker ps -a --format "{{.Names}}" | findstr /x %CONTAINER% >nul
if not errorlevel 1 (
  echo [03] container exists, attaching ...
  docker start -ai %CONTAINER%
  exit /b 0
)

echo [03] creating container %CONTAINER% from %IMAGE%
docker run -it --name %CONTAINER% ^
  --gpus all ^
  --shm-size=8g ^
  -v "%TAO_WS%":/workspace ^
  -v "%TAO_ROOT%\third_party":/third_party ^
  -v "%TAO_ENV%\cache":/root/.cache ^
  -w /workspace ^
  --env-file "%TAO_WS%\.env" ^
  %IMAGE% /bin/bash
