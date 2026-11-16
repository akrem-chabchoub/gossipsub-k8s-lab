#!/usr/bin/env bash
# Scenario 2 (isolation and recovery) end to end, with timestamps in results/RUN/events.log.
# Phases: pre (120 s) -> isolation (240 s) -> recovery/post (150 s).
# Usage: scripts/run_scenario2.sh RUN_NAME
set -uo pipefail
cd "$(dirname "$0")/.."
RUN="${1:?usage: run_scenario2.sh RUN_NAME}"; OUT="results/$RUN"; mkdir -p "$OUT"
CTX=kind-gossip-lab; NS=gossip-mixed
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$OUT/events.log"; }

( while true; do python3 scripts/alert_state.py >> "$OUT/alert_states.jsonl" 2>/dev/null; sleep 20; done ) &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null' EXIT

log "run_start $RUN"
sleep 120
log "pre_end"

scripts/isolate.sh "$OUT" >/dev/null
log "isolation_policy_applied"
kubectl --context "$CTX" -n "$NS" delete pod go-peer-2 py-peer-2 2>&1 | tee -a "$OUT/events.log"
kubectl --context "$CTX" -n "$NS" wait --for=condition=Ready pod/go-peer-2 pod/py-peer-2 --timeout=240s >/dev/null 2>&1
log "isolated_pods_ready_under_policy"

end=$((SECONDS + 240))
while [ $SECONDS -lt $end ]; do scripts/verify_isolation.sh >> "$OUT/verify_during.txt" 2>&1; sleep 20; done

scripts/recover.sh "$OUT" >/dev/null
log "isolation_policy_removed"
sleep 25
scripts/verify_isolation.sh >> "$OUT/verify_after.txt" 2>&1
sleep 125
log "post_end"

# Complete logs from the node (rotated files included). `kubectl logs` returns only the current
# file, which loses receipts once a peer's log passes 10 MiB.
scripts/collect_logs.sh "$OUT" "$NS" go-peer-0 go-peer-1 go-peer-2 py-peer-0 py-peer-1 py-peer-2 >/dev/null
log "logs_collected"
