# capacity planning and cost analysis

## current state vs 12-month target

| metric | today | 12 months |
|--------|-------|-----------|
| loans/month | 500 | 5,000 |
| items/loan | 3-5 | 3-5 |
| total items/month | ~2,000 | ~20,000 |
| daily peak (items/hr) | ~25 | ~250 |
| p95 latency target | < 8s | < 5s |
| availability target | 99.5% | 99.9% |
| cost target per item | - | ≤ $1.50 |

## cost breakdown per item

### LLM cost (dominant factor)

using claude sonnet as the primary model:

| component | estimate |
|-----------|----------|
| input tokens (~2000 tokens per loan context) | ~$0.006 |
| output tokens (~500 tokens per response) | ~$0.003 |
| total LLM cost per item | ~$0.009 |

this is well under the $1.50 target. even with claude opus at 15x the cost, we'd still be around $0.14/item.

note: these are approximate based on current anthropic pricing. if using bedrock, add the bedrock markup.

### compute cost

| resource | monthly cost (today) | monthly cost (12mo) |
|----------|---------------------|---------------------|
| EKS cluster | $73 | $73 |
| node group (2x t3.large) | ~$120 | ~$360 (6 nodes) |
| RDS (db.r6g.large, multi-AZ) | ~$380 | ~$380 |
| NAT gateway | ~$95 | ~$95 |
| ALB | ~$25 | ~$25 |
| S3 (loan docs) | ~$5 | ~$30 |
| ECR | ~$2 | ~$5 |
| secrets manager | ~$3 | ~$3 |
| cloudwatch logs | ~$15 | ~$50 |
| **total infra** | **~$718** | **~$1,021** |

### cost per item calculation

**today** (2,000 items/month):
- infra: $718 / 2,000 = $0.36/item
- LLM: ~$0.009/item
- total: ~$0.37/item

**12-month** (20,000 items/month):
- infra: $1,021 / 20,000 = $0.05/item
- LLM: ~$0.009/item
- total: ~$0.06/item

we're well under the $1.50 target at both scales. the key insight is that infra cost doesnt scale linearly — most of the base cost (EKS, RDS, NAT) is fixed regardless of traffic.

compared to the current labor cost of ~$14/item (20 min loan officer time), even the worst case is a 90%+ cost reduction.

## compute sizing

### pods

each item is a single API call that mostly waits on the LLM (~3-6 seconds). the pod itself is doing very little compute — its just marshalling the request and parsing the response.

**memory**: the app is a straightforward FastAPI service. 256-512Mi is plenty. we set limits at 1Gi to give headroom for spikes.

**CPU**: minimal CPU usage per request (LLM call is network-bound, not CPU-bound). 250m request with 1 CPU limit is fine.

### pod count estimation

at peak (250 items/hour at 12 months):
- items/second: ~0.07
- avg processing time: ~5s
- concurrent items at any moment: ~0.35
- so theoretically 1 pod handles the load easily

but we run minimum 3 pods in prod for availability (multi-AZ spread). HPA scales up to 15 if things get bursty.

the burst pattern matters more than steady state — if 50 items land in a 5-minute window:
- items/second during burst: ~0.17
- concurrent: ~0.85
- still fine with 3 pods

we have plenty of headroom. the HPA is more about fault tolerance than actual load.

### RDS sizing

- current data volume: small (500 loans * 5 items * ~2KB per record = ~5MB/month)
- audit logs are larger: ~10KB per LLM call (full I/O) * 2,000 items = ~20MB/month
- 12-month projection: ~200MB/month of audit data, ~2.4GB/year
- 7-year retention: ~17GB of audit data total
- 20GB initial allocation with autoscaling to 500GB is more than sufficient

db.r6g.large gives us:
- 2 vCPU, 16GB memory
- way overkill for this workload but reasonable for prod (performance insights, connection pooling overhead)
- could drop to db.t3.large to save ~$100/month if cost is a concern

### S3 sizing

loan documents (bank statements, appraisals, insurance policies):
- average: ~2-5MB per document, ~3-5 docs per loan
- monthly: 500 loans * 4 docs * 3.5MB = ~7GB/month
- lifecycle policy moves to IA after 90 days and glacier after 1 year
- cost is negligible

## scaling plan

### phase 1 (now → 3 months): establish baseline
- deploy with current sizing
- monitor actual resource usage and LLM latency
- tune HPA thresholds based on real data
- validate cost per item against estimates

### phase 2 (3-6 months): optimize
- right-size pods based on actual p99 resource usage
- consider spot instances for non-peak hours (if using self-managed nodes)
- evaluate if db.t3.large is sufficient for RDS
- implement request batching if items consistently arrive together

### phase 3 (6-12 months): scale for 5000 loans/month
- increase node group max size
- evaluate need for dedicated node pool for the agent
- consider moving audit log writes to async (SQS → lambda → postgres) to reduce latency
- evaluate bedrock vs direct anthropic API for cost/latency trade-offs at scale

## key risks

1. **LLM provider outage**: anthropic goes down = we can't process items. mitigation: bedrock as fallback provider
2. **LLM cost increase**: if anthropic raises prices significantly, our cost model changes. mitigation: the current margin is huge ($0.009 vs $1.50 budget)
3. **burst beyond expectations**: if the burst pattern is more extreme than spec. mitigation: HPA handles this, and we can increase max replicas quickly
4. **audit log growth**: 7 years of audit data could get large. mitigation: partition by month, archive to S3 glacier after 1 year
