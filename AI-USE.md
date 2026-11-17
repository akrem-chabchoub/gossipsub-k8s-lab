# AI use

I used an AI coding assistant (Claude Code) as a helper. I chose the implementations, ran every scenario, read the results, and decided what went into the report. I can explain every file in this repository.

## Where AI helped

- **First drafts of boilerplate:** Helm values, Kubernetes manifests, shell wrappers, and the Python script that
  generates the Grafana dashboard JSON.
- **Library lookup:** go-libp2p-pubsub tracer events, py-libp2p GossipSub parameters, and LogsQL and PromQL syntax.
- **Review:** a second reading of the Go and Python peer code, and a check of the repository against the brief.
- **Writing:** tightening the wording of the README and the report.

## How I checked its output

Nothing was accepted on generated text alone. Each piece was run on the kind cluster and checked against a measurement.

- **Tests:** `go vet ./...` and `go test ./...` for the Go peer, `python -m unittest` for the Python peer.
- **Behaviour against expectations.** These are cases where checking changed the result:
  - A NetworkPolicy on its own did not drop connections that were already open. Connection counts stayed at 5.
    The isolated pods are now restarted under the policy. The invalid run is kept as evidence.
  - The alert stayed firing for up to 5 minutes after recovery. Its reducer took the minimum over a 5-minute
    window. I changed it to `last` and re-tested that it fires and resolves.
  - Python peers did not return to 5 connections after recovery. Two plausible fixes (clearing a negative cache,
    a dial timeout) did not help. I reproduced the stall in the cluster and found the cause: a stale pod IP in
    the peerstore. Re-resolving DNS on every dial fixed it.
  - Vector's `kubernetes_logs` source shipped nothing. Querying VictoriaLogs showed this, and I replaced it with a
    `file` source.
  - A multi-agent review against the brief flagged that the isolation-phase "delivered" figure (64%) counted
    receipts that arrived after recovery, and that the isolated peers' pre-cut logs were lost when their pods were
    restarted. I re-read the receipt logs, confirmed both, and changed the analysis (grace 0 for the isolation phase,
    logs saved before the restart). The corrected figure is 0% during the cut.
  - The same review said the Python peer's `fromName` was the original author, not the forwarder. I checked the
    py-libp2p source before changing it.
  - The repository review flagged that `kubectl logs` misses rotated log files. I confirmed it by listing the pod
    log files on the kind node before acting on it.
- **Claims in the report** are marked as confirmed only when backed by logs, metrics, or command output. Everything
  else is labelled a hypothesis.
