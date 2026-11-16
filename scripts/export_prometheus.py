"""Export key time series for a scenario window from Prometheus to CSV.

Usage: python3 scripts/export_prometheus.py --from ISO --to ISO --out results/DIR/prometheus.csv

Starts a port-forward to the observability Prometheus service (kind-gossip-lab context),
queries query_range, and stops only the port-forward it started.
"""
import argparse
import csv
import datetime as dt
import json
import os
import socket
import subprocess
import time
import urllib.parse
import urllib.request

CTX = "kind-gossip-lab"
QUERIES = {
    "connected_peers": 'gossip_connected_peers{namespace="gossip-mixed"}',
    "mesh_peers": 'gossip_mesh_peers{namespace="gossip-mixed"}',
    "deliveries_rate_per_s": 'sum by (namespace, peer) (rate(gossip_deliveries_total{namespace="gossip-mixed"}[30s]))',
    "publish_rate_per_s": 'sum by (namespace, peer) (rate(gossip_publish_success_total{namespace="gossip-mixed"}[30s]))',
    "up": 'up{job="gossip-peers",namespace="gossip-mixed"}',
    "cpu_cores": 'sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="gossip-mixed",container="peer"}[1m]))',
    "cpu_throttled_share": 'sum by (pod) (rate(container_cpu_cfs_throttled_periods_total{namespace="gossip-mixed",container="peer"}[1m])) / sum by (pod) (rate(container_cpu_cfs_periods_total{namespace="gossip-mixed",container="peer"}[1m]))',
    "memory_bytes": 'sum by (pod) (container_memory_working_set_bytes{namespace="gossip-mixed",container="peer"})',
    "restarts": 'sum by (pod) (kube_pod_container_status_restarts_total{namespace="gossip-mixed",container="peer"})',
}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t_from", required=True)
    ap.add_argument("--to", dest="t_to", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--step", default="5")
    a = ap.parse_args()
    port = free_port()
    pf = subprocess.Popen(["kubectl", "--context", CTX, "-n", "observability", "port-forward",
                           "svc/prometheus-server", f"{port}:80"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(4)
        rows = []
        for name, q in QUERIES.items():
            params = urllib.parse.urlencode({"query": q, "start": a.t_from, "end": a.t_to, "step": a.step})
            data = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/query_range?{params}", timeout=60))
            for series in data["data"]["result"]:
                labels = ",".join(f'{k}={v}' for k, v in sorted(series["metric"].items()) if k != "__name__")
                for ts, val in series["values"]:
                    rows.append((dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 name, labels, val))
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_utc", "metric", "labels", "value"])
            w.writerows(rows)
        print(f"wrote {len(rows)} rows to {a.out}")
    finally:
        pf.terminate()


if __name__ == "__main__":
    main()
