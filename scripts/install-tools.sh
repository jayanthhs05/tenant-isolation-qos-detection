#!/usr/bin/env bash
# Installs all Phase 0 CLI tooling as user-local binaries into ./bin — no sudo,
# no system package manager changes. Idempotent: re-running skips tools already
# present at the pinned version.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${ROOT_DIR}/bin"
mkdir -p "${BIN_DIR}"
cd "${BIN_DIR}"

ARCH="$(uname -m)"
case "${ARCH}" in
  x86_64) GOARCH="amd64" ;;
  aarch64|arm64) GOARCH="arm64" ;;
  *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;;
esac
OS="linux"

# Pinned versions — deliberately not "latest", see docs/risk-log.md #4.
KUBECTL_VERSION="v1.31.0"
KIND_VERSION="v0.24.0"
HELM_VERSION="v3.16.1"
CILIUM_CLI_VERSION="v0.16.19"
HUBBLE_VERSION="v1.16.3"
KUBE_BENCH_VERSION="0.9.3"
TRIVY_VERSION="0.74.0"
K6_VERSION="v2.2.0"

log() { echo ">>> $*"; }

install_kubectl() {
  if [ -x ./kubectl ] && ./kubectl version --client -o json 2>/dev/null | grep "${KUBECTL_VERSION}" >/dev/null; then
    log "kubectl ${KUBECTL_VERSION} already installed"; return
  fi
  log "Installing kubectl ${KUBECTL_VERSION}"
  curl -fsSL -o kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/${OS}/${GOARCH}/kubectl"
  chmod +x kubectl
}

install_kind() {
  if [ -x ./kind ] && ./kind version 2>/dev/null | grep "${KIND_VERSION}" >/dev/null; then
    log "kind ${KIND_VERSION} already installed"; return
  fi
  log "Installing kind ${KIND_VERSION}"
  curl -fsSL -o kind "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-${OS}-${GOARCH}"
  chmod +x kind
}

install_helm() {
  if [ -x ./helm ] && ./helm version --short 2>/dev/null | grep "${HELM_VERSION}" >/dev/null; then
    log "helm ${HELM_VERSION} already installed"; return
  fi
  log "Installing helm ${HELM_VERSION}"
  tmp="$(mktemp -d)"
  curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-${OS}-${GOARCH}.tar.gz" | tar -xz -C "${tmp}"
  mv "${tmp}/${OS}-${GOARCH}/helm" ./helm
  chmod +x helm
  rm -rf "${tmp}"
}

install_cilium_cli() {
  if [ -x ./cilium ] && ./cilium version --client 2>/dev/null | grep "${CILIUM_CLI_VERSION#v}" >/dev/null; then
    log "cilium CLI ${CILIUM_CLI_VERSION} already installed"; return
  fi
  log "Installing cilium CLI ${CILIUM_CLI_VERSION}"
  tmp="$(mktemp -d)"
  if ! curl -fsSL "https://github.com/cilium/cilium-cli/releases/download/${CILIUM_CLI_VERSION}/cilium-${OS}-${GOARCH}.tar.gz" | tar -xz -C "${tmp}"; then
    rm -rf "${tmp}"
    if [ -x ./cilium ]; then
      log "WARNING: download failed (offline?); keeping the existing cilium binary"; return
    fi
    return 1
  fi
  mv "${tmp}/cilium" ./cilium
  chmod +x cilium
  rm -rf "${tmp}"
}

install_hubble_cli() {
  if [ -x ./hubble ] && ./hubble version 2>/dev/null | grep "${HUBBLE_VERSION#v}" >/dev/null; then
    log "hubble CLI ${HUBBLE_VERSION} already installed"; return
  fi
  log "Installing hubble CLI ${HUBBLE_VERSION}"
  tmp="$(mktemp -d)"
  if ! curl -fsSL "https://github.com/cilium/hubble/releases/download/${HUBBLE_VERSION}/hubble-${OS}-${GOARCH}.tar.gz" | tar -xz -C "${tmp}"; then
    rm -rf "${tmp}"
    if [ -x ./hubble ]; then
      log "WARNING: download failed (offline?); keeping the existing hubble binary"; return
    fi
    return 1
  fi
  mv "${tmp}/hubble" ./hubble
  chmod +x hubble
  rm -rf "${tmp}"
}

install_kube_bench() {
  if [ -x ./kube-bench ] && [ -d ./kube-bench-cfg ] && ./kube-bench version 2>/dev/null | grep "${KUBE_BENCH_VERSION}" >/dev/null; then
    log "kube-bench ${KUBE_BENCH_VERSION} already installed"; return
  fi
  log "Installing kube-bench ${KUBE_BENCH_VERSION}"
  tmp="$(mktemp -d)"
  curl -fsSL -o "${tmp}/kube-bench.tar.gz" "https://github.com/aquasecurity/kube-bench/releases/download/v${KUBE_BENCH_VERSION}/kube-bench_${KUBE_BENCH_VERSION}_${OS}_${GOARCH}.tar.gz"
  tar -xzf "${tmp}/kube-bench.tar.gz" -C "${tmp}"
  mv "${tmp}/kube-bench" ./kube-bench
  chmod +x kube-bench
  # The binary alone isn't runnable — kube-bench loads its CIS benchmark check definitions
  # (per-target YAML) from a `cfg/` directory next to it at runtime. Keep it under a distinct
  # name (not `cfg`) so it doesn't collide with any other tool's working directory.
  rm -rf ./kube-bench-cfg
  mv "${tmp}/cfg" ./kube-bench-cfg
  rm -rf "${tmp}"
}

install_trivy() {
  if [ -x ./trivy ] && ./trivy --version 2>/dev/null | grep "${TRIVY_VERSION}" >/dev/null; then
    log "trivy ${TRIVY_VERSION} already installed"; return
  fi
  log "Installing trivy ${TRIVY_VERSION}"
  tmp="$(mktemp -d)"
  TRIVY_ARCH="64bit"; [ "${GOARCH}" = "arm64" ] && TRIVY_ARCH="ARM64"
  curl -fsSL -o "${tmp}/trivy.tar.gz" "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-${TRIVY_ARCH}.tar.gz"
  tar -xzf "${tmp}/trivy.tar.gz" -C "${tmp}"
  mv "${tmp}/trivy" ./trivy
  chmod +x trivy
  rm -rf "${tmp}"
}

install_k6() {
  if [ -x ./k6 ] && ./k6 version 2>/dev/null | grep "${K6_VERSION#v}" >/dev/null; then
    log "k6 ${K6_VERSION} already installed"; return
  fi
  log "Installing k6 ${K6_VERSION}"
  tmp="$(mktemp -d)"
  curl -fsSL "https://github.com/grafana/k6/releases/download/${K6_VERSION}/k6-${K6_VERSION}-${OS}-${GOARCH}.tar.gz" | tar -xz -C "${tmp}"
  mv "${tmp}/k6-${K6_VERSION}-${OS}-${GOARCH}/k6" ./k6
  chmod +x k6
  rm -rf "${tmp}"
}

install_kubectl
install_kind
install_helm
install_cilium_cli
install_hubble_cli
install_kube_bench
install_trivy
install_k6

log "Done. Add this to your shell profile (or run before each session):"
log "  export PATH=\"${BIN_DIR}:\$PATH\""
