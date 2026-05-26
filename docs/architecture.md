# architecture overview — underwriting-assist agent

## system context

the underwriting-assist agent sits between loan officers and the downstream systems (email, document storage, task queues). when a loan officer finishes reviewing an underwriting item, the item gets sent to this service which figures out what to do next — request a doc from the borrower, email an appraiser, schedule a renewal, or escalate to a human.

```
                                    ┌─────────────────┐
                                    │  anthropic API   │
                                    │  (or bedrock)    │
                                    └────────▲─────────┘
                                             │
┌──────────────┐    POST /v1/items/process   ┌┴────────────────┐
│ loan officer │ ──────────────────────────> │  underwriting    │
│ portal / API │ <────────────────────────── │  assist agent    │
└──────────────┘    AgentResult              └─┬──┬──┬─────────┘
                                               │  │  │
                              ┌─────────────┐  │  │  │  ┌────────────┐
                              │  postgres    │◄─┘  │  └─►│  SES       │
                              │  (loan data  │     │     │  (outbound │
                              │   + audit)   │     │     │   email)   │
                              └─────────────┘     │     └────────────┘
                                                  │
                                           ┌──────▼──────┐
                                           │  S3          │
                                           │  (loan docs) │
                                           └─────────────┘
```

## deployment architecture (AWS / EKS)

```
┌─────────────────────────── AWS Account ───────────────────────────┐
│                                                                    │
│  ┌──────────────────────── VPC ─────────────────────────────────┐  │
│  │                                                               │  │
│  │  ┌─── public subnets ───┐                                    │  │
│  │  │  ALB (ingress)       │                                    │  │
│  │  │  NAT gateway         │                                    │  │
│  │  └──────────┬───────────┘                                    │  │
│  │             │                                                 │  │
│  │  ┌─── private subnets ──────────────────────────────────┐    │  │
│  │  │                                                       │    │  │
│  │  │  ┌─── EKS cluster ────────────────────────────────┐  │    │  │
│  │  │  │                                                 │  │    │  │
│  │  │  │  namespace: underwriting-agent                  │  │    │  │
│  │  │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐       │  │    │  │
│  │  │  │  │  pod 1   │ │  pod 2   │ │  pod N   │       │  │    │  │
│  │  │  │  │  :8080   │ │  :8080   │ │  :8080   │       │  │    │  │
│  │  │  │  └──────────┘ └──────────┘ └──────────┘       │  │    │  │
│  │  │  │        ▲ HPA: 2-10 replicas (CPU/custom)      │  │    │  │
│  │  │  │                                                 │  │    │  │
│  │  │  └─────────────────────────────────────────────────┘  │    │  │
│  │  │                                                       │    │  │
│  │  │  ┌─── RDS (postgres) ──┐  ┌─── S3 ──────────────┐   │    │  │
│  │  │  │  multi-AZ           │  │  loan-docs bucket    │   │    │  │
│  │  │  │  encrypted (KMS)    │  │  encrypted (KMS)     │   │    │  │
│  │  │  │  automated backups  │  │  versioning enabled  │   │    │  │
│  │  │  └─────────────────────┘  └──────────────────────┘   │    │  │
│  │  └───────────────────────────────────────────────────────┘    │  │
│  │                                                               │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌── SES ──┐  ┌── KMS ──┐  ┌── CloudWatch ──┐  ┌── ECR ──┐      │
│  │ email   │  │ keys    │  │  logs/metrics   │  │ images  │      │
│  └─────────┘  └─────────┘  └────────────────┘  └─────────┘      │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

## key architectural decisions

### why EKS over ECS/fargate

- HPA with custom metrics (LLM latency, queue depth) is more flexible in k8s
- network policies for pod-level isolation
- pod disruption budgets for safe rollouts
- the team already runs other services on k8s (shared cluster cost)
- fargate cold starts (~5-15s) would push us past the latency SLO

### stateless design

the service itself holds no state. all persistence is in postgres (loan/item state, audit log) and S3 (documents). this means:
- horizontal scaling is trivial
- any pod can handle any request
- rolling deployments with zero downtime
- no sticky sessions, no affinity rules needed

### scaling strategy

the spec calls out bursty traffic patterns — items arrive in batches during business hours (9-11am, 2-4pm ET). our approach:

- **HPA baseline**: 2 replicas minimum (availability)
- **scale trigger**: CPU > 60% OR custom metric (request queue depth)
- **max replicas**: 10 (covers 10x burst over steady state)
- **scale-down delay**: 5 minutes (avoid flapping during burst gaps)
- **consider later**: KEDA for event-driven scaling if we move to queue-based processing

### networking

- ALB ingress in public subnets (TLS termination)
- service pods in private subnets (no direct internet access)
- NAT gateway for outbound (anthropic API, SES)
- security groups: pod → RDS (5432), pod → S3 (443), pod → anthropic (443), pod → SES (443)
- network policies: deny all ingress except from ALB, deny egress except to known endpoints

### secrets management

- kubernetes external-secrets-operator syncing from AWS Secrets Manager
- separate secrets per environment
- rotation: quarterly minimum per spec, automated via lambda + secrets manager rotation
- secrets in scope: ANTHROPIC_API_KEY, DATABASE_URL, SES credentials

### observability stack

```
app (OTEL SDK) ──► OTEL collector ──► prometheus (metrics)
                                   ──► CloudWatch Logs (structured logs)
                                   ──► X-Ray (traces, optional)

prometheus ──► grafana (dashboards)
           ──► alertmanager (pagerduty/slack)
```

### data flow for a single item

```
1. POST /v1/items/process with LoanContext
2. service validates request (pydantic)
3. service calls LLM (anthropic/bedrock) with context + tools
4. LLM returns tool_use response (e.g. request_document_from_borrower)
5. service parses response into AgentAction
6. service writes audit record (postgres) — includes full LLM I/O
7. service returns AgentResult to caller
8. (async) downstream systems pick up the action (email via SES, etc)
```

latency budget:
- request parsing: ~1ms
- LLM call: ~3-6s (this dominates)
- audit write: ~5-20ms
- response serialization: ~1ms
- total p95 target: < 8s

### environment strategy

| env | purpose | infra | data |
|-----|---------|-------|------|
| local | developer laptop | docker-compose, mock LLM | synthetic only |
| dev | integration testing | shared EKS, small RDS | synthetic only |
| staging | pre-prod validation | mirrors prod topology | synthetic, anonymized |
| prod | live traffic | full HA, multi-AZ | real PII (encrypted) |

strict rule: no production data in non-prod environments. ever.
