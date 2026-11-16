CLUSTER  ?= gossip-lab
CTX      := kind-$(CLUSTER)
GO_IMG   := gossip/go-peer:0.1.1
PY_IMG   := gossip/py-peer:0.1.5
CALICO   := https://raw.githubusercontent.com/projectcalico/calico/v3.30.3/manifests/calico.yaml

.PHONY: up cluster-up build deploy-go-only py-check deploy-mixed status down observability

# Single-node kind cluster with Calico. The default CNI is disabled so NetworkPolicy is enforced.
cluster-up:
	./scripts/cluster-up.sh

build:
	docker build -t $(GO_IMG) apps/go-peer
	docker build -t $(PY_IMG) apps/py-peer
	kind load docker-image $(GO_IMG) $(PY_IMG) --name $(CLUSTER)

# Six Go peers (primary implementation).
deploy-go-only:
	kubectl --context $(CTX) apply -k deploy/k8s/overlays/go-only
	kubectl --context $(CTX) -n gossip-lab rollout status sts/go-peer --timeout=300s

# Validation stage: three py-libp2p peers in their own namespace.
py-check:
	kubectl --context $(CTX) apply -f deploy/k8s/checks/py-only.yaml
	kubectl --context $(CTX) -n gossip-lab-py rollout status sts/py-peer --timeout=300s

# Mixed network: three Go peers plus three py-libp2p peers in namespace gossip-mixed.
deploy-mixed:
	kubectl --context $(CTX) apply -f deploy/k8s/mixed/mixed.yaml
	kubectl --context $(CTX) -n gossip-mixed rollout status sts/go-peer --timeout=300s
	kubectl --context $(CTX) -n gossip-mixed rollout status sts/py-peer --timeout=300s

status:
	kubectl --context $(CTX) get pods -A -l app.kubernetes.io/part-of=gossip-lab -o wide
	kubectl --context $(CTX) -n gossip-mixed get pods -o wide

down:
	kind delete cluster --name $(CLUSTER)

# One command for the observability stack (Prometheus, Grafana, Vector, VictoriaLogs).
observability:
	./scripts/observability-up.sh

# Whole lab in one command: cluster, images, both networks, observability.
up: cluster-up build deploy-go-only deploy-mixed observability forward
	@echo "Lab is up. Next: make status, or see the guide for port-forwards."

# ---- Tests: isolate two peers (go-peer-2, py-peer-2) from the rest of gossip-mixed ----
# Usage: make test-isolate RUN=mytest   then   make test-verify   then   make test-recover RUN=mytest
# RUN names the results folder results/RUN (default: timestamp).
RUN     ?= $(shell date -u +%Y%m%dT%H%M%SZ)
RESULTS := results/$(RUN)
NS_MIXED := gossip-mixed

test-probe:
	kubectl --context $(CTX) apply -f deploy/k8s/mixed/probe.yaml
	kubectl --context $(CTX) -n $(NS_MIXED) wait --for=condition=Ready pod/probe --timeout=120s

# Apply the isolation policy, then restart the two isolated pods so the cut drops existing
# connections (decision D11). Timestamp goes to $(RESULTS)/events.log.
test-isolate:
	scripts/isolate.sh $(RESULTS)
	kubectl --context $(CTX) -n $(NS_MIXED) delete pod go-peer-2 py-peer-2
	kubectl --context $(CTX) -n $(NS_MIXED) wait --for=condition=Ready pod/go-peer-2 pod/py-peer-2 --timeout=240s

# Probe the cut: expected FAIL between isolated and non-isolated peers, OK inside each group.
test-verify:
	scripts/verify_isolation.sh

# Remove the isolation policy and record the end time.
test-recover:
	scripts/recover.sh $(RESULTS)

# Scenario 1, baseline (about 7 minutes): wait for a full mesh, settle 60 s, measure 300 s,
# then delivery completeness and latency for the window. NS=gossip-lab measures the six-Go network.
NS ?= gossip-mixed
test-scenario1:
	NS=$(NS) caffeinate -dimsu scripts/run_scenario1.sh $(RUN)

# Full scenario 2 end to end (about 9 minutes), then per-phase delivery analysis.
test-scenario2:
	caffeinate -dimsu scripts/run_scenario2.sh $(RUN)
	scripts/analyze_run.sh $(RUN)

.PHONY: test-probe test-isolate test-verify test-recover test-scenario1 test-scenario2

# Delete all peer nodes: the six-Go network, the mixed network, and the py-only check.
# Namespaces are removed, which takes their StatefulSets, Services, pods, probe pod and
# NetworkPolicies with them. The cluster and the observability stack are kept.
delete-peers:
	kubectl --context $(CTX) delete namespace gossip-lab gossip-mixed gossip-lab-py --ignore-not-found

.PHONY: delete-peers

# Background port-forwards for Grafana (3100), Prometheus (9090) and VictoriaLogs (9428).
# `make up` runs this at the end. `make unforward` stops them.
forward:
	./scripts/port-forward.sh start

unforward:
	./scripts/port-forward.sh stop

.PHONY: forward unforward
