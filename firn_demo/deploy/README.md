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

## Updating after the firn fork gains metadata columns + facets

```bash
( cd ../firnflow && depot build -t 186219257916.dkr.ecr.us-east-1.amazonaws.com/firnflow-api:latest --push . )
kubectl -n firn rollout restart deploy/firn
kubectl -n firn delete job shelf-firn-indexer && kubectl apply -f firn_demo/deploy/30-indexer-job.yaml
# genre rail + filter light up with no demo code change
```
