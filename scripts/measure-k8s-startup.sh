#!/usr/bin/env bash
# Scale the vLLM Deployment to 0, scale to 1, and time (a) pod Ready and
# (b) the first successful chat completion. Warm model cache (PVC already filled).
# Usage: measure-k8s-startup.sh [runs] > results/startup/<file>.csv
set -euo pipefail
runs="${1:-3}"
ns=llm-inference
ip="$(kubectl -n "$ns" get svc vllm -o jsonpath='{.spec.clusterIP}')"
now() { date +%s.%N; }

echo "run,scale_to_zero_seconds,seconds_to_pod_ready,seconds_to_first_completion"
for run in $(seq 1 "$runs"); do
  t=$(now)
  kubectl -n "$ns" scale deploy/vllm --replicas=0 >/dev/null
  while [[ -n "$(kubectl -n "$ns" get pod -l app=vllm -o name)" ]]; do sleep 1; done
  down=$(echo "$(now) - $t" | bc -l)

  t0=$(now)
  kubectl -n "$ns" scale deploy/vllm --replicas=1 >/dev/null
  ready=""
  while true; do
    if [[ -z "$ready" ]]; then
      r="$(kubectl -n "$ns" get pod -l app=vllm -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null || true)"
      [[ "$r" == "true" ]] && ready=$(echo "$(now) - $t0" | bc -l)
    fi
    if curl -sf -m 5 "http://$ip:8000/v1/chat/completions" -H 'Content-Type: application/json' \
         -d '{"model":"qwen2.5-7b-awq","messages":[{"role":"user","content":"Say hi"}],"max_tokens":8}' >/dev/null 2>&1; then
      first=$(echo "$(now) - $t0" | bc -l)
      break
    fi
    sleep 1
  done
  printf '%s,%.1f,%.1f,%.1f\n' "$run" "$down" "${ready:-nan}" "$first"
done
