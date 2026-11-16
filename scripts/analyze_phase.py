"""Per-phase delivery analysis for the scenario runs.

Usage:
  python3 scripts/analyze_phase.py --publisher go-peer-0 --run RUN_ID \
      --from 2026-11-16T09:11:07Z --to 2026-11-16T09:16:07Z \
      --grace-s 10 LOG [LOG ...]

Messages are assigned to a phase by publish time (publishedAtNs in [from, to)).
For each subscriber, the script reports:
  - delivered_on_time: received within [publish, to + grace]
  - delivered_late: received after to + grace (late arrivals, e.g. after recovery)
  - never: not received in the logs supplied
  - latency p50 / p99 for on-time receipts
Receipts come from JSON lines with event == "receive" and the given runId.
"""
import argparse
import datetime as dt
import json


def iso_ns(s):
    t = dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return int(t.timestamp() * 1e9)


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


def pct(vals, q):
    if not vals:
        return float("nan")
    return vals[min(len(vals) - 1, max(0, int(round(q * len(vals))) - 1))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--publisher", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--from", dest="t_from", required=True)
    ap.add_argument("--to", dest="t_to", required=True)
    ap.add_argument("--grace-s", type=float, default=10.0)
    args = ap.parse_args()
    t0, t1 = iso_ns(args.t_from), iso_ns(args.t_to)
    grace = int(args.grace_s * 1e9)

    per_log = {p: events(p) for p in args.logs}
    pubs = [e for p in args.logs for e in per_log[p]
            if e.get("event") == "publish" and e.get("peer") == args.publisher and e.get("runId") == args.run
            and t0 <= e["publishedAtNs"] < t1]
    seqs = {e["seq"]: e["publishedAtNs"] for e in pubs}
    n = len(seqs)
    print(f"phase {args.t_from} -> {args.t_to}: run={args.run} publisher={args.publisher} published={n}")
    if n == 0:
        return
    for p in args.logs:
        peer = next((e["peer"] for e in per_log[p] if "peer" in e), p)
        if peer == args.publisher:
            continue
        rec = {}
        for e in per_log[p]:
            if e.get("event") == "receive" and e.get("runId") == args.run and e.get("seq") in seqs:
                rec.setdefault(e["seq"], e)  # first receipt
        on_time, late, lat = 0, 0, []
        for s, e in rec.items():
            if e["receivedAtNs"] <= t1 + grace:
                on_time += 1
                lat.append(e["latencyMs"])
            else:
                late += 1
        never = n - len(rec)
        lat.sort()
        print(f"  {peer}: on_time={on_time}/{n} ({100 * on_time / n:.1f}%) late={late} never={never} "
              f"p50_ms={pct(lat, 0.5):.2f} p99_ms={pct(lat, 0.99):.2f}")


if __name__ == "__main__":
    main()
