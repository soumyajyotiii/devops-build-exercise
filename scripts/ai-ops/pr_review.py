#!/usr/bin/env python3
"""
AI-powered PR risk reviewer for infrastructure changes.

reviews diffs in terraform, k8s manifests, dockerfiles, CI/CD workflows
and posts a risk assessment as a PR comment.

usage:
    # in github actions (see .github/workflows/pr-review.yml)
    python pr_review.py --repo owner/repo --pr 42

    # locally — pass a diff directly
    git diff main...HEAD | python pr_review.py --stdin

    # locally — review a specific PR
    python pr_review.py --repo soumyajyotiii/devops-build-exercise --pr 1
"""

import argparse
import json
import os
import subprocess
import sys

import anthropic
import httpx

from utils import log

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")  # github actions provides this automatically

# -- compliance context --
# this gets injected into the system prompt so Claude knows what to check for.
# sourced from docs/security-compliance.md — if the requirements change there,
# update them here too.
SECURITY_CONTEXT = """
key compliance requirements for this project (financial services):
- encryption at rest (KMS-managed) for all data stores
- encryption in transit (TLS 1.2+) for all service-to-service calls
- audit trail of every LLM call: retained 7 years
- least-privilege IAM for service identity
- secrets (LLM API keys, DB creds) rotated quarterly minimum
- no production data in non-prod environments
- container images must run as non-root
- network policies must restrict ingress/egress to known endpoints
- PII data (borrower name, email, financial data) must be encrypted
- no hardcoded secrets or credentials in code
"""

SYSTEM_PROMPT = f"""you are a senior DevOps/SRE engineer reviewing infrastructure changes for a financial services application (mortgage underwriting).

your job is to review the diff and identify:
1. security risks (IAM too permissive, missing encryption, exposed secrets, etc)
2. reliability risks (missing health checks, no resource limits, no PDB, etc)
3. cost implications (oversized instances, missing lifecycle policies, etc)
4. compliance violations (see compliance context below)
5. best practice violations (mutable image tags, no rollback strategy, etc)

{SECURITY_CONTEXT}

output format (use markdown):
## risk level: [LOW / MEDIUM / HIGH / CRITICAL]

### findings
- [each finding as a bullet with severity and explanation]

### suggestions
- [specific actionable recommendations]

### summary
[1-2 sentence overall assessment]

be direct and specific. reference exact lines/resources when possible.
if the change looks clean, say so — dont manufacture issues.
"""


def get_pr_diff(repo: str, pr_number: int) -> str:
    """fetch PR diff from github API.

    tries gh CLI first (works locally if youre authenticated),
    falls back to the REST API with a token (works in CI).
    """
    if not GITHUB_TOKEN:
        result = subprocess.run(
            f"gh pr diff {pr_number} --repo {repo}",
            shell=True, capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout
        raise RuntimeError(f"failed to get PR diff: {result.stderr}")

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.diff",
    }
    resp = httpx.get(
        f"https://api.github.com/repos/{repo}/pulls/{pr_number}",
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def get_pr_files(repo: str, pr_number: int) -> list[str]:
    """get list of changed files in a PR."""
    result = subprocess.run(
        f"gh pr view {pr_number} --repo {repo} --json files --jq '.files[].path'",
        shell=True, capture_output=True, text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip().split("\n")
    return []


def is_infra_change(files: list[str]) -> bool:
    """check if any changed files are infrastructure-related.

    we only run AI review on infra files — reviewing application code changes
    is out of scope for this tool (and would be noisy on every PR).
    """
    infra_patterns = [
        "terraform/", "k8s/", "monitoring/", "Dockerfile",
        ".github/workflows/", ".dockerignore", "docker-compose",
    ]
    return any(
        any(f.startswith(p) or f == p for p in infra_patterns)
        for f in files
    )


def review_diff(diff: str, changed_files: list[str] | None = None) -> str:
    """send the diff to Claude for review.

    no tool-use here — unlike incident triage, PR review is a single-shot analysis.
    Claude gets the diff and compliance context, produces a risk assessment, done.

    diff is truncated to 50k chars because very large PRs would exceed token limits.
    in practice infra PRs are rarely that big.
    """
    file_context = ""
    if changed_files:
        file_context = f"\nchanged files:\n" + "\n".join(f"  - {f}" for f in changed_files)

    user_message = f"""please review this infrastructure diff for security, reliability, cost, and compliance issues.
{file_context}

```diff
{diff[:50000]}
```"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    review = ""
    for block in response.content:
        if hasattr(block, "text"):
            review += block.text
    return review


def post_pr_comment(repo: str, pr_number: int, comment: str):
    """post a review comment on the PR.

    posts as a regular issue comment (not a review). this means it doesnt
    approve or request changes — its purely informational. the human reviewer
    makes the actual decision.
    """
    header = "## 🤖 AI infra review\n\n"
    full_comment = header + comment

    # try gh CLI first (simpler, handles auth automatically)
    result = subprocess.run(
        f'gh pr comment {pr_number} --repo {repo} --body "$(cat)"',
        shell=True, input=full_comment, capture_output=True, text=True,
    )
    if result.returncode == 0:
        log.info(f"posted review comment on PR #{pr_number}")
        return

    # fallback to API
    if GITHUB_TOKEN:
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        resp = httpx.post(
            f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
            headers=headers,
            json={"body": full_comment},
            timeout=30,
        )
        if resp.status_code == 201:
            log.info(f"posted review comment on PR #{pr_number}")
        else:
            log.error(f"failed to post comment: {resp.status_code} {resp.text}")
    else:
        log.warning("no GITHUB_TOKEN, printing review to stdout")
        print(full_comment)


def main():
    parser = argparse.ArgumentParser(description="AI-powered PR risk reviewer")
    parser.add_argument("--repo", help="github repo (owner/repo)")
    parser.add_argument("--pr", type=int, help="PR number")
    parser.add_argument("--stdin", action="store_true", help="read diff from stdin")
    parser.add_argument("--dry-run", action="store_true", help="print review without posting")

    args = parser.parse_args()

    if args.stdin:
        diff = sys.stdin.read()
        if not diff.strip():
            print("no diff provided on stdin")
            sys.exit(1)

        review = review_diff(diff)
        print(review)

    elif args.repo and args.pr:
        # check if this PR has infra changes
        changed_files = get_pr_files(args.repo, args.pr)
        if changed_files and not is_infra_change(changed_files):
            log.info("no infrastructure changes detected, skipping review")
            print("no infrastructure changes — skipping AI review")
            return

        log.info(f"reviewing PR #{args.pr} in {args.repo}")
        diff = get_pr_diff(args.repo, args.pr)
        review = review_diff(diff, changed_files)

        if args.dry_run:
            print(review)
        else:
            post_pr_comment(args.repo, args.pr, review)
            print(review)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
