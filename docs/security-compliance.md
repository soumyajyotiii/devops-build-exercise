# security and compliance — underwriting-assist agent

this document covers how we meet the compliance requirements for a financial services application handling borrower PII, financial data, and legal-sensitive content.

## data classification

| data type | classification | where stored | examples |
|-----------|---------------|--------------|----------|
| borrower PII | confidential | postgres, S3 | name, email, phone, entity |
| financial data | confidential | postgres | income, deposits, balances |
| underwriter notes | internal | postgres | legal-sensitive review notes |
| loan documents | confidential | S3 | bank statements, appraisals, insurance policies |
| LLM call logs | confidential | postgres (audit table) | full input/output of every LLM call |
| application metrics | internal | prometheus/cloudwatch | latency, error rates, scaling events |

## encryption

### at rest
- **RDS**: encrypted with AWS KMS (customer-managed key), configured in terraform
- **S3**: SSE-KMS with bucket key enabled, enforced via bucket policy
- **EKS secrets**: envelope encryption via KMS (configured in eks module)
- **ECR images**: KMS encrypted

all KMS keys have automatic yearly rotation enabled.

### in transit
- **external traffic → ALB**: TLS 1.3 (ELBSecurityPolicy-TLS13-1-2-2021-06)
- **ALB → pods**: TLS 1.2+ (within VPC, could argue plain HTTP is acceptable here but we enforce TLS anyway)
- **pods → RDS**: SSL enforced via connection string parameter (`sslmode=require`)
- **pods → S3**: HTTPS enforced via bucket policy (denies `aws:SecureTransport=false`)
- **pods → Anthropic API**: HTTPS (enforced by Anthropic)
- **pods → SES**: TLS required via SES configuration set

## IAM and access control

### principle of least privilege

the pod service account (via IRSA) has exactly these permissions and nothing more:

| permission | resource | why |
|-----------|----------|-----|
| s3:GetObject, PutObject, HeadBucket, ListBucket | loan-docs bucket only | read/write loan documents |
| ses:SendEmail | restricted to `loans@saaffinance.com` from address | outbound borrower/appraiser emails |
| secretsmanager:GetSecretValue | `underwriting-agent/{env}/*` secrets only | read API keys and DB creds |
| kms:Decrypt, GenerateDataKey | environment-specific KMS key only | decrypt secrets, S3 objects, RDS |

the pod role explicitly does NOT have:
- any `iam:*` permissions
- any `ec2:*` permissions
- any `eks:*` permissions
- any `s3:DeleteObject` or `s3:DeleteBucket`
- any cloudwatch/logs write (thats handled by the node role + fluentbit)

### network isolation

- pods run in private subnets, no direct internet access
- NAT gateway for outbound only
- network policies restrict pod ingress to ALB and egress to known endpoints
- RDS security group only allows connections from EKS cluster security group on port 5432

### RBAC

k8s RBAC is configured so that:
- the service account can only access resources in the `underwriting-agent` namespace
- developers have read-only access to prod
- deployments are done through CI/CD only (no kubectl apply from laptops in prod)

## secrets management

### what we store as secrets
- `ANTHROPIC_API_KEY` — LLM provider credentials
- `DATABASE_URL` — full postgres connection string including password
- SES SMTP credentials (if using SMTP instead of API)

### how we manage them
- stored in **AWS Secrets Manager**
- synced to k8s via **external-secrets-operator**
- kubernetes secrets are never committed to git (the `secret.yaml` in base has placeholder values only)
- rotation: **quarterly minimum** per compliance requirement
- rotation is automated via AWS Secrets Manager rotation lambda

### rotation procedure
1. secrets manager triggers rotation lambda
2. lambda generates new credential
3. lambda updates the secret in secrets manager
4. external-secrets-operator picks up the change (poll interval: 1 hour)
5. pods get restarted by the operator to pick up new values
6. old credentials remain valid for 24 hours (grace period for in-flight requests)

## audit trail

### what gets logged
every single LLM call is recorded with:
- full input (loan context sent to the model)
- full output (model response including tool use)
- model identifier (e.g. claude-sonnet-4-6)
- timestamp (UTC)
- loan ID
- item ID
- IAM principal (pod service account)
- latency
- audit ID (opaque reference for lookup)

### retention
- **7 years** in production (compliance requirement for financial services)
- implemented via: postgres table with partitioning by month, older partitions moved to cold storage
- alternatively: stream audit records to S3 (glacier after 1 year) for cheaper long-term storage
- this is a future optimization — for now, postgres with the 7-year retention policy is the baseline

### immutability
audit records are insert-only. no UPDATE or DELETE queries are allowed on the audit table. this is enforced at the database level via:
- a restrictive GRANT (only INSERT and SELECT)
- a trigger that prevents UPDATE/DELETE operations

## LLM provider compliance

- the LLM provider (Anthropic) must be configured with a **no-training data agreement**
- borrower PII is sent to the LLM as part of the loan context...this is necessary for the agent to function
- the audit trail ensures we can trace exactly what data was sent to the LLM and when
- if using AWS Bedrock as fallback, the same no-training guarantee applies (Bedrock doesnt use customer data for training by default)

## environment separation

| rule | enforcement |
|------|------------|
| no production data in non-prod environments | separate AWS accounts, separate VPCs, separate databases, separate S3 buckets |
| non-prod uses synthetic/anonymized data only | documented in runbook, enforced by access controls |
| prod access is restricted | IAM policies, VPN required, read-only for devs |
| separate secrets per environment | different Secrets Manager paths per env |
| separate KMS keys per environment | each env has its own CMK |

## vulnerability management

- **container images** scanned by Trivy on every CI build (CRITICAL and HIGH severity block the build)
- **ECR** has scan-on-push enabled for continuous scanning
- **dependencies**: dependabot enabled for automated PRs on vulnerable packages
- **infrastructure**: terraform plan reviewed in PR before apply

## incident response

in case of a security incident:
1. revoke compromised credentials immediately (secrets manager emergency rotation)
2. network isolation — apply deny-all network policy if needed
3. preserve audit logs (they're immutable anyway)
4. notify compliance team
5. postmortem within 48 hours

see runbook for detailed playbooks.
