#!/usr/bin/env bash
# Start the lab's port-forwards in the background and print the URLs.
# Idempotent: a forward that is already listening is left alone.
# Stop them with scripts/port-forward.sh stop (or make unforward).
#
# Ports: Grafana 3100 (3000 is used by another project's Grafana on this machine),
# Prometheus 9090, VictoriaLogs 9428.
set -uo pipefail
cd "$(dirname "$0")/.."

CTX="${CTX:-kind-gossip-lab}"
RUN_DIR=".run"
mkdir -p "$RUN_DIR"

# name | namespace | service | local:remote | URL
FORWARDS=(
  "grafana|observability|svc/grafana|3100:80|http://localhost:3100  (admin / lab-admin)"
  "prometheus|observability|svc/prometheus-server|9090:80|http://localhost:9090"
  "victorialogs|observability|svc/victoria-logs-victoria-logs-single-server|9428:9428|http://localhost:9428"
)

is_listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

stop_all() {
  local f name pidfile pid
  for f in "${FORWARDS[@]}"; do
    name="${f%%|*}"
    pidfile="$RUN_DIR/pf-$name.pid"
    if [ -f "$pidfile" ]; then
      pid=$(cat "$pidfile")
      kill "$pid" 2>/dev/null && echo "stopped $name (pid $pid)"
      rm -f "$pidfile"
    fi
  done
}

if [ "${1:-start}" = "stop" ]; then
  stop_all
  exit 0
fi

for f in "${FORWARDS[@]}"; do
  IFS='|' read -r name ns svc ports url <<<"$f"
  local_port="${ports%%:*}"
  pidfile="$RUN_DIR/pf-$name.pid"
  logfile="$RUN_DIR/pf-$name.log"

  if is_listening "$local_port"; then
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
      echo "ok   $name already forwarded: $url"
    else
      echo "BUSY $name: port $local_port is used by something else, so no forward was started. Free it or change the port in scripts/port-forward.sh."
    fi
    continue
  fi

  nohup kubectl --context "$CTX" -n "$ns" port-forward "$svc" "$ports" >"$logfile" 2>&1 &
  echo $! > "$pidfile"
  # Wait until the port answers, up to 20 seconds.
  for _ in $(seq 1 20); do
    is_listening "$local_port" && break
    sleep 1
  done
  if is_listening "$local_port"; then
    echo "up   $name: $url"
  else
    echo "FAIL $name did not start, see $logfile"
  fi
done
