#!/usr/bin/env bash
# 统一启动入口（提交要求）。用法：
#   ./run.sh partition_placement [extra args]
#   ./run.sh full_flow
# 环境变量来自 .env（DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL）；官方代理只需改 BASE_URL。
set -euo pipefail
cd "$(dirname "$0")"
TASK="${1:-partition_placement}"; shift || true
PY="${PYTHON:-python3}"
exec "$PY" -m harness.loop --task "$TASK" "$@"
