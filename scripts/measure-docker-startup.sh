#!/usr/bin/env bash
# Time vLLM start-up in Docker (same image/args as docker/compose.yaml) against a given
# Hugging Face cache volume: seconds until the first successful chat completion.
# Usage: measure-docker-startup.sh <volume-name>
# An empty/new volume measures a cold start including the weights download;
# the compose volume (already filled) measures a warm start. Image is already pulled.
set -euo pipefail
vol="$1"
img=vllm/vllm-openai@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34
now() { date +%s.%N; }
t0=$(now)
docker.exe run -d --rm --name llm-lab-startup-test --gpus=all -p 8002:8000 --shm-size 8gb \
  -v "$vol":/root/.cache/huggingface "$img" \
  --model Qwen/Qwen2.5-7B-Instruct-AWQ --served-model-name qwen2.5-7b-awq --quantization awq \
  --max-model-len 4096 --gpu-memory-utilization 0.80 --max-num-seqs 8 >/dev/null
trap 'docker.exe stop llm-lab-startup-test >/dev/null 2>&1 || true' EXIT
fails=0
until curl -sf -m 5 http://localhost:8002/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5-7b-awq","messages":[{"role":"user","content":"Say hi"}],"max_tokens":8}' >/dev/null 2>&1; do
  # Only give up after several consecutive "not running" answers: a single docker.exe
  # call can fail transiently under WSL (vsock errors) and must not abort a long run.
  if [[ "$(docker.exe inspect -f '{{.State.Running}}' llm-lab-startup-test 2>/dev/null | tr -d '\r')" == "false" ]]; then
    fails=$((fails + 1))
  else
    fails=0
  fi
  if (( fails >= 5 )); then echo "container exited" >&2; exit 1; fi
  sleep 1
done
printf '%s,%.1f\n' "$vol" "$(echo "$(now) - $t0" | bc -l)"
