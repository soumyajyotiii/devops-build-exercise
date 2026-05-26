#!/usr/bin/env python3
"""
weekly ops digest generator.

collects operational data from the past week (deployments, alerts, scaling events,
resource usage) and asks Claude to produce a concise ops digest with trends and
recommendations.

usage:
    # generate digest for the past week
    python ops_digest.py

    # custom time range (days)
    python ops_digest.py --days 14

    # dry run (print to stdout, dont post to slack)
    python ops_digest.py --dry-run
"""

import argparse
import json
import os
import subprocess
import sys

import anthropic

from utils import (
    post_to_slack,
    format_slack_block,
    kubectl,
    get_hpa_status,
    log,
    NAMESPACE,
    DEPLOYMENT,
)

client = anthropic.Anthropic()

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

SYSTEM_PROMPT = """you are the SRE team's weekly ops digest writer for the underwriting-assist service at Saaf Finance.

you receive a week's worth of operational data and produce a concise, actionable digest.

SLO targets:
- p95 latency: < 8s (today), < 5s (12-month target)
- p99 latency: < 15s (today), < 10s (12-month target)
- availability: 99.5% (today), 99.9% (12-month target)
- cost per item: ≤ $1.50

output format (markdown):

# ops digest — week of [date range]

## tldr
[2-3 sentence summary — was it a good week or a bad week?]

## SLO compliance
[did we meet our SLOs? any close calls?]

## notable events
[deployments, incidents, scaling events, anything unusual]

## resource usage trends
[CPU, memory, pod count — trending up, down, or stable?]

## recommendations
[specific actions for the coming week]

---
keep it under 500 words. be direct. skip sections if theres nothing to report.
this gets posted to #sre-weekly and the eng manager reads it.
"""


def collect_deployment_history(days: int = 7) -> str:
    """get recent deployments from git log.

    uses git log as a proxy for deployment history since every merge to main
    triggers a deploy (via our CI pipeline). REPO_PATH env var lets this run
    from anywhere — in CI the checkout is at a different path.
    """
    result = subprocess.run(
        f"git log --oneline --since='{days} days ago' --format='%h %s (%cr)'",
        shell=True, capture_output=True, text=True, cwd=os.getenv("REPO_PATH", "."),
    )
    return result.stdout.strip() if result.returncode == 0 else "could not fetch git log"


def collect_k8s_events(days: int = 7) -> str:
    """get notable k8s events."""
    # recent warning events
    events = kubectl(
        f"get events -n {NAMESPACE} --sort-by=.lastTimestamp"
        f" --field-selector type!=Normal"
    )
    return events if events else "no warning events"


def collect_hpa_history() -> str:
    """get current HPA status (historical data would come from prometheus)."""
    return get_hpa_status()


def collect_pod_resource_usage() -> str:
    """get current resource usage."""
    return kubectl(f"top pods -n {NAMESPACE}")


def collect_rollout_history() -> str:
    """get deployment rollout history."""
    return kubectl(f"rollout history deployment/{DEPLOYMENT} -n {NAMESPACE}")


def collect_all_data(days: int = 7) -> dict:
    """collect all operational data for the digest.

    ideally wed also pull prometheus metrics (latency percentiles, error rate,
    items processed) but that requires access to the prometheus query API which
    varies by setup. for now we work with what kubectl gives us and note the
    gaps in the prompt so Claude mentions them in the digest.
    """
    log.info(f"collecting ops data for the past {days} days...")

    data = {
        "period": f"past {days} days",
        "deployment_history": collect_deployment_history(days),
        "k8s_events": collect_k8s_events(days),
        "hpa_status": collect_hpa_history(),
        "resource_usage": collect_pod_resource_usage(),
        "rollout_history": collect_rollout_history(),
    }

    return data


def generate_digest(data: dict) -> str:
    """send collected data to Claude for digest generation.

    this is a single-shot prompt — no tool-use, no loop. Claude gets all the
    data at once and produces the digest. the system prompt constrains the
    output format so its consistent week to week.
    """
    data_text = ""
    for key, value in data.items():
        data_text += f"\n### {key.replace('_', ' ')}\n{value}\n"

    user_message = f"""here is the operational data for the {data['period']}:
{data_text}

please generate the weekly ops digest.

note: some data sources (prometheus metrics, alert history) may not be available
in this context. work with what you have and note any gaps."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    result = ""
    for block in response.content:
        if hasattr(block, "text"):
            result += block.text
    return result


def main():
    parser = argparse.ArgumentParser(description="weekly ops digest generator")
    parser.add_argument("--days", type=int, default=7, help="number of days to cover")
    parser.add_argument("--dry-run", action="store_true", help="print to stdout only")

    args = parser.parse_args()

    data = collect_all_data(args.days)
    digest = generate_digest(data)

    print(digest)

    if not args.dry_run:
        post_to_slack(
            text=digest,
            blocks=format_slack_block("📊 weekly ops digest", digest),
        )
        log.info("digest posted to slack")


if __name__ == "__main__":
    main()
