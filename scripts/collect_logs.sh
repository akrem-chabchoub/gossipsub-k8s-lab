#!/usr/bin/env bash
# Collect the complete log of each peer pod into RESULTS_DIR/<pod>.log, one JSON line per event.
#
# Why not `kubectl logs`: kubelet rotates container logs at 10 MiB, and `kubectl logs` returns only the
# current file. A peer at 10 msg/s fills 10 MiB in about 40 minutes, so `kubectl logs` silently drops
# older receipts and the delivery analysis reports them as never received. This script reads every
# file (rotated, gzipped, and current, for every container restart) straight from the kind node,
# in order, and strips the CRI prefix ("<time> <stream> <P|F> <message>").
#
# Logs of a pod that was deleted and recreated start at the new pod; the old pod's files are gone.
#
# Usage: scripts/collect_logs.sh RESULTS_DIR NAMESPACE POD [POD ...]
set -euo pipefail
OUT="${1:?usage: collect_logs.sh RESULTS_DIR NAMESPACE POD [POD ...]}"; NS="${2:?namespace}"; shift 2
NODE="${CLUSTER:-gossip-lab}-control-plane"
mkdir -p "$OUT"

for pod in "$@"; do
  # Inside the node: for each pod directory (oldest first), each restart number, print the rotated
  # files in timestamp order and then the current file.
  docker exec "$NODE" sh -c '
    for d in $(ls -dtr /var/log/pods/'"$NS"'_'"$pod"'_*/peer 2>/dev/null); do
      for n in $(ls "$d" | sed -n "s/^\([0-9][0-9]*\)\.log.*/\1/p" | sort -un); do
        for f in $(ls "$d/$n.log."* 2>/dev/null | sort); do
          case "$f" in *.gz) zcat "$f" ;; *) cat "$f" ;; esac
        done
        [ -f "$d/$n.log" ] && cat "$d/$n.log"
      done
    done' \
  | python3 -c '
import sys
buf = []
for line in sys.stdin:
    parts = line.rstrip("\n").split(" ", 3)
    if len(parts) < 3:
        continue
    buf.append(parts[3] if len(parts) == 4 else "")
    if parts[2] == "F":  # P = partial line, F = full (last chunk)
        print("".join(buf))
        buf = []
' > "$OUT/$pod.log"
  echo "$pod: $(wc -l < "$OUT/$pod.log" | tr -d ' ') lines -> $OUT/$pod.log"
done
