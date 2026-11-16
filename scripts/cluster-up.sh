#!/usr/bin/env bash
# Create the kind cluster with Calico and print real progress until it is ready.
#
# Why the pre-load: the kind node cannot always reach Docker Hub. When Calico's images are
# missing, its pods stay in Init for minutes and the node stays NotReady. The script pulls
# the Calico images on the host and imports them into the node before Calico starts, so
# the first pod start succeeds.
#
# Exits 0 when the node is Ready and every kube-system pod is Running or Completed.
# Exits 1 after TIMEOUT seconds, printing the pods that are not ready.
#
# Usage: scripts/cluster-up.sh            (CLUSTER=name and TIMEOUT=seconds are optional)
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER="${CLUSTER:-gossip-lab}"
CTX="kind-${CLUSTER}"
NODE="${CLUSTER}-control-plane"
TIMEOUT="${TIMEOUT:-600}"
CALICO_URL="https://raw.githubusercontent.com/projectcalico/calico/v3.30.3/manifests/calico.yaml"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
T0=$(date +%s)

say() { printf '[%4ss] %s\n' "$(( $(date +%s) - T0 ))" "$*"; }
say "Expected time: about 1 to 2 minutes (77 s measured on a test cluster). Longer if Calico images must download."

# 1. Calico manifest and its images, pulled on the host in parallel with cluster creation.
curl -sfL "$CALICO_URL" -o "$WORK/calico.yaml"
IMAGES=$(grep -o 'image: [^ ]*' "$WORK/calico.yaml" | awk '{print $2}' | sort -u)
say "pulling Calico images on the host (background)"
(
  for img in $IMAGES; do
    docker image inspect "$img" >/dev/null 2>&1 || docker pull -q "$img" >/dev/null
  done
  echo done > "$WORK/pulled"
) &
PULL_PID=$!

# 2. Cluster.
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  say "cluster $CLUSTER already exists, reusing it"
else
  say "creating kind cluster $CLUSTER (about 1 minute)"
  kind create cluster --name "$CLUSTER" --config deploy/kind/kind-config.yaml >"$WORK/kind.log" 2>&1 \
    || { cat "$WORK/kind.log"; exit 1; }
  say "kind cluster created"
fi

# 3. Import the Calico images into the node. kind load fails on these multi-platform images,
#    so each one is saved and imported with ctr for the node's architecture.
wait "$PULL_PID"
ARCH=$(docker exec "$NODE" uname -m)
case "$ARCH" in aarch64) PLATFORM=linux/arm64 ;; *) PLATFORM=linux/amd64 ;; esac
say "importing Calico images into $NODE ($PLATFORM)"
for img in $IMAGES; do
  tar="$WORK/$(echo "$img" | tr '/:' '__').tar"
  docker save -o "$tar" "$img"
  docker cp "$tar" "$NODE:/import.tar"
  docker exec "$NODE" ctr -n k8s.io images import --platform "$PLATFORM" --all-platforms=false /import.tar >/dev/null
  docker exec "$NODE" rm -f /import.tar
done

# 4. Calico, then wait with progress.
kubectl --context "$CTX" apply -f "$WORK/calico.yaml" >/dev/null
say "Calico applied, waiting for the node and kube-system pods"

last=""
while :; do
  elapsed=$(( $(date +%s) - T0 ))
  node=$(kubectl --context "$CTX" get node "$NODE" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)
  pods=$(kubectl --context "$CTX" -n kube-system get pods --no-headers 2>/dev/null || true)
  total=$(printf '%s\n' "$pods" | grep -c . || true)
  ready=$(printf '%s\n' "$pods" | awk '$3=="Running" || $3=="Completed"' | grep -c . || true)
  line="node Ready=${node:-unknown}  kube-system ${ready}/${total} ready"
  # Show what the pods that are not ready are waiting for.
  waiting=$(printf '%s\n' "$pods" | awk '$3!="Running" && $3!="Completed" {print $1 ":" $3}' | paste -sd ' ' - || true)
  [ -n "$waiting" ] && line="$line  [$waiting]"
  if [ "$line" != "$last" ]; then
    printf '[%4ss] %s\n' "$elapsed" "$line"
    last="$line"
  fi
  if [ "$node" = "True" ] && [ "$total" -gt 0 ] && [ "$ready" -eq "$total" ]; then
    say "READY: node Ready, $ready/$total kube-system pods running. Context: $CTX"
    exit 0
  fi
  if [ "$elapsed" -ge "$TIMEOUT" ]; then
    say "TIMEOUT after ${TIMEOUT}s. Not ready:"
    printf '%s\n' "$pods" | awk '$3!="Running" && $3!="Completed"'
    kubectl --context "$CTX" -n kube-system get events --sort-by=.lastTimestamp 2>/dev/null | tail -5
    exit 1
  fi
  sleep 5
done
