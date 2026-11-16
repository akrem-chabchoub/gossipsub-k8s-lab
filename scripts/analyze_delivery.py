"""Delivery analysis from structured peer logs (one JSON object per line).

Usage:
  python3 scripts/analyze_delivery.py --publisher go-peer-0 --window-s 60 LOG [LOG...]

Each LOG is the captured stdout of one peer (kubectl logs). The publisher's
`publish` events define the denominator. For each subscriber log, the script
reports unique delivered seqs for the chosen run, completeness over the whole run
and over the last --window-s seconds, and receive latency percentiles.
The run is the runId of the last publish event from the publisher.
"""
import argparse
import json
import sys


def events(path):
    out = []
    with open(path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def pct(sorted_vals, q):
    if not sorted_vals:
        return float("nan")
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * len(sorted_vals))) - 1))
    return sorted_vals[idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--publisher", required=True, help="peer name of the publisher")
    ap.add_argument("--window-s", type=float, default=60.0)
    args = ap.parse_args()

    all_ev = {p: events(p) for p in args.logs}
    pub_events = [e for p in args.logs for e in all_ev[p]
                  if e.get("event") == "publish" and e.get("peer") == args.publisher]
    if not pub_events:
        sys.exit("no publish events found for publisher %s" % args.publisher)
    run = max(pub_events, key=lambda e: e["publishedAtNs"])["runId"]
    pubs = [e for e in pub_events if e["runId"] == run]
    seqs = {e["seq"] for e in pubs}
    t_end = max(e["publishedAtNs"] for e in pubs)
    window = {e["seq"] for e in pubs if e["publishedAtNs"] >= t_end - args.window_s * 1e9}
    span_s = (t_end - min(e["publishedAtNs"] for e in pubs)) / 1e9
    print(f"run={run} publisher={args.publisher} published={len(seqs)} "
          f"span_s={span_s:.1f} rate_per_s={len(seqs) / span_s if span_s else float('nan'):.2f}")
    for path in args.logs:
        peer = next((e["peer"] for e in all_ev[path] if "peer" in e), path)
        if peer == args.publisher:
            continue
        rec = [e for e in all_ev[path] if e.get("event") == "receive" and e.get("runId") == run]
        got = {e["seq"] for e in rec} & seqs
        lat = sorted(e["latencyMs"] for e in rec if e["seq"] in seqs)
        win_got = got & window
        print(f"{peer}: completeness_run={100 * len(got) / len(seqs):.2f}% "
              f"completeness_last{args.window_s:.0f}s={100 * len(win_got) / max(1, len(window)):.2f}% "
              f"p50_ms={pct(lat, 0.5):.2f} p99_ms={pct(lat, 0.99):.2f} max_ms={lat[-1] if lat else float('nan'):.2f}")


if __name__ == "__main__":
    main()
