#!/usr/bin/env bash
# Evidence that isolation is enforced beyond the policy object (decision D8).
# Expected: isolated <-> non-isolated probes fail; isolated <-> isolated and control probes succeed.
set -uo pipefail
CTX=kind-gossip-lab; NS=gossip-mixed
ip() { kubectl --context "$CTX" -n "$NS" get pod "$1" -o jsonpath='{.status.podIP}'; }
probe() { # probe <label> <target-pod>
  local t; t=$(ip "$2")
  local r; r=$(kubectl --context "$CTX" -n "$NS" exec probe -- sh -c "nc -z -w 3 $t 4001 >/dev/null 2>&1 && echo OK || echo FAIL")
  echo "$(date -u +%H:%M:%S) $1 -> $2 ($t:4001): $r"
}
probe "non-isolated probe" go-peer-2
probe "non-isolated probe" py-peer-2
probe "non-isolated control" py-peer-0
probe "non-isolated control" go-peer-1
