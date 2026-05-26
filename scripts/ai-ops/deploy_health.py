#!/usr/bin/env python3
"""
post-deploy health checker.

runs after a production deployment, polls key metrics for a few minutes,
then asks Claude whether the deploy looks healthy or needs rollback.

usage:
    # after deploying to prod
    python deploy_health.py --commit abc1234

    # with custom check duration
    python deploy_health.py --commit abc1234 --duration 300 --interval 30

    # dry run (dont post to slack or trigger rollback)
    python deploy_health.py --commit abc1234 --dry-run
"""

import argparse
import json
import os
import sys
import time

import anthropic

from utils import (
    post_to_slack,
    format_slack_block,
    kubectl,
    get_pod_status,
    get_pod_logs,
    get_deployment_status,
    check_endpoint_health,
    log,
    NAMESPACE,
    DEPLOYMENT,
)

client = anthropic.Anthropic()

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

SYSTEM_PROMPT = """you are a deployment health checker for the underwriting-assist service.

after a production deployment, you receive a series of health snapshots taken over several minutes.
your job is to analyze these snapshots and decide:
1. is the deployment healthy? (yes / concerning / unhealthy)
2. what specifically looks good or bad?
3. should we rollback? (yes / no / monitor)

key baselines:
- error rate should be < 0.5%
- p95 latency should be < 8 seconds
- zero pod restarts after a clean deploy
- all pods should be Ready
- readiness probe should pass (postgres + S3 connected)

be concise. the deploy engineer is waiting for your verdict.

output format:
## verdict: [HEALTHY / CONCERNING / UNHEALTHY]

### observations
- [what you see in the data]

### recommendation
[what to do next — continue, monitor closely, or rollback]
"""


def collect_health_snapshot() -> dict:
    """collect a single health snapshot from the cluster."""
    snapshot = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "pods": get_pod_status(),
        "deployment": get_deployment_status(),
        "recent_logs": get_pod_logs(tail=20, grep="error"),
        "events": kubectl(
            f"get events -n {NAMESPACE} --sort-by=.lastTimestamp"
            f" --field-selector type!=Normal | tail -10"
        ),
    }

    # try to hit the health endpoints via port-forward or service URL
    svc_url = os.getenv("SERVICE_URL", "")
    if svc_url:
        snapshot["health_endpoints"] = check_endpoint_health(svc_url)

    return snapshot


def collect_snapshots(duration_sec: int = 180, interval_sec: int = 30) -> list[dict]:
    """collect health snapshots over a time period."""
    snapshots = []
    iterations = max(1, duration_sec // interval_sec)

    log.info(f"collecting {iterations} health snapshots over {duration_sec}s...")

    for i in range(iterations):
        log.info(f"  snapshot {i + 1}/{iterations}")
        snapshot = collect_health_snapshot()
        snapshots.append(snapshot)

        if i < iterations - 1:
            time.sleep(interval_sec)

    return snapshots


def analyze_health(commit: str, snapshots: list[dict]) -> str:
    """send snapshots to Claude for health analysis."""

    snapshot_text = ""
    for i, snap in enumerate(snapshots):
        snapshot_text += f"\n--- snapshot {i + 1} ({snap['timestamp']}) ---\n"
        snapshot_text += f"pods:\n{snap['pods']}\n\n"
        snapshot_text += f"deployment:\n{snap['deployment']}\n\n"
        snapshot_text += f"error logs:\n{snap['recent_logs']}\n\n"
        snapshot_text += f"warning events:\n{snap['events']}\n\n"
        if "health_endpoints" in snap:
            snapshot_text += f"health endpoints:\n{json.dumps(snap['health_endpoints'], indent=2)}\n\n"

    user_message = f"""deployment commit: {commit}
namespace: {NAMESPACE}
deployment: {DEPLOYMENT}

health snapshots collected post-deploy:
{snapshot_text}

please analyze these snapshots and provide your deployment health verdict."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    result = ""
    for block in response.content:
        if hasattr(block, "text"):
            result += block.text
    return result


def trigger_rollback():
    """execute rollback if needed."""
    log.warning("triggering rollback...")
    result = kubectl(f"rollout undo deployment/{DEPLOYMENT} -n {NAMESPACE}")
    log.info(f"rollback result: {result}")

    status = kubectl(f"rollout status deployment/{DEPLOYMENT} -n {NAMESPACE} --timeout=120s")
    log.info(f"rollout status after rollback: {status}")

    return f"rollback executed.\n{result}\n{status}"


def main():
    parser = argparse.ArgumentParser(description="post-deploy health checker")
    parser.add_argument("--commit", required=True, help="deployed commit SHA")
    parser.add_argument("--duration", type=int, default=180, help="monitoring duration in seconds")
    parser.add_argument("--interval", type=int, default=30, help="snapshot interval in seconds")
    parser.add_argument("--dry-run", action="store_true", help="dont post to slack or rollback")
    parser.add_argument("--auto-rollback", action="store_true", help="auto-rollback if unhealthy")

    args = parser.parse_args()

    log.info(f"monitoring deployment {args.commit} for {args.duration}s...")

    # collect health snapshots
    snapshots = collect_snapshots(args.duration, args.interval)

    # analyze with Claude
    log.info("analyzing health data with Claude...")
    analysis = analyze_health(args.commit, snapshots)

    print(analysis)

    # determine verdict from the analysis text
    is_unhealthy = "UNHEALTHY" in analysis.upper().split("VERDICT")[1] if "VERDICT" in analysis.upper() else False

    if not args.dry_run:
        # post to slack
        emoji = "✅" if not is_unhealthy else "🚨"
        title = f"{emoji} deploy health check: {args.commit[:7]}"
        post_to_slack(
            text=f"{title}\n\n{analysis}",
            blocks=format_slack_block(title, analysis),
        )

        # auto-rollback if unhealthy and flag is set
        if is_unhealthy and args.auto_rollback:
            rollback_result = trigger_rollback()
            post_to_slack(
                text=f"🔙 auto-rollback triggered for {args.commit[:7]}\n\n{rollback_result}",
            )
        elif is_unhealthy:
            post_to_slack(
                text=f"⚠️ deploy {args.commit[:7]} looks unhealthy but auto-rollback is OFF. manual action needed.",
            )

    if is_unhealthy:
        sys.exit(1)


if __name__ == "__main__":
    main()
