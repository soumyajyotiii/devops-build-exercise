# known caveats and things to address

stuff we ran into during local testing and setup that needs attention. some of these are quick fixes, some need coordination with the ML team.

## 1. CI lint step will fail on existing code

`ruff check` catches an unused import in the agent code:

```
src/agent/config.py:14 — `pydantic.Field` imported but unused (F401)
```

the agent code is owned by the ML team and out of scope for us to modify. two options:
- **ask the ML team to fix it** — its a one-line removal, should be easy
- **add a ruff ignore rule** — we could add `per-file-ignores = ["src/agent/config.py:F401"]` to pyproject.toml but thats papering over it

for now CI will fail on the lint step until this is resolved. tests and docker build pass fine.

## 2. no .dockerignore existed

the repo had no `.dockerignore`, which means everything we added (terraform/, k8s/, docs/, monitoring/, .git/) was being sent as docker build context. not a build-breaking issue but:
- slows down builds unnecessarily
- leaks infra code into the image build context

**fixed**: added `.dockerignore` in the same commit as this doc.

## 3. CORS is wide open in the application code

`main.py` line 35 sets `allow_origins=["*"]`. the inline comment says "tighten in production" but theres nothing enforcing that at the infra level.

for a service handling borrower PII (name, email, phone, financial data), this is a legitimate concern. options:
- **app-level fix** (ML team): set `allow_origins` from an env var, configure it per environment
- **infra-level mitigation**: the ALB + network policy already restricts who can reach the service (its internal-only via ALB with `alb.ingress.kubernetes.io/scheme: internal`). so even with CORS wide open, the attack surface is limited to internal network
- **WAF**: could add AWS WAF on the ALB to restrict origins at the load balancer level

this isnt critical because the service sits behind an internal ALB (not internet-facing), but its still not great practice. flagging it for the ML team to address.

## 4. Dockerfile HEALTHCHECK vs k8s readiness

the Dockerfile defines a HEALTHCHECK that hits `/healthz`:
```dockerfile
HEALTHCHECK CMD curl -f http://localhost:${PORT}/healthz || exit 1
```

`/healthz` always returns 200 (its a liveness check). `/readyz` is the one that actually checks postgres and S3 connectivity.

in kubernetes this is fine — we configured separate liveness and readiness probes pointing to the right endpoints. but if someone runs the container standalone (docker-compose, ECS without custom health checks), the docker HEALTHCHECK will show "healthy" even when the backing services are completely down.

not a blocker for our deployment path (k8s handles it correctly) but worth being aware of.

## 5. no metrics endpoint yet

our monitoring setup (prometheus rules, servicemonitor, grafana dashboard) assumes the app exposes a `/metrics` endpoint with prometheus-format metrics. the app has OTEL instrumentation stubs but doesnt currently expose a `/metrics` endpoint.

the ML team would need to either:
- add `prometheus-fastapi-instrumentator` or similar to expose standard HTTP metrics
- or we set up an OTEL collector sidecar that converts OTEL → prometheus format

the grafana dashboard and prometheus rules wont show data until this is resolved. the alerts wont fire either (which means theyre also not going to false-alarm, so...silver lining).

## 6. audit table schema not defined

the app's `store.py` calls `write_audit_record()` which generates an audit ID and logs it, but theres no actual database migration or schema definition for the audit table. the app code is a stub that just returns an ID.

when the ML team ships the real implementation, we'll need:
- a database migration tool (alembic most likely)
- the audit table schema with appropriate indexes
- the INSERT-only permissions and trigger we documented in security-compliance.md
- a CI step to run migrations before deployment

## summary

| issue | severity | who fixes | status |
|-------|----------|-----------|--------|
| ruff lint failure (unused import) | medium | ML team | open |
| missing .dockerignore | low | us | fixed |
| CORS allow_origins=* | medium | ML team (app) + us (WAF) | open |
| Dockerfile HEALTHCHECK vs readyz | low | awareness only | documented |
| no /metrics endpoint | medium | ML team + us (sidecar) | open |
| no audit table migration | medium | ML team | open |
