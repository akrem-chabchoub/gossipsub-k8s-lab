# VictoriaLogs queries (LogsQL)

Access: `kubectl --context kind-gossip-lab -n observability port-forward svc/victoria-logs-victoria-logs-single-server 9428:9428`
then open http://localhost:9428/select/vmui, or query the API at `/select/logsql/query`.

Log fields (from the peer JSON): `event`, `runId`, `peer`, `impl`, `publisherName`, `seq`,
`publishedAtNs`, `receivedAtNs`, `latencyMs`, `fromName`, `peerName`. Kubernetes fields:
`kubernetes.pod_namespace`, `kubernetes.pod_name`.

## Per-message receipt timeline (required query)

Which application peers observed message (runId, seq), and when:

    _time:1h runId:RUN_ID seq:SEQ event:receive
      | fields _time, peer, impl, publisherName, latencyMs, fromName
      | sort by (_time)

Publisher side, for the same message:

    _time:1h runId:RUN_ID seq:SEQ event:publish | fields _time, peer

## Receipts per peer over the last 5 minutes

    _time:5m event:receive | stats by (kubernetes.pod_namespace, peer) count() as receipts

## Connection and mesh events (connectivity evidence)

    _time:30m (event:connect or event:disconnect or event:graft or event:prune)
      | stats by (event, kubernetes.pod_namespace, peer) count() as n

## Errors and warnings

    _time:30m (event:dial_error or event:publish_error or event:decode_error)
      | stats by (event, peer) count() as n

## Telemetry gap check: lines per peer per minute

    _time:5m kubernetes.pod_namespace:gossip-* | stats by (kubernetes.pod_name) count() as lines
