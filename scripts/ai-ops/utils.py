"""
shared utilities for AI ops scripts.
slack posting, k8s helpers, common config.

every ai-ops script imports from here instead of reimplementing
kubectl wrappers and slack posting. keeps the individual scripts
focused on their specific logic.
"""

import json
import os
import subprocess
import logging

import httpx

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("ai-ops")

# -- config from env vars --
# these are set in k8s configmap or CI env, with sane defaults for local dev
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
NAMESPACE = os.getenv("K8S_NAMESPACE", "underwriting-agent")
DEPLOYMENT = os.getenv("K8S_DEPLOYMENT", "underwriting-agent")


def post_to_slack(text: str, channel_override: str | None = None, blocks: list | None = None):
    """send a message to slack via incoming webhook.

    gracefully degrades to stdout if SLACK_WEBHOOK_URL isnt configured —
    this way the scripts work fine during local dev/testing without needing
    a real slack workspace.
    """
    if not SLACK_WEBHOOK_URL:
        log.warning("SLACK_WEBHOOK_URL not set, printing to stdout instead")
        print(text)
        return

    # text is the fallback for clients that dont render blocks (mobile notifs etc)
    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks

    resp = httpx.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
    if resp.status_code != 200:
        log.error(f"slack post failed: {resp.status_code} {resp.text}")


def kubectl(args: str, timeout_sec: int = 30) -> str:
    """run a kubectl command and return stdout.

    returns error strings instead of raising — this is intentional because
    these outputs get fed to Claude as tool results, and Claude can reason
    about error messages just fine. raising would break the agentic loop.
    """
    cmd = f"kubectl {args}"
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout_sec
        )
        if result.returncode != 0:
            return f"ERROR: {result.stderr.strip()}"
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "ERROR: kubectl command timed out"
    except Exception as e:
        return f"ERROR: {e}"


def get_pod_status(namespace: str = NAMESPACE) -> str:
    """get pod status in the namespace."""
    return kubectl(f"get pods -n {namespace} -o wide")


def get_pod_logs(namespace: str = NAMESPACE, tail: int = 50, grep: str | None = None) -> str:
    """get recent logs from the deployment.

    uses label selector instead of pod name so it works across all replicas.
    optional grep filter runs client-side (simpler than server-side filtering)
    and caps at 30 matching lines to avoid blowing up Claude's context.
    """
    cmd = f"logs -n {namespace} -l app.kubernetes.io/name={DEPLOYMENT} --tail={tail}"
    output = kubectl(cmd)
    if grep and "ERROR" not in output:
        # only filter if kubectl itself didnt error — otherwise we'd hide the error message
        lines = output.split("\n")
        filtered = [l for l in lines if grep.lower() in l.lower()]
        return "\n".join(filtered[-30:]) if filtered else f"no lines matching '{grep}'"
    return output


def get_hpa_status(namespace: str = NAMESPACE) -> str:
    """get HPA status."""
    return kubectl(f"get hpa -n {namespace} -o wide")


def get_recent_events(namespace: str = NAMESPACE, limit: int = 20) -> str:
    """get recent events sorted by timestamp."""
    return kubectl(f"get events -n {namespace} --sort-by=.lastTimestamp | tail -{limit}")


def get_deployment_status(namespace: str = NAMESPACE) -> str:
    """get rollout status and deployment details.

    combines two pieces of info: whether a rollout is in progress (rollout status)
    and the actual replica counts (ready vs desired). the jq filter keeps the
    output compact — Claude doesnt need the full deployment JSON.
    """
    status = kubectl(f"rollout status deployment/{DEPLOYMENT} -n {namespace} --timeout=5s")
    describe = kubectl(
        f"get deployment {DEPLOYMENT} -n {namespace} -o json"
        " | jq '{replicas: .status.replicas, ready: .status.readyReplicas,"
        " updated: .status.updatedReplicas, available: .status.availableReplicas}'"
    )
    return f"rollout: {status}\n\nreplicas: {describe}"


def check_anthropic_status() -> str:
    """check anthropic API status page.

    this is the first thing to check on latency alerts — if anthropic is
    degraded theres nothing we can do on our side. the statuspage API is
    public and doesnt need auth.
    """
    try:
        resp = httpx.get("https://status.anthropic.com/api/v2/status.json", timeout=5)
        data = resp.json()
        # indicator is one of: none, minor, major, critical
        indicator = data.get("status", {}).get("indicator", "unknown")
        description = data.get("status", {}).get("description", "unknown")
        return f"anthropic status: {indicator} — {description}"
    except Exception as e:
        return f"could not check anthropic status: {e}"


def check_endpoint_health(endpoint: str = "http://localhost:8080") -> dict:
    """check healthz and readyz endpoints.

    healthz is the liveness check (always 200 if the process is alive).
    readyz is the readiness check (checks postgres + S3 connectivity).
    if readyz is failing but healthz is fine, its a backing service issue not an app issue.
    """
    results = {}
    for path in ["/healthz", "/readyz"]:
        try:
            resp = httpx.get(f"{endpoint}{path}", timeout=5)
            results[path] = {"status": resp.status_code, "body": resp.json()}
        except Exception as e:
            results[path] = {"status": "unreachable", "error": str(e)}
    return results


def format_slack_block(title: str, body: str, color: str = "#36a64f") -> list:
    """format a slack message with blocks for nicer display.

    truncates title to 150 chars and body to 3000 chars because slack
    block kit has hard limits on text length. the plain text fallback
    in post_to_slack() handles the full content for clients that dont
    render blocks.
    """
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title[:150]},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": body[:3000]},
        },
    ]
