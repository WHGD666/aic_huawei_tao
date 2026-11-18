@echo off
chcp 65001 >nul
REM ============================================================
REM  05_pull_open3dbench.bat -- 拉取 Open3DBench 官方镜像，用于在官方容器到来前实测流程与日志格式
REM  前置：Docker Desktop 磁盘镜像已迁到 F:\huawei_aic_tao\task1\env\docker（否则 D 盘会爆）
REM  镜像来源：https://github.com/lamda-bbo/Open3DBench  README
REM ============================================================
call "%~dp000_setenv.bat"

docker info >nul 2>&1 || ( echo [05] Docker Desktop not running & exit /b 1 )
for /f "tokens=*" %%i in ('docker info --format "{{.DockerRootDir}}"') do echo [05] docker root = %%i

echo [05] pulling shiyunqi/open3dbench:eval  (OpenROAD + HotSpot; several GB)
docker pull shiyunqi/open3dbench:eval || exit /b 1

echo [05] pulling shiyunqi/open3dbench:place (DREAMPlace + CUDA; optional, larger)
docker pull shiyunqi/open3dbench:place

docker images | findstr open3dbench
echo [05] done. Next: 06_clone_open3dbench.bat to fetch the flow scripts, then 07_run_lol_bpfe.bat
