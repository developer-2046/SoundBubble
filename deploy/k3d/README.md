# Deploy to the team k3d cluster (Radar-connected, arm64 Mac)

Run on the Mac that hosts k3d cluster `hackathon`, from the repo root:

```bash
docker build -t soundbubble:0.1 .                 # ~10 min first time (torch + models)
k3d image import soundbubble:0.1 -c hackathon
kubectl create namespace soundbubble
read -rs ES_API_KEY                                # paste the Elastic API key, Enter
kubectl -n soundbubble create secret generic soundbubble-es \
  --from-literal=ES_URL=https://my-elasticsearch-project-cdc18c.es.us-east4.gcp.elastic.cloud \
  --from-literal=ES_API_KEY="$ES_API_KEY" \
  --from-literal=SB_ARCHIVE_TOKEN="$(openssl rand -hex 24)"
```

Then the manifests in this folder are applied through Radar MCP (`apply_resource`), or:
`kubectl apply -f deploy/k3d/app.yaml`.

Open it: `kubectl -n soundbubble port-forward --address 0.0.0.0 svc/app 8080:80` → http://localhost:8080
