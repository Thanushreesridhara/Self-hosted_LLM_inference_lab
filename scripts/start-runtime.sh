#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 ollama|vllm" >&2
  exit 2
fi

engine="$1"
case "$engine" in
  ollama) other_engine=vllm ;;
  vllm) other_engine=ollama ;;
  *)
    echo "Unsupported runtime: $engine (choose ollama or vllm)" >&2
    exit 2
    ;;
esac

if command -v docker >/dev/null 2>&1; then
  docker_cmd=(docker)
elif command -v docker.exe >/dev/null 2>&1; then
  docker_cmd=(docker.exe)
else
  echo "Docker CLI is unavailable; enable Docker Desktop WSL integration." >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="$repo_root/docker/compose.yaml"

other_container="$("${docker_cmd[@]}" compose -f "$compose_file" \
  --profile "$other_engine" ps -q "$other_engine")"
if [[ -n "$other_container" ]]; then
  "${docker_cmd[@]}" compose -f "$compose_file" \
    --profile "$other_engine" stop "$other_engine"
fi

"${docker_cmd[@]}" compose -f "$compose_file" \
  --profile "$engine" up -d "$engine"
