#!/usr/bin/env bash
# Scenario 1 (baseline): wait until the network is stable, then measure normal propagation.
#
# Phases, all timestamped in results/RUN/events.log:
#   1. wait until every peer pod is Ready and reports a full mesh of connections (N-1 peers each)
#   2. settle for STABILIZE_S seconds so the GossipSub mesh and heartbeats reach steady state
#   3. measure for MEASURE_S seconds (the window the analysis uses)
#   4. wait GRACE_S so receipts of the last messages in the window are logged, then collect
#
# Output in results/RUN/: events.log, pods_before.txt, pods_after.txt, <pod>.log (complete logs),
# delivery_summary.txt (per-subscriber completeness and latency), prometheus.csv.
#
# Usage: scripts/run_scenario1.sh RUN_NAME
# Env:   NS=gossip-mixed (or gossip-lab for the six-Go network), STABILIZE_S=60, MEASURE_S=300,
#        GRACE_S=10, WAIT_TIMEOUT_S=300, PUBLISHER=go-peer-0
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="${1:?usage: run_scenario1.sh RUN_NAME}"; OUT="results/$RUN"; mkdir -p "$OUT"
CTX="${CTX:-kind-gossip-lab}"; NS="${NS:-gossip-mixed}"; PUBLISHER="${PUBLISHER:-go-peer-0}"
STABILIZE_S="${STABILIZE_S:-60}"; MEASURE_S="${MEASURE_S:-300}"; GRACE_S="${GRACE_S:-10}"
WAIT_TIMEOUT_S="${WAIT_TIMEOUT_S:-300}"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$OUT/events.log"; }
now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Peer pods carry app.kubernetes.io/part-of=<namespace> in both peer namespaces.
PODS=$(kubectl --context "$CTX" -n "$NS" get pods -l "app.kubernetes.io/part-of=$NS" \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort)
N=$(printf '%s\n' "$PODS" | grep -c .)
[ "$N" -ge 2 ] || { echo "found $N peer pods in $NS, need at least 2" >&2; exit 1; }
WANT=$((N - 1))

log "run_start $RUN ns=$NS peers=$N publisher=$PUBLISHER stabilize_s=$STABILIZE_S measure_s=$MEASURE_S"
kubectl --context "$CTX" -n "$NS" get pods -o wide > "$OUT/pods_before.txt"

# 1. Stable = every pod Ready and every peer connected to all N-1 others.
# Metrics are read through the API server proxy, so no port-forward is needed.
connected() {
  kubectl --context "$CTX" get --raw "/api/v1/namespaces/$NS/pods/$1:9100/proxy/metrics" 2>/dev/null \
    | awk '/^gossip_connected_peers[{ ]/ {print int($NF)}'
}
deadline=$((SECONDS + WAIT_TIMEOUT_S))
while :; do
  kubectl --context "$CTX" -n "$NS" wait --for=condition=Ready pod -l "app.kubernetes.io/part-of=$NS" \
    --timeout=10s >/dev/null 2>&1 && ready=1 || ready=0
  status=""; full=1
  for p in $PODS; do
    c=$(connected "$p"); c="${c:-?}"
    status="$status $p=$c"
    [ "$c" = "$WANT" ] || full=0
  done
  if [ "$ready" = 1 ] && [ "$full" = 1 ]; then break; fi
  if [ $SECONDS -ge $deadline ]; then
    log "not_stable_after ${WAIT_TIMEOUT_S}s ready=$ready connections:$status"
    exit 1
  fi
  sleep 5
done
log "mesh_full connections:$status"

# 2. Settle.
sleep "$STABILIZE_S"
log "stabilized"

# 3. Measure. The analysis window is [measure_start, measure_end) by publish time.
MEASURE_START=$(now_iso); log "measure_start"
sleep "$MEASURE_S"
MEASURE_END=$(now_iso); log "measure_end"

# 4. Grace for in-flight receipts, then collect.
sleep "$GRACE_S"
kubectl --context "$CTX" -n "$NS" get pods -o wide > "$OUT/pods_after.txt"
# shellcheck disable=SC2086
scripts/collect_logs.sh "$OUT" "$NS" $PODS >/dev/null
log "logs_collected"

# A restart during the window invalidates the baseline: compare restart counts.
if ! diff <(awk 'NR>1{print $1, $4}' "$OUT/pods_before.txt") <(awk 'NR>1{print $1, $4}' "$OUT/pods_after.txt") >/dev/null; then
  log "WARNING restart_counts_changed (see pods_before.txt / pods_after.txt); baseline may be invalid"
fi

RUNID=$(grep '"event":"publish"' "$OUT/$PUBLISHER.log" | tail -1 | grep -oE '"runId":"[^"]+"' | cut -d'"' -f4)
LOGS=(); for p in $PODS; do LOGS+=("$OUT/$p.log"); done
{
  echo "scenario=baseline run=$RUN ns=$NS runId=$RUNID publisher=$PUBLISHER"
  echo "window=$MEASURE_START -> $MEASURE_END (${MEASURE_S}s) grace=${GRACE_S}s stabilize=${STABILIZE_S}s after mesh_full"
  python3 scripts/analyze_phase.py --publisher "$PUBLISHER" --run "$RUNID" --grace-s "$GRACE_S" \
    --from "$MEASURE_START" --to "$MEASURE_END" "${LOGS[@]}"
} | tee "$OUT/delivery_summary.txt"
python3 scripts/export_prometheus.py --namespace "$NS" --from "$MEASURE_START" --to "$MEASURE_END" \
  --out "$OUT/prometheus.csv"
log "done"
