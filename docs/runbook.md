# runbook — underwriting-assist agent

operational playbook for the on-call engineer. if youre reading this at 2am, sorry...hopefully the answer is here.

## quick reference

| item | value |
|------|-------|
| namespace | `underwriting-agent` |
| deployment | `underwriting-agent` |
| service port | 80 → 8080 |
| healthcheck | GET `/healthz` |
| readiness | GET `/readyz` (checks postgres + S3) |
| logs | `kubectl logs -n underwriting-agent -l app.kubernetes.io/name=underwriting-agent` |
| grafana | `grafana.internal/d/underwriting-agent-overview` |
| pagerduty service | underwriting-agent-prod |

## common scenarios

### 1. HighP95Latency / HighP99Latency alert

**likely cause**: anthropic API is slow or degraded

**diagnosis**:
```bash
# check LLM call latency specifically
kubectl logs -n underwriting-agent -l app.kubernetes.io/name=underwriting-agent --tail=50 | grep latency_ms

# check anthropic status
curl -s https://status.anthropic.com/api/v2/status.json | jq .status
```

**actions**:
- if anthropic is degraded: nothing we can do except wait. the service handles this gracefully — requests will be slower but wont fail
- if its our infra: check pod resource usage, check if HPA is scaling
- if its postgres: check readiness probe, RDS metrics in cloudwatch
- consider: if latency is consistently above SLO, we might need to switch to bedrock as fallback

### 2. HighErrorRate alert

**diagnosis**:
```bash
# check recent errors
kubectl logs -n underwriting-agent -l app.kubernetes.io/name=underwriting-agent --tail=100 | grep -i error

# check pod status
kubectl get pods -n underwriting-agent

# check recent events
kubectl get events -n underwriting-agent --sort-by=.lastTimestamp | tail -20
```

**common causes**:
- anthropic API key expired or rate limited → check secrets, rotate if needed
- postgres connection issues → check RDS status in AWS console
- S3 bucket permissions changed → check IAM role
- OOM kills → check memory usage, consider increasing limits

### 3. PodCrashLooping alert

**diagnosis**:
```bash
# check why pods are restarting
kubectl describe pod -n underwriting-agent <pod-name>

# check logs from the previous container instance
kubectl logs -n underwriting-agent <pod-name> --previous

# check if its OOM
kubectl get pods -n underwriting-agent -o json | jq '.items[].status.containerStatuses[].lastState'
```

**common causes**:
- OOM: increase memory limits in the kustomize overlay
- crash on startup: check env vars, secrets, database connectivity
- readiness probe failure: postgres or S3 might be down

### 4. HPAMaxedOut alert

the autoscaler has been at max replicas for 15+ minutes. this means traffic is higher than our ceiling or something is wrong.

**diagnosis**:
```bash
kubectl get hpa -n underwriting-agent
kubectl top pods -n underwriting-agent
```

**actions**:
- check if this is legitimate burst traffic (is it 9-11am or 2-4pm ET?)
- if legitimate: temporarily increase max replicas
  ```bash
  kubectl patch hpa underwriting-agent -n underwriting-agent --type=merge -p '{"spec":{"maxReplicas": 20}}'
  ```
- if not legitimate: check for a stuck client sending repeated requests
- long term: update the HPA max in the kustomize overlay and commit

### 5. ReadinessProbeFailure alert

the readiness probe hits `/readyz` which checks postgres AND S3. if its failing, one of those is down.

**diagnosis**:
```bash
# exec into a pod and check manually
kubectl exec -n underwriting-agent deploy/underwriting-agent -- curl -s localhost:8080/readyz | jq .

# check RDS
aws rds describe-db-instances --db-instance-identifier underwriting-agent-prod --query 'DBInstances[0].DBInstanceStatus'

# check S3
aws s3 ls s3://underwriting-agent-prod-loan-docs/
```

**actions**:
- if RDS is down: check AWS console for maintenance windows, failover events
- if S3 is unreachable: extremely rare, likely a network issue or IAM change
- pods will be taken out of the service automatically (readiness probe removes them from endpoints)

## deployment procedures

### normal deployment (via CI/CD)

1. merge PR to main
2. CI runs lint + test + build + push
3. auto-deploys to dev
4. manually approve staging deployment in github
5. manually approve prod deployment in github
6. verify in grafana that error rate / latency are stable after deploy

### emergency rollback

```bash
# check deployment history
kubectl rollout history deployment/underwriting-agent -n underwriting-agent

# rollback to previous version
kubectl rollout undo deployment/underwriting-agent -n underwriting-agent

# verify
kubectl rollout status deployment/underwriting-agent -n underwriting-agent
```

### manual deployment (break glass only)

only do this if CI/CD is broken and you need to ship a fix NOW.

```bash
# build and push locally
docker build -t <ECR_URL>/underwriting-agent:<tag> .
docker push <ECR_URL>/underwriting-agent:<tag>

# update deployment
kubectl set image deployment/underwriting-agent -n underwriting-agent agent=<ECR_URL>/underwriting-agent:<tag>

# watch rollout
kubectl rollout status deployment/underwriting-agent -n underwriting-agent
```

document what you did and create a follow-up ticket to get CI/CD fixed.

## secrets rotation

### scheduled rotation (quarterly)

this should happen automatically via secrets manager rotation lambda. verify it worked:

```bash
# check the secret was updated
aws secretsmanager describe-secret --secret-id underwriting-agent/prod/anthropic-api-key --query 'LastRotatedDate'

# verify pods picked up the new secret (external-secrets-operator polls every 1h)
kubectl get externalsecret -n underwriting-agent -o wide

# if pods havent picked it up, force a restart
kubectl rollout restart deployment/underwriting-agent -n underwriting-agent
```

### emergency rotation (credential compromised)

1. generate new credentials immediately
2. update in secrets manager
3. force external-secrets sync:
   ```bash
   kubectl annotate externalsecret underwriting-agent-secrets -n underwriting-agent force-sync=$(date +%s)
   ```
4. restart pods:
   ```bash
   kubectl rollout restart deployment/underwriting-agent -n underwriting-agent
   ```
5. revoke old credentials
6. file incident report

## scaling

### horizontal (more pods)

handled by HPA automatically. to adjust:
```bash
# temporary
kubectl patch hpa underwriting-agent -n underwriting-agent --type=merge -p '{"spec":{"minReplicas": 5, "maxReplicas": 20}}'

# permanent — update k8s/overlays/prod/hpa-patch.yaml and commit
```

### vertical (bigger pods)

update resource limits in `k8s/overlays/prod/deployment-patch.yaml` and deploy.

current prod sizing:
- requests: 500m CPU, 512Mi memory
- limits: 2 CPU, 1Gi memory

### node scaling

EKS managed node group auto-scales based on pending pods. if pods are stuck in Pending:
```bash
# check if nodes are at capacity
kubectl describe nodes | grep -A 5 "Allocated resources"

# check node group
aws eks describe-nodegroup --cluster-name underwriting-agent-prod --nodegroup-name underwriting-agent-prod-main
```

## maintenance windows

- RDS: sundays 4:00-5:00 UTC (auto minor version upgrades)
- EKS: coordinate with platform team, use PDB to ensure availability during node upgrades
- app deploys: anytime during business hours, avoid peak processing windows (9-11am, 2-4pm ET)

## useful commands cheat sheet

```bash
# tail logs in real time
kubectl logs -n underwriting-agent -l app.kubernetes.io/name=underwriting-agent -f --tail=50

# check resource usage
kubectl top pods -n underwriting-agent

# check all resources in namespace
kubectl get all -n underwriting-agent

# port-forward for local debugging
kubectl port-forward -n underwriting-agent svc/underwriting-agent 8080:80

# test the service locally via port-forward
curl http://localhost:8080/healthz
curl -X POST http://localhost:8080/v1/items/process -H "Content-Type: application/json" -d @examples/sample_loan.json

# check events for issues
kubectl get events -n underwriting-agent --sort-by=.lastTimestamp

# describe deployment for rollout config
kubectl describe deployment underwriting-agent -n underwriting-agent
```
