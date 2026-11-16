#!/usr/bin/env bash
# Apply scenario 2 isolation and record when it starts. Usage: scripts/isolate.sh RESULTS_DIR
set -euo pipefail
CTX=kind-gossip-lab; NS=gossip-mixed
OUT="${1:?usage: isolate.sh RESULTS_DIR}"; mkdir -p "$OUT"
kubectl --context "$CTX" apply -f deploy/k8s/mixed/isolate-go2-py2.yaml
echo "isolation_start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$OUT/events.log"
