#!/usr/bin/env bash
# Evidence that isolation is enforced beyond the policy object (decision D8): real TCP connects to
# the GossipSub port, compared with what the current state should allow.
#
#   during: outside -> isolated FAIL, isolated -> outside FAIL, isolated <-> isolated OK, controls OK
#   after:  every check OK
#
# "Outside" checks run from the probe pod (busybox nc); "isolated" checks run from py-peer-2 with
# Python, because go-peer-2 is distroless and has no shell. Each line reads
#   <time> <PASS|MISMATCH> <from> -> <to> (<ip>:4001): <OK|FAIL> expected <OK|FAIL>
# Exit status is 1 if any check does not match the expectation.
#
# Usage: scripts/verify_isolation.sh during|after
set -euo pipefail
CTX=kind-gossip-lab; NS=gossip-mixed
MODE="${1:?usage: verify_isolation.sh during|after}"
case "$MODE" in during|after) ;; *) echo "mode must be during or after" >&2; exit 2 ;; esac

ip() {
  local v; v=$(kubectl --context "$CTX" -n "$NS" get pod "$1" -o jsonpath='{.status.podIP}')
  [ -n "$v" ] || { echo "no IP for pod $1" >&2; return 1; }
  echo "$v"
}

kubectl --context "$CTX" -n "$NS" get pod probe >/dev/null 2>&1 \
  || { echo "probe pod missing: run 'make test-probe' first" >&2; exit 2; }

mismatches=0
check() { # check <from-pod> <to-pod> <expected OK|FAIL>
  local from="$1" to="$2" want="$3" t r
  t=$(ip "$to")
  if [ "$from" = probe ]; then
    r=$(kubectl --context "$CTX" -n "$NS" exec probe -- \
      sh -c "nc -z -w 3 $t 4001 >/dev/null 2>&1 && echo OK || echo FAIL")
  else
    r=$(kubectl --context "$CTX" -n "$NS" exec "$from" -c peer -- python -c "
import socket, sys
try:
    socket.create_connection(('$t', 4001), timeout=3).close(); print('OK')
except OSError:
    print('FAIL')")
  fi
  local verdict=PASS
  [ "$r" = "$want" ] || { verdict=MISMATCH; mismatches=$((mismatches + 1)); }
  echo "$(date -u +%H:%M:%S) $verdict $from -> $to ($t:4001): $r expected $want"
}

if [ "$MODE" = during ]; then cut=FAIL; else cut=OK; fi
check probe go-peer-2 "$cut"      # outside -> isolated (ingress)
check probe py-peer-2 "$cut"
check py-peer-2 go-peer-0 "$cut"  # isolated -> outside (egress)
check py-peer-2 py-peer-1 "$cut"
check py-peer-2 go-peer-2 OK      # inside the isolated pair
check probe py-peer-0 OK          # controls: outside peers stay reachable
check probe go-peer-1 OK

[ "$mismatches" -eq 0 ] || { echo "verify_isolation: $mismatches check(s) did not match '$MODE'" >&2; exit 1; }
