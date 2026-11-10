# Radar usage and AI investigation (evidence)

Cluster: `hackathon` (k3d, single arm64 node, Radar 1.20.2), Radar Cloud org "bob".
Workload: Deployment `soundbubble/app` (API + UI + ASR + embeddings; Elastic Cloud for storage).

## What we did through Radar MCP (from Claude Code)
1. `list_clusters`, `list_namespaces`, `get_resource node`: discovered the cluster (10 CPU, 8 GiB, local-path storage, Traefik, no Argo CD, no Prometheus).
2. `apply_resource` (dry run, then real): created namespace `soundbubble`; validated PVC + Deployment + Service server-side before the image existed.
3. `list_resources secrets`: confirmed Secret `soundbubble-es` keys (`ES_URL`, `ES_API_KEY`, `SB_ARCHIVE_TOKEN`) without reading values; the Secret was created by a teammate with kubectl, so the key never passed through git or Radar.
4. `apply_resource`: deployed; rollout complete, 1/1 ready, PVC bound.
5. `get_workload_logs`: verified probes 200 and the pod reaching Elastic Cloud (`GET /` 200, index HEAD 200, `_search` 200).
6. `top_resources`: live usage 67 Mi / 4 mCPU vs requests 1536 Mi / 500 m (models load lazily, not yet loaded).

## AI investigation
* Run `chat_r-1BGyT1SmY9rsW7` (started in Radar UI): gathered `diagnose` + `issues`, then stopped; our
  follow-up question on sizing returned Radar's "An internal error occurred". Not counted as a result.
* Run `chat_qAti0PYK4KNbEvBo` (started via MCP, 23:29:45Z, done 23:30:36Z), verdict **healthy**,
  confidence 0.9: 1/1 ready, no restarts, `/healthz` + `/readyz` 200 every 10–20 s, no Warning events,
  only creation + readiness changes. Noted one posture finding: `automountServiceAccountToken` (medium).

## Corrective action and verification
* Baseline `get_cluster_audit` (namespace soundbubble): 3 medium findings:
  `automountServiceAccountToken`, `readOnlyRootFs`, `singleReplica`.
* Fix: SoundBubble never calls the Kubernetes API, so we set
  `spec.template.spec.automountServiceAccountToken: false` (`patch_resource`, generation 2), and
  mirrored it in `deploy/k3d/app.yaml` and `deploy/app.yaml`.
* Verify: `wait_for rollout` → "rolled out: 1/1 available"; audit re-run → 2 findings, token finding gone.
* Deliberately not changed:
  * `readOnlyRootFs`: faster-whisper / sentence-transformers write caches and temp files; would need
    emptyDir mounts and re-testing.
  * `singleReplica`: the data PVC is ReadWriteOnce (local-path), so a 2nd replica needs shared
    storage. Accepted for a demo.

## Open question (not answered by Radar)
Memory sizing after the ASR + embedding models load. Without Prometheus there is no history;
`top_resources` after a real save on the cluster would answer it.
