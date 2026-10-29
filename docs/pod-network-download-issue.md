# Hugging Face downloads failing from pods (k3s on WSL2)

**Observed (2026-10-29):** the vLLM pod could not complete its first model download.
- With default settings: `RuntimeError: ... CAS Client Error: Format error: I/O error: error decoding response body` (xet backend), 3 container restarts.
- With `HF_HUB_DISABLE_XET=1`: `httpx ConnectError: [Errno 104] Connection reset by peer` and read timeouts. The PVC held only 13 MB after about 30 minutes.
- The same model downloaded without problems under Docker Desktop on the same machine (Phase 1).

**Not diagnosed:** the root cause (pod network / MTU / WSL2 NAT path were not investigated).

**Workaround used:** copy the Phase 1 Hugging Face cache from the Docker volume into the PVC through a temporary pod (`k8s/tests/cache-loader.yaml`), then run vLLM with `HF_HUB_OFFLINE=1`:

```bash
kubectl apply -f k8s/tests/cache-loader.yaml
docker run --rm -v self-hosted-llm-lab_huggingface-cache:/src:ro alpine tar -C /src -cf - . \
  | kubectl -n llm-inference exec -i cache-loader -- tar -C /cache -xf -
kubectl delete -f k8s/tests/cache-loader.yaml
```

Consequence: the cold-start numbers for the cluster do not include a network download of the weights.
