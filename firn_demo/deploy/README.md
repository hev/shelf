# Deploy: Firn engine + shelf-on-Firn demo → EKS `layer-prod`

Deploys, to namespace `firn` in EKS `layer-prod` (account `186219257916`, us-east-1):
the **Firn engine** (firnflow-api, S3-backed via IRSA, internal ClusterIP) and the
**demo app** (public at `firn.hevlayer.com` via the shared `hev-public` ALB), plus a
one-shot **indexer Job**. The gateway shelf demo is unaffected — both versions coexist.

## One-time prerequisites (account 186219257916, profile `mesh-916`) — already provisioned

```bash
# S3 bucket (public access blocked)
aws s3api create-bucket --bucket hev-firn-186219257916 --profile mesh-916 --region us-east-1
aws s3api put-public-access-block --bucket hev-firn-186219257916 \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true \
  --profile mesh-916 --region us-east-1

# IRSA role (trust = cluster OIDC for system:serviceaccount:firn:firn) + minimal S3 policy
#   role: layer-prod-firn-sa-role  (s3:Get/Put/Delete/ListBucket on hev-firn-186219257916[/*])

# ECR repos
aws ecr create-repository --repository-name firnflow-api    --profile mesh-916 --region us-east-1
aws ecr create-repository --repository-name shelf-firn-demo --profile mesh-916 --region us-east-1
```

## Secret (out-of-band — never committed)

```bash
kubectl -n firn create secret generic firn-keys \
  --from-literal=api-key="$(openssl rand -hex 32)" \
  --from-literal=admin-key="$(openssl rand -hex 32)" \
  --from-literal=metrics-token="$(openssl rand -hex 32)"
```

## Deploy

```bash
./firn_demo/deploy/deploy.sh        # ECR login → depot build+push both images → apply → index
# or, manifests only (images already pushed):
SKIP_BUILD=1 ./firn_demo/deploy/deploy.sh
```

Manual equivalent:

```bash
kubectl apply -f firn_demo/deploy/00-namespace-sa.yaml
# (create the firn-keys secret — see above)
kubectl apply -f firn_demo/deploy/10-firn-engine.yaml
kubectl apply -f firn_demo/deploy/20-demo.yaml
kubectl apply -f firn_demo/deploy/40-ingress.yaml
kubectl -n firn rollout status deploy/firn deploy/shelf-firn-demo
kubectl apply -f firn_demo/deploy/30-indexer-job.yaml   # loads ~10k + builds indexes
```

## DNS

No external-dns on this cluster, so add the record manually (profile `mesh-916`):
`firn.hevlayer.com` → the shared `hev-public` ALB
(`k8s-hevpublic-…elb.amazonaws.com`), as an ALIAS A (or CNAME), matching the other
`*.hevlayer.com` demos. TLS is auto-discovered from the wildcard `*.hevlayer.com`
ACM cert on the shared ALB (no `certificate-arn` needed).

## Verify

```bash
kubectl -n firn get pods,svc,ingress
kubectl -n firn port-forward svc/firn 3000:3000 &   # /health, /ns/shelf-books
curl -s https://firn.hevlayer.com/api/config
```

## v2 — genre facets (`[]string`), deploy AFTER v1 is live

v1 (above) runs search + cache with the genre rail hidden. v2 adds Firn's
`[]string` attribute type so `genres` is stored, faceted (count per genre), and
filtered (`array_has`). It is a **rolling update on the same manifests** — new
images + a reindex (v1 stored no genres). No manifest or DNS change.

Prereq: v1 deployed and healthy. Then, with cluster apply access:

```bash
ECR=186219257916.dkr.ecr.us-east-1.amazonaws.com
aws ecr get-login-password --profile mesh-916 --region us-east-1 | docker login --username AWS --password-stdin $ECR

# 1) Build + push both v2 images
( cd ../firnflow && depot build -t $ECR/firnflow-api:latest --push . )            # engine: []string support
depot build --project t4vlld595v -f firn_demo/Dockerfile -t $ECR/shelf-firn-demo:latest --push .  # demo: reconciled client

# 2) Roll the new images
kubectl -n firn rollout restart deploy/firn deploy/shelf-firn-demo
kubectl -n firn rollout status  deploy/firn deploy/shelf-firn-demo --timeout=300s

# 3) Reindex so genres land as []string. Delete the namespace first so the
#    columns are created at table-creation time (avoids schema-evolution on the
#    list column). The admin key authorizes DELETE; it's derived data, safe to drop.
ADMIN=$(kubectl -n firn get secret firn-keys -o jsonpath='{.data.admin-key}' | base64 -d)
kubectl -n firn run firn-reset --rm -i --restart=Never --image=curlimages/curl -- \
  -s -X DELETE -H "Authorization: Bearer $ADMIN" http://firn.firn.svc.cluster.local:3000/ns/shelf-books
kubectl -n firn delete job shelf-firn-indexer --ignore-not-found
kubectl apply -f firn_demo/deploy/30-indexer-job.yaml
kubectl -n firn wait --for=condition=complete job/shelf-firn-indexer --timeout=1800s

# 4) Verify the genre facet end to end
kubectl -n firn port-forward svc/firn 3000:3000 &
curl -s -X POST localhost:3000/ns/shelf-books/facet -H 'content-type: application/json' \
  -d '{"fields":["genres"],"top":8}'    # -> per-genre counts
# then open https://firn.hevlayer.com — the "Narrow by genre" rail is now populated;
# clicking a genre sends filter array_has(genres,'<g>').
```
