#!/usr/bin/env bash
# Remove scenario 2 isolation and record when it ends. Deletes only objects labelled
# gossip.lab/experiment=isolation-recovery (decision D5). Usage: scripts/recover.sh RESULTS_DIR
set -euo pipefail
CTX=kind-gossip-lab; NS=gossip-mixed
OUT="${1:?usage: recover.sh RESULTS_DIR}"; mkdir -p "$OUT"
kubectl --context "$CTX" -n "$NS" delete networkpolicy -l gossip.lab/experiment=isolation-recovery
echo "isolation_end_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$OUT/events.log"
