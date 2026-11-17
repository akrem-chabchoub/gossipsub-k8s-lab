"""Per-phase delivery analysis for the scenario runs.

Usage:
  python3 scripts/analyze_phase.py --publisher go-peer-0 --run RUN_ID \
      --from 2026-11-16T09:11:07Z --to 2026-11-16T09:16:07Z \
      --grace-s 10 LOG [LOG ...]

Messages are assigned to a phase by publish time (publishedAtNs in [from, to)).
For each subscriber, the script reports:
  - on_time: received by to + grace
  - late: received after to + grace, with the window those late receipts arrived in
  - never: not received in the logs supplied
  - latency p50 / p95 / p99 of the on-time receipts
Use --grace-s 0 for a phase that ends with a change of network state (the isolation phase ends
when the policy is removed): any grace would count receipts after recovery as on time.

Several files may belong to one peer (for example the logs saved before a pod restart and the
logs of the new pod); they are merged by the "peer" field, keeping the first receipt per seq.
Receipts come from JSON lines with event == "receive" and the given runId.
Exit status 1 if the phase contains no published messages: an empty phase is not a result.
"""
import argparse
import datetime as dt
import json
import sys


def iso_ns(s):
    t = dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return int(t.timestamp() * 1e9)


def hms(ns):
    return dt.datetime.fromtimestamp(ns / 1e9, dt.timezone.utc).strftime("%H:%M:%S.%f")[:-3]


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
    ap.add_argument("--label", default="", help="phase name printed in the header")
    args = ap.parse_args()
    if not args.run:
        sys.exit("analyze_phase: empty --run (no runId found in the publisher log?)")
    t0, t1 = iso_ns(args.t_from), iso_ns(args.t_to)
    grace = int(args.grace_s * 1e9)

    by_peer = {}  # peer name -> events from all of its files, in file order
    for p in args.logs:
        evs = events(p)
        peer = next((e["peer"] for e in evs if "peer" in e), p)
        by_peer.setdefault(peer, []).extend(evs)

    pubs = [e for evs in by_peer.values() for e in evs
            if e.get("event") == "publish" and e.get("peer") == args.publisher and e.get("runId") == args.run
            and t0 <= e["publishedAtNs"] < t1]
    seqs = {e["seq"]: e["publishedAtNs"] for e in pubs}
    n = len(seqs)
    label = f"{args.label} " if args.label else ""
    print(f"phase {label}{args.t_from} -> {args.t_to} grace={args.grace_s:g}s: run={args.run} "
          f"publisher={args.publisher} published={n}")
    if n == 0:
        sys.exit(f"analyze_phase: no messages published by {args.publisher} in run {args.run} in this phase")
    for peer in sorted(by_peer):
        if peer == args.publisher:
            continue
        rec = {}
        for e in by_peer[peer]:
            if e.get("event") == "receive" and e.get("runId") == args.run and e.get("seq") in seqs:
                rec.setdefault(e["seq"], e)  # first receipt
        on_time, lat, late = 0, [], []
        for e in rec.values():
            if e["receivedAtNs"] <= t1 + grace:
                on_time += 1
                lat.append(e["latencyMs"])
            else:
                late.append(e["receivedAtNs"])
        never = n - len(rec)
        lat.sort()
        line = (f"  {peer}: on_time={on_time}/{n} ({100 * on_time / n:.1f}%) late={len(late)} never={never} "
                f"p50_ms={pct(lat, 0.5):.2f} p95_ms={pct(lat, 0.95):.2f} p99_ms={pct(lat, 0.99):.2f}")
        if late:
            line += f" late_arrived={hms(min(late))}->{hms(max(late))}"
        print(line)


if __name__ == "__main__":
    main()
