# Reproduction entry points. See README.md for the full walkthrough.
KUBECONFIG ?= $(HOME)/.kube/config
export KUBECONFIG
KPS_VERSION  := 92.15.0
DCGM_VERSION := 4.8.4
NVDP_VERSION := 0.20.1
PY ?= python3

.PHONY: test lint helm-validate docker-ollama docker-vllm bench-ollama bench-vllm \
        k8s-gpu-plugin k8s-vllm k8s-monitoring k8s-dashboard bench-k8s grafana-password

test:
	$(PY) -m pytest -q benchmark/tests
	$(PY) -m pytest -q mcp/tests

lint:
	yamllint .

# Render upstream charts with this repo's values and schema-check the output.
helm-validate:
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
	helm repo add gpu-helm-charts https://nvidia.github.io/dcgm-exporter/helm-charts
	helm repo update
	rm -rf .chart-lint && mkdir .chart-lint
	helm pull prometheus-community/kube-prometheus-stack --version $(KPS_VERSION) --untar --untardir .chart-lint
	helm pull gpu-helm-charts/dcgm-exporter --version $(DCGM_VERSION) --untar --untardir .chart-lint
	helm lint --strict .chart-lint/kube-prometheus-stack -f k8s/monitoring/kube-prometheus-stack-values.yaml
	helm lint --strict .chart-lint/dcgm-exporter -f k8s/monitoring/dcgm-exporter-values.yaml
	rm -rf .chart-lint
	helm template kps prometheus-community/kube-prometheus-stack --version $(KPS_VERSION) -n monitoring \
	  -f k8s/monitoring/kube-prometheus-stack-values.yaml | kubeconform -ignore-missing-schemas -summary
	helm template dcgm gpu-helm-charts/dcgm-exporter --version $(DCGM_VERSION) -n monitoring \
	  -f k8s/monitoring/dcgm-exporter-values.yaml | kubeconform -ignore-missing-schemas -summary

# --- Phase 1: Docker (one engine at a time on the GPU) ---
docker-ollama:
	./scripts/start-runtime.sh ollama
	docker exec llm-lab-ollama ollama pull qwen2.5:7b-instruct-q4_K_M

docker-vllm:
	./scripts/start-runtime.sh vllm

bench-ollama:
	$(PY) -m benchmark.bench --engine ollama --software-version 0.40.13 \
	  --image ollama/ollama@sha256:b2f0122c476bc9fe7ce679d8bb4bd555351e25e2554eefb156f61cb0a89cf429 \
	  --base-url http://localhost:11435/v1 --model qwen2.5:7b-instruct-q4_K_M

bench-vllm:
	$(PY) -m benchmark.bench --engine vllm --software-version 0.32.0 \
	  --image vllm/vllm-openai@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34 \
	  --base-url http://localhost:8000/v1 --model qwen2.5-7b-awq

# --- Phase 2: Kubernetes (after scripts/setup-k3s-wsl.sh) ---
k8s-gpu-plugin:
	helm repo add nvdp https://nvidia.github.io/k8s-device-plugin
	helm upgrade -i nvdp nvdp/nvidia-device-plugin -n nvidia-device-plugin --create-namespace \
	  --version $(NVDP_VERSION) --set runtimeClassName=nvidia
	# The chart only schedules on NFD-labelled nodes; this cluster has no NFD.
	kubectl label node --all nvidia.com/gpu.present=true --overwrite

k8s-vllm:
	kubectl apply -f k8s/vllm/00-namespace.yaml
	kubectl apply -f k8s/vllm/10-pvc.yaml -f k8s/vllm/20-deployment.yaml -f k8s/vllm/30-service.yaml
	@echo "If pods cannot download weights, pre-load the cache: see docs/pod-network-download-issue.md"

k8s-monitoring:
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
	helm repo add gpu-helm-charts https://nvidia.github.io/dcgm-exporter/helm-charts
	helm repo update
	helm upgrade -i kps prometheus-community/kube-prometheus-stack -n monitoring --create-namespace \
	  --version $(KPS_VERSION) -f k8s/monitoring/kube-prometheus-stack-values.yaml
	helm upgrade -i dcgm gpu-helm-charts/dcgm-exporter -n monitoring \
	  --version $(DCGM_VERSION) -f k8s/monitoring/dcgm-exporter-values.yaml
	kubectl apply -f k8s/monitoring/vllm-servicemonitor.yaml

k8s-dashboard:
	kubectl apply -f k8s/monitoring/dashboard-configmap.yaml

bench-k8s:
	kubectl -n llm-inference port-forward svc/vllm 8001:8000 & PF=$$!; sleep 4; \
	$(PY) -m benchmark.bench --engine vllm --software-version "0.32.0 (k3s)" \
	  --image vllm/vllm-openai@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34 \
	  --base-url http://localhost:8001/v1 --model qwen2.5-7b-awq --output-dir results/k8s; \
	kill $$PF

grafana-password:
	@kubectl -n monitoring get secret kps-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo
