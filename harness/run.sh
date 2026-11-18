#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: run.sh CONTEST_SOURCE_DIR {partition_placement|full_flow|both} [start.py options...]" >&2
    exit 2
fi

release=$(realpath "$1")
task=$2
shift 2
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
agents="$script_dir/agent"
if [[ ! -d "$agents" ]]; then
    agents="$script_dir/../agent"
fi

if [[ ! -f "$release/start.py" || ! -f "$release/bundle.json" ]]; then
    echo "Official contest-source release not found: $release" >&2
    exit 2
fi
for name in partition_placement full_flow; do
    if [[ ! -f "$agents/$name/agent.py" ]]; then
        echo "Missing participant Agent: $agents/$name/agent.py" >&2
        exit 2
    fi
    mkdir -p "$release/agent/$name"
    cp -- "$agents/$name/agent.py" "$release/agent/$name/agent.py"
    cp -- "$agents/$name/__init__.py" "$release/agent/$name/__init__.py"
done

cd -- "$release"
case "$task" in
    partition_placement|full_flow)
        python3 start.py --task "$task" "$@"
        ;;
    both)
        python3 start.py --task partition_placement "$@"
        python3 start.py --task full_flow "$@"
        ;;
    *)
        echo "Unknown task: $task" >&2
        exit 2
        ;;
esac
