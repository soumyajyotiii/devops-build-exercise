# AI-powered ops integration

how we use Claude API to reduce toil and improve reliability across the devops lifecycle.

## philosophy

we're not adding AI for the sake of it. each integration targets a specific pain point where the gap between "alert fires" and "engineer understands the situation" costs real time. the goal is:
- reduce MTTR (mean time to recovery) by automating initial diagnosis
- catch infra misconfigs before they ship via AI-powered PR review
- remove the tedious "stare at grafana for 10 minutes after deploy" ritual
- generate the weekly ops reports nobody has time to write manually

all of these use Claude API directly. they're standalone scripts in `scripts/ai-ops/` with no dependency on the application code.

## the four integrations

### 1. incident triage agent — `scripts/ai-ops/incident_triage.py`

**what it does**: when an alert fires, this agent automatically runs diagnostics (kubectl, health checks, external dependency status) and posts a synthesis to slack. the on-call engineer gets a diagnosis before they even open their laptop.

**how it works**:
- alertmanager sends webhook to the triage agent (runs as a sidecar or standalone pod)
- agent calls Claude with tool-use — Claude decides which diagnostics to run
- available tools: `get_pod_status`, `get_pod_logs`, `get_hpa_status`, `check_anthropic_status`, `check_endpoint_health`, `get_recent_events`
- Claude runs 2-4 tools typically, synthesizes the results, and posts to slack

**why tool-use matters here**: different alerts need different diagnostics. a latency alert needs LLM provider status check. a crash loop needs pod describe + previous logs. Claude picks the right tools based on the alert context instead of us hardcoding decision trees.

```
alert fires → alertmanager webhook → triage agent
                                         │
                                    Claude (tool-use)
                                    ├── get_pod_status()
                                    ├── get_pod_logs(grep="error")
                                    ├── check_anthropic_status()
                                    └── synthesize
                                         │
                                    slack: "p95 latency spike correlates with
                                    anthropic API degradation. no action needed."
```

**usage**:
```bash
# manual triage
python incident_triage.py --alert "HighP95Latency" --severity "warning" \
    --description "p95 latency is above 8s SLO"

# run as webhook server
python incident_triage.py --serve --port 9095
```

**estimated impact**: 5-10 minutes saved per alert. at ~20 alerts/month thats 2-3 hours of on-call time back.

### 2. PR risk reviewer — `scripts/ai-ops/pr_review.py`

**what it does**: reviews infra PRs (terraform, k8s, dockerfile, CI changes) against our security/compliance requirements. posts a risk assessment as a PR comment.

**how it works**:
- github actions workflow triggers on PRs that modify infra files
- script fetches the PR diff, sends it to Claude with our compliance context
- Claude identifies security risks, reliability issues, cost implications
- posts a structured review comment (risk level + findings + suggestions)

**what it checks against**:
- encryption at rest/transit requirements
- IAM least-privilege
- missing resource limits or health checks
- hardcoded secrets
- network policy gaps
- PII handling

**key design choice**: it does NOT auto-approve or block PRs. its purely additive signal for the human reviewer. we dont want AI to be a gate in the critical path.

```bash
# review a PR
python pr_review.py --repo soumyajyotiii/devops-build-exercise --pr 42

# review a local diff
git diff main...HEAD | python pr_review.py --stdin
```

### 3. post-deploy health checker — `scripts/ai-ops/deploy_health.py`

**what it does**: after prod deploy, collects health snapshots over 3 minutes, feeds them to Claude, and gets a go/no-go verdict. replaces "stare at grafana and hope nothing breaks."

**how it works**:
- integrated into the deploy-prod job in CI (runs after rollout completes)
- collects snapshots every 30s for 3 minutes: pod status, deployment state, error logs, k8s events
- sends all snapshots to Claude with baseline expectations (error rate < 0.5%, latency < 8s, zero restarts)
- Claude produces a verdict: HEALTHY / CONCERNING / UNHEALTHY
- posts result to slack deploy channel
- optionally triggers auto-rollback if unhealthy (off by default)

```bash
# after deploying
python deploy_health.py --commit abc1234 --duration 180 --interval 30

# with auto-rollback (use carefully)
python deploy_health.py --commit abc1234 --auto-rollback
```

**why not just use static thresholds?** because the combination of signals matters. 2 pod restarts + stable error rate is probably fine (pod got OOM-killed and recovered). 0 pod restarts + rising error rate is concerning. Claude weighs these together.

### 4. weekly ops digest — `scripts/ai-ops/ops_digest.py`

**what it does**: collects a week of operational data and generates a concise digest covering SLO compliance, notable events, resource trends, and recommendations.

**how it works**:
- runs on a schedule (monday morning cron or github actions scheduled workflow)
- collects: deployment history (git log), k8s events, HPA status, resource usage, rollout history
- Claude produces a structured digest: tldr, SLO compliance, events, trends, recommendations
- posts to #sre-weekly slack channel

```bash
# generate this week's digest
python ops_digest.py

# past 14 days
python ops_digest.py --days 14
```

**what we wish we could feed it**: prometheus metrics (p95/p99 latency, error rate, items processed). right now the script works with kubectl data. future iteration: query prometheus HTTP API directly.

## architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Claude API                                │
│                    (tool-use for triage,                     │
│                     plain prompts for review/digest)         │
└──────────┬────────────┬─────────────┬──────────────┬────────┘
           │            │             │              │
    ┌──────▼──────┐ ┌───▼──────┐ ┌───▼──────┐ ┌────▼─────┐
    │  incident   │ │ PR       │ │ deploy   │ │ ops      │
    │  triage     │ │ review   │ │ health   │ │ digest   │
    │  agent      │ │          │ │ checker  │ │          │
    └──────┬──────┘ └───┬──────┘ └───┬──────┘ └────┬─────┘
           │            │            │              │
    alertmanager   github PR     ci/cd job      cron schedule
    webhook        webhook       post-deploy    (weekly)
           │            │            │              │
           └────────────┴────────────┴──────────────┘
                              │
                         slack / pagerduty
```

## environment variables

| variable | used by | description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | all scripts | Claude API key |
| `SLACK_WEBHOOK_URL` | all scripts | incoming webhook for posting to slack |
| `GITHUB_TOKEN` | pr_review | for posting PR comments |
| `CLAUDE_MODEL` | all scripts | model to use (default: claude-sonnet-4-20250514) |
| `K8S_NAMESPACE` | triage, deploy_health | kubernetes namespace (default: underwriting-agent) |
| `SERVICE_URL` | deploy_health | URL to hit health endpoints (optional) |

## cost

all four integrations use claude-sonnet which is cheap:
- incident triage: ~$0.01 per alert (2-3 tool calls + synthesis)
- PR review: ~$0.005 per review (single prompt, diff usually < 5K tokens)
- deploy health: ~$0.005 per deploy (snapshot data is small)
- ops digest: ~$0.01 per week

total estimated: ~$5-10/month at current alert and deploy volumes. less than the cost of 1 minute of engineer time per incident.

## what we explicitly decided NOT to build

- **auto-remediation without human approval**: too risky for a financial services app. the AI diagnoses, the human decides.
- **LLM-powered log search**: structured logging + grep is already fast enough. AI adds latency without much benefit here.
- **AI-generated terraform**: terraform needs to be reviewable and diffable. AI writing IaC is more footgun than help right now.
- **chatbot interface**: a slack chatbot sounds cool but adds complexity (state management, auth, rate limiting). scripts triggered by events are simpler and more reliable.
