# AGENTS.md

Guidance for coding agents working in this repository. Human-facing setup is in `README.md`.

## Project

A GossipSub network on a local kind cluster: three Go peers (go-libp2p) and three Python peers (py-libp2p) on one
topic, plus a six-peer Go-only network. Prometheus, Grafana, Vector, and VictoriaLogs observe it. The main experiment
cuts two peers off with a NetworkPolicy and measures delivery, connectivity, and recovery.

## Commands

| Task | Command |
|---|---|
| Whole lab | `make up` |
| Status | `make status` |
| Go tests | `cd apps/go-peer && go vet ./... && go test ./...` |
| Python tests | see below |
| Shell syntax | `for f in scripts/*.sh; do bash -n "$f"; done` |
| Dry-run Make | `make -n <target>` |
| Isolation test | `make test-isolate RUN=x`, `make test-verify`, `make test-recover RUN=x` |
| Full scenario | `make test-scenario2 RUN=x` (about 9 minutes) |

Python tests need the peer's dependencies, which are only in the image. Run them against the working tree:

```bash
docker run --rm \
  -v "$PWD/apps/py-peer/peer:/app/peer:ro" -v "$PWD/apps/py-peer/tests:/app/tests:ro" \
  --entrypoint python gossip/py-peer:0.1.5 -m unittest discover -s tests -t .
```

Before finishing a change, run the tests for every part you touched, and the shell syntax check for any script.

## Safety rules

- **Always pass `--context kind-gossip-lab`** (or `--kube-context` for Helm). Other clusters exist on this machine.
  Never change the current kubectl context.
- **Deletes are scoped.** Delete only labelled lab objects (`gossip.lab/experiment=...`), the lab namespaces
  (`gossip-lab`, `gossip-mixed`, `gossip-lab-py`), or the two isolated pods (`go-peer-2`, `py-peer-2`).
  Ask before `make down`, `make delete-peers`, or deleting anything else.
- **Never kill processes by pattern** (`pkill -f`). Stop only processes you started, by PID.
  `scripts/port-forward.sh stop` stops the forwards it started.
- **Do not commit** `local/` or `results/`. Both are ignored on purpose.

## Conventions

- **Commits:** Conventional Commits (`feat(scope):`, `fix(scope):`, `docs(scope):`, `chore(scope):`).
  Scopes in use: `peers`, `py-peer`, `go-peer`, `observability`, `scripts`, `readme`, `gitignore`.
- **Image tags:** bump the tag on every rebuild (`gossip/py-peer:0.1.6`, not a re-used `0.1.5`). kind keeps the old
  image under a re-used tag. Update the tag in `Makefile` and in the manifests together.
- **Peer parity:** the Go and Python peers share the same environment variables, Prometheus metric names, labels,
  and JSON log fields. A change to one peer's metrics or log fields must be made in the other too.
- **Shell:** scripts are bash (`#!/usr/bin/env bash`, `set -euo pipefail`). The interactive shell here is zsh, which
  does not word-split unquoted variables. Run multi-word loops through `bash -c` or a script.
- **macOS:** `sed -i ''` (BSD sed). There is no `timeout` command.
- **Comments** say why, not history. Decision IDs (`D1` to `D15`) in comments refer to the maintainer's decision log.

## Gotchas

- **The kind node pulls images slowly.** `scripts/cluster-up.sh` and `scripts/observability-up.sh` pull images on the
  host and import them into the node with `ctr`. `kind load docker-image` fails on these multi-platform images.
- **`make build` can fail** with `auth.docker.io: i/o timeout` when Docker Desktop's DNS is broken, even if the host
  resolves names. Reuse existing images with `kind load docker-image gossip/go-peer:0.1.1 gossip/py-peer:0.1.5
  --name gossip-lab`.
- **Port 3000 belongs to another Grafana** on this machine. The lab's Grafana is on 3100.
- **py-libp2p needs non-default settings** at 10 msg/s: a 1 s heartbeat, a raised per-topic rate limit, and a one-way
  dial rule (only the lexicographically smaller name dials). Do not revert these.
- **py-libp2p keeps stale addresses** after a peer restarts. The redial loop re-resolves DNS and clears the
  peerstore on every attempt. Keep that, or restarted peers fail with
  `handshake failed when upgrading to secure connection`.
- **Isolation requires a pod restart.** Applying the NetworkPolicy alone does not drop existing connections, so
  `make test-isolate` restarts `go-peer-2` and `py-peer-2`.
- **The alert reducer is `last`.** Using `min` over the 5-minute window keeps the alert firing for about 5 minutes after
  recovery.
- **Latency assumes one host clock.** All pods run on one kind node. Do not compare latency across machines.

## Layout

```
apps/go-peer/       Go peer (primary)
apps/py-peer/       Python peer (same contract as the Go peer)
deploy/             kind config and Kubernetes manifests
observability/      Grafana dashboard, Helm values, LogsQL queries
scripts/            cluster setup, observability, port-forwards, isolation, analysis
```
