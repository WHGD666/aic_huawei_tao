#!/usr/bin/env bash
# ============================================================
#  04_build_open3dbench.sh -- 备选方案：官方容器未到时，在一个
#  通用 CUDA 容器里从源码拉取 Open3DBench（含 OpenROAD + DREAMPlace）
#  在容器内执行；/third_party 已由 03_docker_run.bat 挂载
#  注意：OpenROAD 从源码编译需要 1-2 小时与 ~16GB 内存，优先用官方镜像
# ============================================================
set -euo pipefail
TP=/third_party
mkdir -p "$TP" && cd "$TP"

if [ ! -d Open3DBench ]; then
  git clone --depth 1 https://github.com/lamda-bbo/Open3DBench.git
fi
cd Open3DBench
echo "== repo layout =="; ls
echo "== README head =="; head -80 README.md || true
echo
echo ">>> 按仓库 README 继续（通常是 build OpenROAD + pip install DREAMPlace 依赖）。"
echo ">>> 把真实步骤补回本脚本，保持可复现。"
