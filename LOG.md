# Project log

All measurements and environment details below are observed, not estimated.
Times are local to the WSL2 environment (`2026-10-29`, Europe/London).

## 2026-10-29 — Phase 0: environment discovery

### Environment observed

- OS: Ubuntu 24.04.4 LTS (Noble), x86_64, running on WSL2.
- WSL: 2.7.11.0; kernel 6.18.33.2-microsoft-standard-WSL2.
- GPU: NVIDIA GeForce RTX 5070 Ti; `nvidia-smi` reported 16,303 MiB total memory.
- Driver: KMD 610.88; `nvidia-smi` 610.57.01; CUDA UMD 13.3.
- At inspection: `nvidia-smi` reported 992 MiB GPU memory in use and 3% GPU utilization. No compute process was listed.
- Memory: 15 GiB total, 13 GiB available; swap 4 GiB.
- Disk: 923 GiB available on the workspace filesystem.
- `systemctl is-system-running`: `running`; `/run/systemd/system` exists.
- Docker Desktop Windows CLI: 29.6.2 (API 1.55). The Linux engine was unreachable at inspection.
- Docker CLI, `kubectl`, Helm, k3s, containerd, nerdctl, `ctr`, and `nvidia-ctk` are not available in the Ubuntu distro's PATH.
- Workspace was empty before this log and repository-hygiene files were added.

### Commands run

- `cat /etc/os-release`
- `uname -a`
- `grep -qi microsoft /proc/version`
- `nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader`
- `nvidia-smi`
- `docker --version`
- `docker info --format 'Server={{.ServerVersion}} OS={{.OperatingSystem}} Runtimes={{json .Runtimes}}'`
- `kubectl version --short`, `helm version --short`, `k3s version`
- `df -h .`, `pwd`, `ls -la`
- `systemctl is-system-running`; checked `/run/systemd/system`, `WSL_INTEROP`, and `WSL_DISTRO_NAME`
- Checked command availability/version for `wsl.exe`, `docker.exe`, `containerd`, `nerdctl`, `ctr`, and `nvidia-ctk`
- `free -h`
- `docker.exe version` (client responded; engine connection failed because the Docker Desktop Linux engine pipe was absent)
- `docker.exe info --format 'Server={{.ServerVersion}} OS={{.OperatingSystem}} Runtimes={{json .Runtimes}}'` (engine connection failed)
- `wsl.exe --version`

### Docs checked

- Docker Desktop WSL integration: https://docs.docker.com/desktop/features/wsl/
- Docker Desktop GPU support: https://docs.docker.com/desktop/features/gpu/
- NVIDIA CUDA on WSL: https://docs.nvidia.com/cuda/wsl-user-guide/index.html
- NVIDIA Container Toolkit installation: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
- k3s requirements: https://docs.k3s.io/installation/requirements
- NVIDIA Kubernetes device plugin: https://github.com/NVIDIA/k8s-device-plugin

### Decision and blocker

- Proposed setup: Docker Desktop's WSL2 backend for the Docker baseline, then single-node k3s in this Ubuntu WSL2 distro using its containerd runtime, NVIDIA Container Toolkit, and the NVIDIA device plugin. WSL systemd is active; actual k3s/GPU compatibility remains unverified.
- Docker Desktop was not running/accessible at first inspection. The user started it and enabled Ubuntu integration before the following check.
- No benchmark results or model throughput/latency measurements exist yet.

## 2026-10-29 — Docker GPU smoke test

### Versions observed

- Docker Desktop: 4.84.0 (234817).
- Docker Engine: 29.6.2, Linux/amd64.
- Docker containerd: v2.2.5.
- Docker runc: 1.3.6.
- Docker CLI: 29.6.2, API 1.55.

### Command run

- `docker.exe run --rm --gpus=all nvcr.io/nvidia/k8s/cuda-sample:nbody nbody -gpu -benchmark`

### Result

- Passed. Container reported `NVIDIA GeForce RTX 5070 Ti`, compute capability 12.0, and completed its CUDA sample.
- The sample printed 36.573 ms for ten iterations and 28,097.211 single-precision GFLOP/s. This is only a GPU-access smoke test; it is not an LLM performance result and will not be used in the Ollama/vLLM comparison.
- Pulled image: `nvcr.io/nvidia/k8s/cuda-sample:nbody`, digest `sha256:59261e419d6d48a772aad5bb213f9f1588fcdb042b115ceb7166c89a51f03363`.

## 2026-10-29 — Benchmark harness and model selection

### Official artifacts checked

- Ollama library lists `qwen2.5:7b-instruct-q4_K_M` as an existing 4.7 GB, 32K-context model (library digest prefix `845dbda0ea48`): https://ollama.com/library/qwen2.5/tags
- Hugging Face lists `Qwen/Qwen2.5-7B-Instruct-AWQ`, with 7.61B parameters and AWQ 4-bit quantization: https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-AWQ
- Ollama OpenAI compatibility docs and official Ollama API source document `stream_options.include_usage`, used to read exact completion token counts from streamed responses: https://docs.ollama.com/api/openai-compatibility and https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx
- vLLM stable Docker docs document the `vllm/vllm-openai` image and `latest` tag: https://docs.vllm.ai/en/stable/deployment/docker/
- Docker Hub API reported active Ollama image tag `0.40.13`, manifest digest `sha256:b2f0122c476bc9fe7ce679d8bb4bd555351e25e2554eefb156f61cb0a89cf429`.
- Docker Hub API reported active vLLM `latest`, manifest digest `sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34` (amd64 image digest `sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9`). The image's internal vLLM version has not yet been inspected.
- vLLM stable quantization documentation lists AWQ support and the stable engine-arguments docs were checked before configuring the server: https://docs.vllm.ai/en/stable/features/quantization/ and https://docs.vllm.ai/en/stable/configuration/engine_args/

### Benchmark harness implementation and verification

- Added fixed prompts, an OpenAI-compatible streaming benchmark, CSV/JSON result writing, and tests for SSE parsing and statistics under `benchmark/`.
- The harness requests `stream_options.include_usage`; TTFT is measured from the first non-empty assistant content delta; tokens/sec is completion tokens divided by post-TTFT generation time. If an endpoint omits token usage, the request remains a successful request but its throughput is explicitly unavailable rather than estimated.
- Summary p50 uses median; p95 uses nearest-rank. Latency summaries include successful requests only; failures are reported separately in the failure count/rate.
- First test-environment attempt: `python3 -m venv .venv` failed because Ubuntu's `ensurepip`/`python3-venv` package is absent. No apt/sudo install was performed. `uv` was available, so dependencies were installed into `.venv-test` instead; the failed `.venv` directory was left untouched and ignored.
- `uv --version`: 0.12.2. The test environment used CPython 3.12.12, `requests` 2.34.2, and `pytest` 9.2.0. The distro's `/usr/bin/python3 --version` reported Python 3.12.3.
- Commands run: `python3 -m venv .venv` (failed as above); `uv venv .venv-test --python python3`; `uv pip install --python .venv-test/bin/python -r benchmark/requirements.txt`; `.venv-test/bin/python -m pytest -q benchmark/tests`; `.venv-test/bin/python -m compileall -q benchmark`.
- Verification: 7 tests passed; Python compilation passed.
- No Ollama/vLLM inference benchmark has run yet.

### Docker runtime configuration

- Added digest-pinned Ollama and vLLM services in `docker/compose.yaml`; each requests one NVIDIA GPU using Compose device reservations (`driver: nvidia`, `count: 1`, `capabilities: [gpu]`).
- The vLLM service is configured for the AWQ model with `--max-model-len 4096`, `--gpu-memory-utilization 0.80`, and `--max-num-seqs 8`.
- Added `scripts/start-runtime.sh`; it stops the other Compose service before bringing up the selected runtime.
- Docker's current Compose GPU guide was checked: https://docs.docker.com/compose/how-tos/gpu-support/
- Commands run: `docker.exe compose -f docker/compose.yaml config --quiet`; `bash -n scripts/start-runtime.sh`; invalid runtime argument was tested and returned exit status 2 with a usage error.
- `./scripts/start-runtime.sh ollama` pulled the pinned Ollama image and started the project container. Inside that container, `ollama --version` reported 0.40.13 and `nvidia-smi` reported the RTX 5070 Ti, 16,303 MiB, driver 610.88.
- The expected host-port mapping was absent from the running container's network port state. `curl http://localhost:11434/api/version` and `/api/tags` reached a pre-existing WSL system service instead: `/usr/local/bin/ollama serve`, version 0.32.5, running as systemd unit `ollama.service`, bound to `127.0.0.1:11434`. Its `/api/ps` response showed no loaded model; `/api/tags` listed an existing `qwen2.5-coder:7b` model. At that point `nvidia-smi` reported 1,051 MiB in use and 1% GPU utilization.
- The project container was stopped with `docker.exe compose -f docker/compose.yaml --profile ollama stop ollama`; its model volume was not deleted. The pre-existing service remains untouched. Docker-based Ollama model pull/benchmark is blocked until that service is stopped.
- The user approved a temporary stop. `sudo systemctl stop ollama` was attempted, but sudo could not prompt for credentials in the non-interactive tool (`a terminal is required` / `a password is required`). Read-only verification confirmed `ollama.service` remains active and the project container remains stopped. No password was requested or captured.

## 2026-10-29 — Phase 1 benchmarks (Docker, one engine at a time)

### Port workaround
- The pre-existing system `ollama.service` (0.32.5) holds host port 11434 and stopping it needs sudo, which the tool session cannot prompt for. Instead of stopping it, the project's Ollama container is published on host port 11435 (`docker/compose.yaml`). Confirmed `curl localhost:11435/api/version` returned 0.40.13 (the container). The system service was left untouched.

### Ollama
- Image `ollama/ollama@sha256:b2f0122c...` (0.40.13); model `qwen2.5:7b-instruct-q4_K_M` (GGUF Q4_K_M, digest `845dbda0ea48...`).
- Command: `python -m benchmark.bench --engine ollama ... --base-url http://localhost:11435/v1` (defaults: concurrency 1 2 4 8, 2 repeats, max_tokens 64). Output: `results/ollama_20261029T112426.441496Z.{json,csv}`. 16 requests per level, 0 failures.
- Observation: TTFT p50 rose 0.02 s -> 0.47 -> 1.37 -> 3.14 s at concurrency 1/2/4/8 while per-request tokens/s stayed ~145.5. Consistent with requests being processed serially; the cause (e.g. Ollama's parallelism setting) was not investigated or confirmed.
- Peak VRAM 6,234 MiB is whole-GPU memory from nvidia-smi and includes ~1 GiB already in use by other processes before the run.

### vLLM
- Image `vllm/vllm-openai@sha256:befccf0e...` (vLLM 0.32.0); model `Qwen/Qwen2.5-7B-Instruct-AWQ`, `--quantization awq --max-model-len 4096 --gpu-memory-utilization 0.80 --max-num-seqs 8`.
- Startup from container creation: engine init (profile, KV cache, warmup) took 93.56 s (compilation 11.53 s) after the image/model download; KV cache 116,016 tokens (6.2 GiB). This is a first-start figure including model download, not a clean cold/warm-cache measurement.
- Output: `results/vllm_20261029T121246.269036Z.{json,csv}`. 16 requests per level, 0 failures.
- TTFT p50 0.016 / 0.021 / 0.028 / 0.035 s and e2e p50 0.45-0.47 s at concurrency 1/2/4/8; per-request tokens/s p50 146.5-149.7.
- Peak VRAM 13,486-13,488 MiB (whole GPU; vLLM pre-allocates per --gpu-memory-utilization, so this reflects the setting, not model need).
- Not apples-to-apples: GGUF Q4_K_M (llama.cpp backend) vs AWQ 4-bit (Marlin kernels); different runtimes, and max_tokens 64 only.

## 2026-10-29 — Phase 2 step 1: k3s + GPU in Kubernetes

- Host setup was run by the user (sudo cannot be entered in the tool session) via `scripts/setup-k3s-wsl.sh`, which follows the NVIDIA Container Toolkit and k3s NVIDIA-runtime docs. Toolkit installed before k3s so k3s detects the runtime at start.
- Versions observed: k3s v1.36.5+k3s1 (containerd 2.3.4-k3s1.36), NVIDIA Container Toolkit CLI 1.20.1, Helm v3.22.1. Single node `gurukarthik`, Ready; `nvidia` RuntimeClass present.
- Device plugin: the plugin docs page showed chart 0.17.1, but `helm search repo` listed 0.20.1 as latest; installed `nvdp/nvidia-device-plugin` 0.20.1 in namespace `nvidia-device-plugin` with `--set runtimeClassName=nvidia`.
- Problem: the DaemonSet scheduled 0 pods, because its node affinity requires NFD/GFD labels (`feature.node.kubernetes.io/pci-10de.present`, etc.) and this cluster has no NFD. Fix: manually labelled the node `nvidia.com/gpu.present=true`. The plugin then started, logged "Detected platform: wsl", registered `nvidia.com/gpu`, and the node reported allocatable `nvidia.com/gpu: 1`.
- Verification: `k8s/tests/gpu-smoke.yaml` (runtimeClassName nvidia, limit nvidia.com/gpu: 1) ran `nvidia-smi` and listed the RTX 5070 Ti, 16,303 MiB, driver 610.57.01. Pod deleted afterwards.

## 2026-10-29 — Phase 2 step 2: vLLM on k3s, in-cluster benchmark

- Manifests in `k8s/vllm/`: namespace `llm-inference` + ResourceQuota (1 GPU, 8 CPU, 12 Gi mem, 60 Gi storage), 30 Gi PVC `hf-cache` (local-path), Deployment (Recreate, `runtimeClassName: nvidia`, same vLLM args as Phase 1, startup/readiness/liveness on `/health`), ClusterIP Service. Quota enforcement visible via `kubectl describe resourcequota`.
- Problem 1: Service named `vllm` injected `VLLM_PORT=tcp://...` service-link variables (vLLM warned about unknown `VLLM_*` vars). Set `enableServiceLinks: false` preventively; whether it would have caused a failure was not tested.
- Problem 2: the pod could not finish Hugging Face downloads (xet `CAS Client Error`, then with `HF_HUB_DISABLE_XET=1` `Connection reset by peer`); 3 container restarts, PVC held 13 MB after ~30 min. Root cause not diagnosed (see `docs/pod-network-download-issue.md`). Workaround: copied the 5.2 GB Phase 1 cache from the Docker volume into the PVC via a temporary pod (the partial 13 MB download was deleted first), and set `HF_HUB_OFFLINE=1`.
- With the cache in place the pod became Ready; engine init took 92.72 s (compilation 11.31 s), KV cache 116,016 tokens, identical to the Docker run (93.56 s).
- Benchmark: `kubectl port-forward svc/vllm 8001:8000`, same harness and prompts, output `results/k8s/vllm_20261029T154626.064016Z.{json,csv}`; 0 failures.
  - TTFT p50 0.020 / 0.027 / 0.034 / 0.049 s; e2e p50 0.447 / 0.444 / 0.454 / 0.473 s; tokens/s p50 149.4 / 151.6 / 151.6 / 150.6 at concurrency 1/2/4/8; peak VRAM 13,588 MiB (whole GPU).
  - Compared with Docker (TTFT p50 0.016-0.035 s, tokens/s 146.5-149.7): similar. Differences are small and include the port-forward hop and run-to-run noise; only one run of each was made, so no claim is made about which is faster.

## 2026-10-29 — Phase 2 step 3: monitoring

- Installed `prometheus-community/kube-prometheus-stack` 92.15.0 (namespace `monitoring`) with lean values (`k8s/monitoring/kube-prometheus-stack-values.yaml`): Alertmanager off, resource limits set, ServiceMonitors selected from all namespaces, Grafana admin password generated by the chart (not stored in the repo). Initial hardcoded password was removed before commit.
- node-exporter disabled: its image (`quay.io/prometheus/node-exporter:v1.12.1-distroless`) failed to pull on the node (`CreateContainerError`); host-level metrics are therefore not collected.
- `k8s/monitoring/vllm-servicemonitor.yaml` scrapes vLLM `/metrics` every 15 s. The target was `up`; my first two queries returned nothing only because they ran before the first scrape (and one used unencoded braces in curl).
- DCGM exporter chart 4.8.4 (app 4.8.4, image tag 4.6.1-4.8.4-distroless) with `runtimeClassName: nvidia`. DCGM works under WSL2 here: the exporter served `DCGM_FI_DEV_GPU_UTIL`, `FB_USED`, `FB_FREE`, `GPU_TEMP`, `POWER_USAGE` for the RTX 5070 Ti. `DCGM_FI_DEV_MEMORY_TEMP` reported 0 (likely not supported on this GPU; not investigated).
- Problem: the chart's default `scrapeTimeout: 25s` exceeds the 15 s interval; the prometheus-operator skipped the ServiceMonitor ("scrapeTimeout 25s greater than scrapeInterval"), so no GPU series existed. Fixed with `scrapeTimeout: 10s`; target went `up`.
- Dashboard as code: `dashboards/llm-inference.json` (11 panels: e2e latency p50/p95, TTFT p50/p95, inter-token latency, tokens/s, requests/s, running vs waiting requests, KV-cache usage, GPU utilisation, GPU memory, GPU temperature, GPU power) and ConfigMap `k8s/monitoring/dashboard-configmap.yaml` (label `grafana_dashboard=1`). Grafana's sidecar loaded it (found via the Grafana search API, uid `llm-inference-lab`).
- Verification: after a short load run, each vLLM panel query returned data from Prometheus (e.g. e2e p95 0.758 s over a 5 min window, generated-token rate non-zero). The GPU temperature query returned a series once the target was up. I did not visually inspect the rendered dashboard in a browser.

## 2026-10-29 — Stretch A/B: weight cache and scale-to-zero

- `scripts/measure-k8s-startup.sh 3`: scale vLLM Deployment 0 -> 1 with the weights already in the PVC, via the Service ClusterIP (reachable from the WSL host). Time to first successful chat completion: 144.8, 134.6, 143.8 s; pod Ready within ~0.4 s of that; scale-down to zero 7.9, 3.4, 3.4 s. Data: `results/startup/k8s_warm_pvc_20261029.csv`.
- Docker, warm volume (`scripts/measure-docker-startup.sh`): 138.1 s (`docker_warm_20261029.csv`).
- Docker, empty volume (`lab-cold-empty-cache-2`): 7302.2 s (about 2 h 2 min), almost all weight download (5.2 GB) (`docker_cold_20261029.csv`).
- Problem: the first cold attempt (volume `lab-cold-empty-cache`) was lost. A transient WSL vsock error (`accept4 failed 110`) made one `docker.exe ps` call fail; the script treated that as "container exited" and its trap stopped the container just before the first completion (about 40 min in, judging from log timestamps: weights loaded 17:36 UTC). Script changed to require 5 consecutive "not running" answers; cold run repeated. No figure from the lost run is reported as a result.
- No in-cluster empty-cache measurement exists (pods could not download weights; see `docs/pod-network-download-issue.md`). KEDA/Knative not tried; scaling was manual.
- Left on the machine (not deleted, awaiting user decision): Docker volumes `lab-cold-empty-cache` and `lab-cold-empty-cache-2` (~5.2 GB each). The cluster Deployment is currently scaled to 0.

## 2026-10-29 — Stretch C: GPU time-slicing

- Device plugin reconfigured via `k8s/sharing/device-plugin-timeslicing-values.yaml` (`sharing.timeSlicing` replicas 2); node then advertised `nvidia.com/gpu: 2`. Quota override `k8s/sharing/quota-override.yaml` (2 GPUs).
- Problem 1: pod A at `--gpu-memory-utilization=0.42` crashed (KV cache 0.15 GiB < 0.22 GiB needed for 4,096 tokens). 0.45 started fine for A alone.
- Problem 2: pod B at 0.45 failed with "Available KV cache memory: -6.15 GiB": vLLM 0.32.0 counts the other pod's GPU memory against its own budget. Switched both pods to `--kv-cache-memory-bytes=1073741824` (flag verified with `vllm serve --help=all`).
- Mistake and fix: my `kubectl delete deploy vllm-b` omitted `-n llm-inference` (NotFound), so the crash-looping B kept loading weights on the GPU while I ran the "A alone" baseline (nvidia-smi showed 13.5 GiB instead of A's ~7.7 GiB). That baseline and an earlier 0.45-config baseline were discarded (uncommitted, my own files) and re-run with B scaled to 0; nvidia-smi then showed 7,713 MiB.
- Results (`results/sharing/`): A alone c=1: TTFT p50 0.013 s, 153.1 tok/s, e2e p50 0.431 s; with B also loaded, c=1: 71.7 tok/s, e2e p50 0.912 s (A) and 71.5 tok/s, 0.918 s (B); c=4: 72.8 / 71.2 tok/s, e2e p50 0.928 / 0.946 s. 0 failures. Inside both pods `nvidia-smi` showed 16,303 MiB total and 14,638 MiB used (no isolation).
- Reverted afterwards: test Deployments/Services deleted, plugin helm-upgraded with `--reset-values --set runtimeClassName=nvidia` (allocatable back to 1), original quota re-applied, main `vllm` scaled back to 1.
