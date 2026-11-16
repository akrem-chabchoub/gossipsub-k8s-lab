#!/usr/bin/env bash
# Phase analysis for a scenario-2 run. Usage: scripts/analyze_run.sh RUN_NAME
set -uo pipefail
cd "$(dirname "$0")/.."
RUN="${1:?usage: analyze_run.sh RUN_NAME}"; OUT="results/$RUN"
t() { grep "$1" "$OUT/events.log" | head -1 | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z'; }
RUNID=$(grep -oE '"runId":"[0-9TZ]+"' "$OUT/go-peer-0.log" | tail -1 | grep -oE '[0-9]{8}T[0-9]{6}Z')
LOGS=("$OUT"/go-peer-0.log "$OUT"/go-peer-1.log "$OUT"/go-peer-2.log "$OUT"/py-peer-0.log "$OUT"/py-peer-1.log "$OUT"/py-peer-2.log)
run_start=$(t run_start); pre_end=$(t pre_end); iso_start=$(t isolation_policy_applied); iso_end=$(t isolation_policy_removed); post_end=$(t post_end)
A=(--publisher go-peer-0 --run "$RUNID" --grace-s 10)
{
echo "run=$RUN runId=$RUNID"
echo "--- pre-isolation ($run_start -> $pre_end)"; python3 scripts/analyze_phase.py "${A[@]}" --from "$run_start" --to "$pre_end" "${LOGS[@]}"
echo "--- isolation ($iso_start -> $iso_end)"; python3 scripts/analyze_phase.py "${A[@]}" --from "$iso_start" --to "$iso_end" "${LOGS[@]}"
echo "--- recovery ($iso_end -> $post_end)"; python3 scripts/analyze_phase.py "${A[@]}" --from "$iso_end" --to "$post_end" "${LOGS[@]}"
} | tee "$OUT/delivery_summary.txt"
python3 scripts/export_prometheus.py --from "$run_start" --to "$post_end" --out "$OUT/prometheus.csv"
