# prioritisation — underwriting-assist agent deployment

## why this order

the way i see it...theres no point setting up monitoring for something that cant even be deployed yet. and theres no point deploying if we dont have a pipeline to build and ship it. so the order here is bottom-up — foundation first, polish later.

also worth noting — the agent code itself is out of scope. the ML team ships that. we deploy and operate it. so everything here is infra, pipeline, and operational tooling.

## priority breakdown

### P0 — must have before anything ships

**ci/cd pipeline (github actions)**
- if we cant build and test automatically, nothing else matters
- lint + test + docker build + push to ECR
- separate workflows for PR validation vs deploy-to-prod
- why first: every other piece of work benefits from having CI in place. we want to catch broken dockerfiles, failing tests, bad configs before they hit any environment

**kubernetes manifests**
- this is how the thing actually runs in production
- deployment, service, HPA, configmaps, secrets, network policies
- kustomize overlays for dev/staging/prod because we need env separation
- why P0: without this we literally cannot deploy. everything else is optimization on top

### P1 — needed before prod traffic

**terraform (infrastructure as code)**
- EKS cluster, RDS postgres, S3 buckets, VPC/networking, IAM roles, SES config
- modular structure so we can spin up identical environments
- this is the platform the k8s manifests deploy onto
- why P1 and not P0: you can technically deploy to an existing cluster manually first...but you shouldnt be doing that for long

**security and compliance documentation**
- financial services means we cant wing it on security
- encryption at rest (KMS), TLS 1.2+, IAM least-privilege, secrets rotation
- audit trail for every LLM call — 7 year retention requirement
- PII handling (borrower data, financial records)
- why P1: compliance isnt optional in finserv. but its documentation of how we configure things, not a blocker for initial deployment

### P2 — needed before on-call

**monitoring and alerting**
- prometheus metrics, grafana dashboards, alerting rules
- key signals: request latency (p95/p99), error rate, LLM call duration, pod health, HPA scaling events
- PagerDuty/Slack integration for critical alerts
- why P2: you can run without monitoring for a bit but you really shouldnt. this is "before we put real traffic on it"

**runbook / operational docs**
- incident playbooks, scaling procedures, common failure modes
- how to roll back, how to drain traffic, how to rotate secrets
- why P2: the person who gets paged at 2am needs this

### P3 — needed for maturity

**cost analysis and capacity planning**
- working through the numbers: compute + LLM cost per item
- scaling projections from 500 loans/month to 5000
- instance sizing, HPA thresholds, budget forecasting
- why P3: important for planning but not blocking deployment

**disaster recovery documentation**
- RPO 1 hour, RTO 30 minutes — how we actually achieve that
- RDS automated backups, multi-AZ, failover procedures
- S3 versioning and cross-region replication
- why P3: DR strategy needs to be documented but the actual mechanisms (multi-AZ RDS, S3 versioning) are configured in terraform

## what we're NOT doing

- modifying the agent code (thats the ML teams job)
- model fine-tuning infrastructure
- building a custom LLM serving layer
- setting up a data pipeline for training data

these are explicitly out of scope per the spec.

## trade-offs and decisions

| decision | reasoning |
|----------|-----------|
| kustomize over helm | simpler for this service size, less abstraction to debug. if we end up with 10+ services we might revisit |
| github actions over jenkins/argocd | already on github, keeps everything in one place. argocd could be a good addition later for gitops |
| terraform over CDK/pulumi | team standard, broadest hiring pool, most community modules |
| EKS over ECS | the spec mentions horizontal scaling and the app is stateless — k8s gives us HPA + pod disruption budgets + network policies out of the box |
| single region to start | 99.5% availability target is achievable single-region with multi-AZ. we'll revisit for 99.9% |
