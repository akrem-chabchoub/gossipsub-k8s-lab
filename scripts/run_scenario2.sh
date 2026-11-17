#!/usr/bin/env bash
# Scenario 2 (isolation and recovery) end to end, with timestamps in results/RUN/events.log.
#
# Phases (events.log markers):
#   wait for a full mesh                 -> mesh_full
#   pre-isolation, PRE_S                 run_start .. pre_end
#   policy applied, isolated pods saved and restarted under it
#                                        isolation_policy_applied .. isolated_pods_ready_under_policy
#   isolation, ISO_S                     isolated_pods_ready_under_policy .. isolation_policy_removed
#   recovery, POST_S                     isolation_policy_removed .. post_end
#
# The two isolated pods are restarted because the policy alone does not drop established
# connections (decision D11). Their logs are saved first (results/RUN/before-restart/): a deleted
# pod's log files are removed from the node, and without them the pre-isolation receipts of the
# isolated peers would be reported as never received.
#
# Any failed step stops the run. If the run stops while the policy is applied, the policy is removed.
# Exit status 1 if an isolation check did not match its expectation.
#
# Usage: scripts/run_scenario2.sh RUN_NAME
# Env:   PRE_S=120 ISO_S=240 POST_S=150 WAIT_TIMEOUT_S=300
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="${1:?usage: run_scenario2.sh RUN_NAME}"; OUT="results/$RUN"; mkdir -p "$OUT"
CTX=kind-gossip-lab; NS=gossip-mixed
PRE_S="${PRE_S:-120}"; ISO_S="${ISO_S:-240}"; POST_S="${POST_S:-150}"; WAIT_TIMEOUT_S="${WAIT_TIMEOUT_S:-300}"
PODS="go-peer-0 go-peer-1 go-peer-2 py-peer-0 py-peer-1 py-peer-2"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$OUT/events.log"; }

POLICY_APPLIED=0; SAMPLER=""
cleanup() {
  local rc=$?
  [ -n "$SAMPLER" ] && kill "$SAMPLER" 2>/dev/null || true
  if [ "$POLICY_APPLIED" = 1 ]; then
    log "ABORTED rc=$rc: removing the isolation policy"
    scripts/recover.sh "$OUT" >/dev/null || log "ABORTED: recover.sh failed, remove the policy by hand"
  fi
}
trap cleanup EXIT

# Probe pod for the reachability checks.
kubectl --context "$CTX" apply -f deploy/k8s/mixed/probe.yaml >/dev/null
kubectl --context "$CTX" -n "$NS" wait --for=condition=Ready pod/probe --timeout=120s >/dev/null

# Start from a full mesh: every peer connected to the five others.
connected() {
  kubectl --context "$CTX" get --raw "/api/v1/namespaces/$NS/pods/$1:9100/proxy/metrics" 2>/dev/null \
    | awk '/^gossip_connected_peers[{ ]/ {print int($NF)}' || true
}
deadline=$((SECONDS + WAIT_TIMEOUT_S))
while :; do
  status=""; full=1
  for p in $PODS; do c=$(connected "$p"); status="$status $p=${c:-?}"; [ "${c:-}" = 5 ] || full=0; done
  [ "$full" = 1 ] && break
  [ $SECONDS -lt $deadline ] || { log "not_stable_after ${WAIT_TIMEOUT_S}s connections:$status"; exit 1; }
  sleep 5
done
log "mesh_full connections:$status"

# Alert state every 20 s for the fire/resolve evidence.
( while true; do python3 scripts/alert_state.py >> "$OUT/alert_states.jsonl" 2>> "$OUT/alert_sampler.err"; sleep 20; done ) &
SAMPLER=$!

log "run_start $RUN pre_s=$PRE_S iso_s=$ISO_S post_s=$POST_S"
kubectl --context "$CTX" -n "$NS" get pods -o wide > "$OUT/pods_before.txt"
sleep "$PRE_S"
log "pre_end"

POLICY_APPLIED=1
scripts/isolate.sh "$OUT" >/dev/null
log "isolation_policy_applied"
scripts/collect_logs.sh "$OUT/before-restart" "$NS" go-peer-2 py-peer-2 >/dev/null
log "before_restart_logs_saved"
kubectl --context "$CTX" -n "$NS" delete pod go-peer-2 py-peer-2 2>&1 | tee -a "$OUT/events.log"
log "isolated_pods_deleted"
kubectl --context "$CTX" -n "$NS" wait --for=condition=Ready pod/go-peer-2 pod/py-peer-2 --timeout=240s >/dev/null
log "isolated_pods_ready_under_policy"

VERIFY_FAILED=0
end=$((SECONDS + ISO_S))
while [ $SECONDS -lt $end ]; do
  scripts/verify_isolation.sh during >> "$OUT/verify_during.txt" 2>&1 \
    || { VERIFY_FAILED=1; log "verify_during_MISMATCH (see verify_during.txt)"; }
  sleep 20
done

scripts/recover.sh "$OUT" >/dev/null
POLICY_APPLIED=0
log "isolation_policy_removed"
sleep 25
scripts/verify_isolation.sh after >> "$OUT/verify_after.txt" 2>&1 \
  || { VERIFY_FAILED=1; log "verify_after_MISMATCH (see verify_after.txt)"; }
sleep $((POST_S - 25))
log "post_end"
kubectl --context "$CTX" -n "$NS" get pods -o wide > "$OUT/pods_after.txt"

# Complete logs from the node (rotated files included). `kubectl logs` returns only the current
# file, which loses receipts once a peer's log passes 10 MiB.
# shellcheck disable=SC2086
scripts/collect_logs.sh "$OUT" "$NS" $PODS >/dev/null
log "logs_collected"
[ "$VERIFY_FAILED" = 0 ] || { log "FAILED: an isolation check did not match its expectation"; exit 1; }
log "done"
