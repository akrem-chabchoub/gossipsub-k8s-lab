"""Generates observability/grafana/dashboards/gossipsub-lab.json.

The dashboard is produced from this script so panel definitions are reviewable in code.
Each panel is tagged with the question it answers.
"""
import json
import os

DS = {"type": "prometheus", "uid": "prometheus"}
OUT = os.path.join(os.path.dirname(__file__), "dashboards", "gossipsub-lab.json")
_id = [0]


def panel(title, question, expr_list, unit="short", ptype="timeseries", description="", y=0, x=0, w=12, h=8):
    _id[0] += 1
    targets = []
    for i, e in enumerate(expr_list):
        expr, legend = (e if isinstance(e, tuple) else (e, ""))
        targets.append({"datasource": DS, "expr": expr, "legendFormat": legend, "refId": chr(65 + i)})
    return {
        "id": _id[0], "type": ptype, "title": title, "datasource": DS, "targets": targets,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "description": f"Question: {question}\n\n{description}".strip(),
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "max"]},
                    "tooltip": {"mode": "multi"}} if ptype == "timeseries" else {},
    }


def row(title, y):
    _id[0] += 1
    return {"id": _id[0], "type": "row", "title": title, "collapsed": False,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


NS = 'namespace=~"$namespace"'
PUB = 'publisher=~"$publisher"'

panels = []
y = 0
panels.append(row("1. Is the workload running?", y)); y += 1
panels.append(panel("Publish attempts, successes, errors per second", "Is the workload running?",
    [(f'sum by (namespace, peer) (rate(gossip_publish_attempted_total{{{NS}}}[1m]))', "attempted {{namespace}}/{{peer}}"),
     (f'sum by (namespace, peer) (rate(gossip_publish_success_total{{{NS}}}[1m]))', "success {{namespace}}/{{peer}}"),
     (f'sum by (namespace, peer) (rate(gossip_publish_errors_total{{{NS}}}[1m]))', "errors {{namespace}}/{{peer}}")],
    unit="ops", description="Target rate is PUBLISH_RATE (10/s). Only the publisher peer is non-zero.", y=y, x=0, w=24))
y += 8
panels.append(row("2. Are messages reaching subscribers?", y)); y += 1
panels.append(panel("Unique deliveries per subscriber (per second)", "Are messages reaching subscribers?",
    [(f'sum by (namespace, peer) (rate(gossip_deliveries_total{{{NS}}}[1m]))', "{{namespace}}/{{peer}}")],
    unit="ops", description="Deduplicated by (publisher, seq). Each subscriber should show about PUBLISH_RATE.", y=y, x=0, w=12))
panels.append(panel("Delivery completeness (fraction of expected)", "Are messages reaching subscribers?",
    [(f'sum by (namespace) (rate(gossip_deliveries_total{{{NS},{PUB}}}[1m])) / '
      f'(clamp_min(count by (namespace) (gossip_build_info{{{NS}}}) - 1, 1) * '
      f'sum by (namespace) (rate(gossip_publish_success_total{{{NS},peer=~"$publisher"}}[1m])))', "{{namespace}}")],
    unit="percentunit",
    description="Expected deliveries = publish rate x (subscribers - 1). Subscribers = peers in namespace minus publisher. "
                "The denominator is fixed during isolation, so isolated peers reduce completeness (tradeoffs.md D3).",
    y=y, x=12, w=12))
y += 8
panels.append(row("3. Is propagation slowing down?", y)); y += 1
panels.append(panel("Delivery latency p50 / p95 / p99", "Is propagation slowing down?",
    [(f'histogram_quantile(0.50, sum by (le, namespace, impl) (rate(gossip_delivery_latency_seconds_bucket{{{NS}}}[1m])))', "p50 {{namespace}} {{impl}}"),
     (f'histogram_quantile(0.95, sum by (le, namespace, impl) (rate(gossip_delivery_latency_seconds_bucket{{{NS}}}[1m])))', "p95 {{namespace}} {{impl}}"),
     (f'histogram_quantile(0.99, sum by (le, namespace, impl) (rate(gossip_delivery_latency_seconds_bucket{{{NS}}}[1m])))', "p99 {{namespace}} {{impl}}")],
    unit="s",
    description="Assumptions: publish time is the publisher's wall clock (UnixNano), receive time is the subscriber's wall clock. "
                "All pods run on one kind node, so they share a host clock. Negative values are counted as clock skew, "
                "not as latency. Histogram buckets: 1 ms to 5 s.",
    y=y, x=0, w=24))
y += 8
panels.append(row("4. Is connectivity changing?", y)); y += 1
panels.append(panel("Connected peers per peer", "Is connectivity changing?",
    [(f'gossip_connected_peers{{{NS}}}', "{{namespace}}/{{peer}}")], unit="short",
    description="Full mesh of 5 per subscriber in a 6-peer network; 2 in a 3-peer validation network. Drops mean links changed.",
    y=y, x=0, w=12))
panels.append(panel("GossipSub mesh size per peer", "Is connectivity changing?",
    [(f'gossip_mesh_peers{{{NS}}}', "{{namespace}}/{{peer}}")], unit="short",
    description="Mesh membership from GRAFT/PRUNE trace events (Go) and from the mesh size sampled each second (py). "
                "Mesh membership is a subset of connected peers.",
    y=y, x=12, w=12))
y += 8
panels.append(row("5. Is a process struggling?", y)); y += 1
panels.append(panel("CPU usage per peer (cores)", "Is a process struggling?",
    [('sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{namespace=~"$namespace",container="peer"}[1m]))', "{{namespace}}/{{pod}}")],
    unit="short", description="Limit is 0.5 cores per peer.", y=y, x=0, w=8))
panels.append(panel("CPU throttled share per peer", "Is a process struggling?",
    [('sum by (namespace, pod) (rate(container_cpu_cfs_throttled_periods_total{namespace=~"$namespace",container="peer"}[5m])) / '
      'sum by (namespace, pod) (rate(container_cpu_cfs_periods_total{namespace=~"$namespace",container="peer"}[5m]))', "{{namespace}}/{{pod}}")],
    unit="percentunit", description="Fraction of CFS periods throttled. Above 0 means the container hit its CPU limit.",
    y=y, x=8, w=8))
panels.append(panel("Memory working set per peer", "Is a process struggling?",
    [('sum by (namespace, pod) (container_memory_working_set_bytes{namespace=~"$namespace",container="peer"})', "{{namespace}}/{{pod}}")],
    unit="bytes", description="Limit is 256 MiB per peer.", y=y, x=16, w=8))
y += 8
panels.append(panel("Restarts per peer (cumulative)", "Is a process struggling?",
    [('sum by (namespace, pod) (kube_pod_container_status_restarts_total{namespace=~"$namespace",container="peer"})', "{{namespace}}/{{pod}}")],
    unit="short", description="Any increase is a crash or liveness failure.", y=y, x=0, w=24, h=6))
y += 6
panels.append(row("6. Is telemetry missing?", y)); y += 1
panels.append(panel("Scrape health (up) per peer", "Is telemetry missing?",
    [('up{job="gossip-peers",namespace=~"$namespace"}', "{{namespace}}/{{pod}}")], unit="short",
    description="0 means Prometheus could not scrape the peer. A gap in the series also means missing data.", y=y, x=0, w=12))
panels.append(panel("Seconds since last receipt per peer", "Is telemetry missing?",
    [('time() - gossip_last_receive_timestamp_seconds{namespace=~"$namespace"}', "{{namespace}}/{{peer}}")], unit="s",
    description="Rises when a peer stops receiving. Also rises during isolation, so read with the connectivity panels.",
    y=y, x=12, w=12))
y += 8
panels.append(row("7. Individual message timeline (VictoriaLogs)", y)); y += 1
panels.append({"id": 900, "type": "text", "title": "Per-message receipt timeline",
    "gridPos": {"x": 0, "y": y, "w": 24, "h": 6},
    "options": {"mode": "markdown", "content": (
        "Run this in the VictoriaLogs UI (http://localhost:9428/select/vmui via port-forward) or the API. "
        "Replace RUN_ID and SEQ.\n\n"
        "```\n_time:1h runId:RUN_ID seq:SEQ event:receive | fields _time, peer, publisherName, latencyMs, fromName | sort by (_time)\n```\n\n"
        "See `observability/queries/logsql.md` for the full query set.")}})
y += 6

dashboard = {
    "uid": "gossipsub-lab", "title": "GossipSub lab", "tags": ["gossipsub", "lab"],
    "schemaVersion": 39, "version": 1, "editable": False, "timezone": "browser",
    "time": {"from": "now-15m", "to": "now"}, "refresh": "5s",
    "templating": {"list": [
        {"name": "namespace", "type": "custom", "label": "Namespace", "multi": True, "includeAll": True,
         "current": {"text": "All", "value": "$__all"},
         "query": "gossip-lab,gossip-mixed,gossip-lab-py",
         "options": [{"text": t, "value": t, "selected": False} for t in ["gossip-lab", "gossip-mixed", "gossip-lab-py"]]},
        {"name": "publisher", "type": "custom", "label": "Publisher peer", "multi": False, "includeAll": False,
         "current": {"text": "go-peer-0", "value": "go-peer-0"}, "query": "go-peer-0,py-peer-0",
         "options": [{"text": t, "value": t, "selected": t == "go-peer-0"} for t in ["go-peer-0", "py-peer-0"]]},
    ]},
    "annotations": {"list": []},
    "panels": panels,
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as f:
    json.dump(dashboard, f, indent=2)
print("wrote", OUT, "panels:", len(panels))
