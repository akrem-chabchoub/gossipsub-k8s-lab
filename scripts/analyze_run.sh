#!/usr/bin/env bash
# Phase analysis for a scenario-2 run. Usage: scripts/analyze_run.sh RUN_NAME
#
# Phases by publish time:
#   pre-isolation  run_start .. pre_end                                   grace 10 s
#   restart        isolation_policy_applied .. isolated_pods_ready         grace 0 (pods restarting)
#   isolation      isolated_pods_ready_under_policy .. isolation_policy_removed   grace 0
#   recovery       isolation_policy_removed .. post_end                    grace 10 s
# The isolation phase uses no grace, so a message published during the cut that arrives after the
# policy is removed is reported as late, with the window it arrived in, not as on time.
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="${1:?usage: analyze_run.sh RUN_NAME}"; OUT="results/$RUN"
[ -f "$OUT/events.log" ] || { echo "missing $OUT/events.log" >&2; exit 1; }

t() {
  local v; v=$(grep " $1" "$OUT/events.log" | head -1 | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z' || true)
  [ -n "$v" ] || { echo "events.log has no '$1' marker; the run did not complete" >&2; exit 1; }
  echo "$v"
}
run_start=$(t run_start); pre_end=$(t pre_end); iso_applied=$(t isolation_policy_applied)
iso_ready=$(t isolated_pods_ready_under_policy); iso_end=$(t isolation_policy_removed); post_end=$(t post_end)

RUNID=$(grep -oE '"runId": ?"[^"]+"' "$OUT/go-peer-0.log" | tail -1 | grep -oE '[0-9]{8}T[0-9]{6}Z' || true)
[ -n "$RUNID" ] || { echo "no runId in $OUT/go-peer-0.log" >&2; exit 1; }

LOGS=()
for p in go-peer-0 go-peer-1 go-peer-2 py-peer-0 py-peer-1 py-peer-2; do LOGS+=("$OUT/$p.log"); done
for p in "$OUT"/before-restart/*.log; do [ -f "$p" ] && LOGS+=("$p"); done

A=(--publisher go-peer-0 --run "$RUNID")
{
  echo "run=$RUN runId=$RUNID"
  python3 scripts/analyze_phase.py "${A[@]}" --label pre-isolation --grace-s 10 --from "$run_start" --to "$pre_end" "${LOGS[@]}"
  python3 scripts/analyze_phase.py "${A[@]}" --label restart --grace-s 0 --from "$iso_applied" --to "$iso_ready" "${LOGS[@]}"
  python3 scripts/analyze_phase.py "${A[@]}" --label isolation --grace-s 0 --from "$iso_ready" --to "$iso_end" "${LOGS[@]}"
  python3 scripts/analyze_phase.py "${A[@]}" --label recovery --grace-s 10 --from "$iso_end" --to "$post_end" "${LOGS[@]}"
} | tee "$OUT/delivery_summary.txt"
python3 scripts/export_prometheus.py --from "$run_start" --to "$post_end" --out "$OUT/prometheus.csv"
