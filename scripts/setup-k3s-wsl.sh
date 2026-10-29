#!/usr/bin/env bash
# One-time host setup for Phase 2: NVIDIA container toolkit, then k3s, then Helm.
# Needs sudo. Read it before running. Steps follow:
#   https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
#   https://docs.k3s.io/advanced#nvidia-container-runtime-support
# The toolkit goes in first because k3s only detects the NVIDIA runtime at start-up.
set -euo pipefail

# 1. NVIDIA container toolkit apt repo + packages (adds /etc/apt sources and a keyring).
sudo apt-get update
sudo apt-get install -y --no-install-recommends ca-certificates curl gnupg2
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
nvidia-ctk --version

# 2. k3s (systemd service; its own containerd). Do NOT run `nvidia-ctk runtime configure`
#    here: that targets a system containerd, and k3s wires up the runtime itself.
curl -sfL https://get.k3s.io | sh -
sudo systemctl is-active k3s
k3s --version

# 3. kubeconfig for your user (no world-readable kubeconfig).
mkdir -p "$HOME/.kube"
sudo cp /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"
sudo chown "$USER:$USER" "$HOME/.kube/config"
chmod 600 "$HOME/.kube/config"

# 4. Confirm k3s picked up the NVIDIA runtime.
sudo grep -n nvidia /var/lib/rancher/k3s/agent/etc/containerd/config.toml | head

# 5. Helm into ~/.local/bin (no sudo).
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 \
  | USE_SUDO=false HELM_INSTALL_DIR="$HOME/.local/bin" bash
"$HOME/.local/bin/helm" version --short
echo "Done. Next: kubectl get nodes"
