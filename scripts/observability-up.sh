#!/usr/bin/env bash
# Install the observability stack into the gossip-lab cluster with one command.
# Prometheus, Grafana (with provisioned dashboard and alert), Vector and VictoriaLogs.
# Safe to re-run: repos, namespace and ConfigMap are created or updated, and Helm upgrades in place.
#
# Expected time: about 3 to 5 minutes (not measured yet).
# Usage: scripts/observability-up.sh        (CTX=kube-context overrides kind-gossip-lab)
set -euo pipefail
cd "$(dirname "$0")/.."

CTX="${CTX:-kind-gossip-lab}"
NS=observability
T0=$(date +%s)
say() { printf '[%4ss] %s\n' "$(( $(date +%s) - T0 ))" "$*"; }

say "Expected time: about 3 to 5 minutes (not measured yet)"

say "1/5 Helm repositories (only missing ones are added; no index refresh when they exist)"
# `helm repo update` downloads every repository index on each run, which took minutes here.
# Run it by hand (helm repo update) when you want newer chart versions.
added=0
add_repo() {
  if ! helm repo list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$1"; then
    helm repo add "$1" "$2" >/dev/null
    added=1
  fi
}
add_repo prometheus-community https://prometheus-community.github.io/helm-charts
add_repo grafana https://grafana.github.io/helm-charts
add_repo vector https://helm.vector.dev
add_repo victoriametrics https://victoriametrics.github.io/helm-charts/
[ "$added" = 1 ] && helm repo update >/dev/null && say "  repository indexes refreshed (new repos added)"

# Pull the chart images on the host and import them into the node. Pulls inside the kind node
# are very slow (a 13 MB image took more than 6 minutes), and the Prometheus pod stalls on them.
preload_images() {
  local node="${CLUSTER:-gossip-lab}-control-plane"
  local tmp; tmp="$(mktemp -d)"
  local images=""
  for spec in "prometheus-community/prometheus 29.47.2 prometheus-values.yaml" \
              "grafana/grafana 10.5.15 grafana-values.yaml" \
              "vector/vector 0.61.0 vector-values.yaml" \
              "victoriametrics/victoria-logs-single 0.13.13 victorialogs-values.yaml"; do
    set -- $spec
    images="$images $(helm template probe "$1" --version "$2" -f "observability/helm/$3" 2>/dev/null \
      | grep -oE 'image: *"?[^" ]+' | sed -E 's/image: *"?//' | sort -u)"
  done
  images=$(printf '%s\n' $images | sort -u | grep -v '^$' || true)
  local count; count=$(printf '%s\n' "$images" | grep -c . || true)
  say "  preloading $count chart images (pulls in parallel, images already in the node are skipped)"
  # Pull missing images on the host, 8 at a time.
  printf '%s\n' $images | xargs -P 8 -I{} sh -c 'docker image inspect "$1" >/dev/null 2>&1 || docker pull -q "$1" >/dev/null' _ {}
  local arch; arch=$(docker exec "$node" uname -m)
  local platform=linux/amd64; [ "$arch" = aarch64 ] && platform=linux/arm64
  # Images the node already has are skipped.
  local onnode; onnode=$(docker exec "$node" crictl images 2>/dev/null | awk 'NR>1{print $1":"$2}' || true)
  local imported=0
  for img in $images; do
    local norm="$img"
    if printf '%s\n' "$onnode" | grep -qxF "$norm" || printf '%s\n' "$onnode" | grep -qxF "${norm#docker.io/}"; then
      continue
    fi
    local f="$tmp/$(echo "$img" | tr '/:' '__').tar"
    docker save -o "$f" "$img"
    docker cp "$f" "$node:/import.tar"
    docker exec "$node" ctr -n k8s.io images import --platform "$platform" --all-platforms=false /import.tar >/dev/null
    docker exec "$node" rm -f /import.tar
    imported=$((imported + 1))
  done
  rm -rf "$tmp"
  say "  $imported images imported into the node"
}

say "2/5 namespace $NS"
kubectl --context "$CTX" create namespace "$NS" --dry-run=client -o yaml | kubectl --context "$CTX" apply -f - >/dev/null

say "3/5 dashboards ConfigMap"
python3 observability/grafana/build_dashboard.py >/dev/null
kubectl --context "$CTX" -n "$NS" create configmap gossip-lab-dashboards \
  --from-file=observability/grafana/dashboards --dry-run=client -o yaml \
  | kubectl --context "$CTX" apply -f - >/dev/null

preload_images
say "4/5 charts: prometheus, grafana, vector, victoria-logs (each waits until its pods are ready)"
helm --kube-context "$CTX" upgrade --install prometheus prometheus-community/prometheus --version 29.47.2 \
  -n "$NS" -f observability/helm/prometheus-values.yaml --wait --timeout 10m >/dev/null
say "  prometheus ready"
helm --kube-context "$CTX" upgrade --install grafana grafana/grafana --version 10.5.15 \
  -n "$NS" -f observability/helm/grafana-values.yaml --wait --timeout 10m >/dev/null
say "  grafana ready"
helm --kube-context "$CTX" upgrade --install vector vector/vector --version 0.61.0 \
  -n "$NS" -f observability/helm/vector-values.yaml --wait --timeout 10m >/dev/null
say "  vector ready"
helm --kube-context "$CTX" upgrade --install victoria-logs victoriametrics/victoria-logs-single --version 0.13.13 \
  -n "$NS" -f observability/helm/victorialogs-values.yaml --wait --timeout 10m >/dev/null
say "  victoria-logs ready"

say "5/5 status"
kubectl --context "$CTX" -n "$NS" get pods --no-headers | awk '{print "  " $1, $2, $3}'
say "DONE. Grafana: kubectl --context $CTX -n $NS port-forward svc/grafana 3100:80  then open http://localhost:3100 (3000 is taken by another project's Grafana)"
