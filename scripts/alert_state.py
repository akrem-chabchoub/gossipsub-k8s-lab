"""Snapshot the state of the provisioned alert rule from Grafana.

Usage: python3 scripts/alert_state.py >> results/DIR/alert_states.jsonl   (run repeatedly)

Output: one JSON line per call with UTC time, rule title, state, and the active alert values.
Starts and stops its own port-forward to the Grafana service.
"""
import base64
import datetime as dt
import json
import socket
import subprocess
import time
import urllib.request

CTX = "kind-gossip-lab"
AUTH = "Basic " + base64.b64encode(b"admin:lab-admin").decode()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    port = free_port()
    pf = subprocess.Popen(["kubectl", "--context", CTX, "-n", "observability", "port-forward",
                           "svc/grafana", f"{port}:80"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):  # wait for the port-forward instead of a fixed sleep
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/prometheus/grafana/api/v1/rules",
                                     headers={"Authorization": AUTH})
        data = json.load(urllib.request.urlopen(req, timeout=30))
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for group in data["data"]["groups"]:
            for rule in group["rules"]:
                if rule.get("name") == "Peer connectivity below 5" or rule.get("type") == "alerting":
                    alerts = [{"labels": a.get("labels"), "state": a.get("state"), "value": a.get("value")}
                              for a in rule.get("alerts", [])]
                    print(json.dumps({"time_utc": now, "rule": rule.get("name"), "state": rule.get("state"),
                                      "health": rule.get("health"), "alerts": alerts}))
    finally:
        pf.terminate()


if __name__ == "__main__":
    main()
