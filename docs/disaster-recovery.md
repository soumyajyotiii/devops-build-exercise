# disaster recovery

## targets from spec

| metric | target |
|--------|--------|
| RPO (recovery point objective) | 1 hour |
| RTO (recovery time objective) | 30 minutes |
| availability (today) | 99.5% |
| availability (12-month) | 99.9% |
| idempotency | retries must not produce duplicate emails or duplicate task records |

## what can go wrong (and what we do about it)

### 1. pod failures

**impact**: individual requests fail, load balanced to other pods
**RPO**: 0 (no data loss — pods are stateless)
**RTO**: seconds (k8s restarts the pod, other pods handle traffic immediately)

**how we handle it**:
- minimum 3 pods in prod, spread across AZs (topologySpreadConstraints)
- PDB ensures at least 1 pod is always available during disruptions
- HPA replaces failed pods and scales up if needed
- liveness probe detects hung processes, readiness probe removes unhealthy pods from service

### 2. RDS failure

**impact**: all item processing stops (cant write audit records, cant read loan data)
**RPO**: depends on failure mode
**RTO**: ~5 minutes (multi-AZ failover)

**how we handle it**:
- **multi-AZ deployment**: standby replica in a different AZ, automatic failover
- **automated backups**: daily snapshots, retained for 30 days in prod
- **point-in-time recovery**: transaction logs retained, can restore to any second within the retention window
- **RPO for hardware failure**: 0 (synchronous replication to standby)
- **RPO for data corruption**: minutes (restore from point-in-time backup before corruption)

**recovery procedure**:
```bash
# check RDS status
aws rds describe-db-instances --db-instance-identifier underwriting-agent-prod

# if multi-AZ failover happened, the endpoint stays the same
# pods will reconnect automatically (sqlalchemy pool_pre_ping=True)

# if need to restore from snapshot
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier underwriting-agent-prod-restored \
  --db-snapshot-identifier <snapshot-id>
```

### 3. S3 failure

**impact**: cant reference uploaded documents (but item processing might still work for items that dont need doc references)

S3 has 99.999999999% durability and 99.99% availability. an S3 outage would be a major AWS incident affecting everyone.

**how we handle it**:
- versioning enabled (protects against accidental deletion)
- server-side encryption with KMS
- bucket policy prevents public access
- cross-region replication could be added if we need even more durability (probably overkill)

### 4. anthropic API outage

**impact**: cant classify items or generate responses. the service returns errors for item processing.

**how we handle it**:
- the app already has a mock mode (LLM_PROVIDER=mock) but thats not useful in prod
- **bedrock fallback**: configure AWS Bedrock as secondary provider. if anthropic is down, route to bedrock
- **circuit breaker** (future): after N consecutive failures, stop calling the LLM and escalate all items to human review
- this is a graceful degradation — items still get processed, just with "escalate to human" as the action

**RPO**: 0 (no data loss — items are still in postgres waiting to be processed)
**RTO**: depends on anthropic. we can switch to bedrock manually in ~5 minutes (update configmap and restart pods)

### 5. full AZ failure

**impact**: pods in that AZ go down, RDS might failover

**how we handle it**:
- pods spread across 3 AZs (topologySpreadConstraints)
- RDS multi-AZ with automatic failover
- NAT gateway per AZ in prod (so remaining AZs keep outbound connectivity)
- ALB automatically routes to healthy AZs

**RTO**: ~2 minutes (pods in other AZs continue serving, RDS fails over)
**RPO**: 0 (multi-AZ replication is synchronous)

### 6. full region failure

**impact**: everything goes down

this is a catastrophic scenario. for 99.5% availability (current target), single-region with multi-AZ is sufficient. for the 99.9% target at 12 months, we should evaluate multi-region.

**current approach**: accept the risk for now, document it as a known limitation
**future approach**: 
- pilot light in us-west-2 (RDS read replica, S3 cross-region replication, standby EKS cluster)
- DNS failover via Route53 health checks
- estimated additional cost: ~$500/month

### 7. secrets compromise

**impact**: attacker has API keys or DB credentials

**how we handle it**:
- emergency rotation procedure in runbook
- secrets manager supports immediate rotation
- revoke old credentials, generate new ones, force sync to k8s, restart pods
- total RTO: ~10 minutes if runbook is followed

### 8. data corruption (bad deploy, bug)

**impact**: audit records or loan data might be corrupted

**how we handle it**:
- deployment strategy: rolling update with maxUnavailable=0 (no downtime)
- canary-like behavior: new pods must pass startup + readiness probes before old pods are terminated
- immediate rollback: `kubectl rollout undo`
- RDS point-in-time recovery for data corruption
- audit records are insert-only (no updates/deletes possible)

## idempotency

the spec explicitly requires idempotent processing — retries must not produce duplicate outbound emails or duplicate task records.

**how the app handles it**:
- each item has a unique `item_id`
- the audit record includes the `item_id` — before processing, check if an audit record already exists
- outbound email actions are queued, not sent inline — the queue consumer deduplicates
- tool stubs generate a unique `task_id` — retries produce new task records but the downstream system deduplicates on item_id

**how the infra supports it**:
- postgres unique constraints on (loan_id, item_id, action) tuples
- SQS FIFO queues for email sending (message deduplication built in)

## backup schedule

| resource | backup type | frequency | retention |
|----------|------------|-----------|-----------|
| RDS | automated snapshots | daily | 30 days |
| RDS | transaction logs | continuous | 30 days |
| S3 | versioning | continuous | indefinite |
| terraform state | S3 versioning | on every apply | indefinite |
| audit logs | postgres + future S3 archive | continuous | 7 years |

## testing DR

we should test our DR capabilities quarterly:

1. **pod failure**: kill pods randomly (chaos engineering style), verify service stays up
2. **RDS failover**: trigger manual failover in staging, measure RTO
3. **rollback**: deploy a known-bad version to staging, practice rollback procedure
4. **secrets rotation**: run the full rotation procedure in staging
5. **restore from backup**: restore a staging RDS from snapshot, verify data integrity

document results of each test and iterate on the runbook.
