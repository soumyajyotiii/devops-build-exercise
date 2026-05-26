#!/usr/bin/env python3
"""
incident triage agent.

uses claude with tool-use to automatically diagnose alerts from alertmanager.
can be triggered via alertmanager webhook or run manually for any alert.

usage:
    # manual — pass alert info directly
    python incident_triage.py --alert "HighP95Latency" --severity "warning" \
        --description "p95 latency is above 8s SLO"

    # webhook mode — runs as a tiny HTTP server that alertmanager posts to
    python incident_triage.py --serve --port 9095

the agent will:
1. receive the alert context
2. use Claude with tools to run kubectl commands, check external deps, read logs
3. synthesize findings into a diagnosis
4. post the diagnosis to slack
"""

import argparse
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

import anthropic

from utils import (
    post_to_slack,
    format_slack_block,
    get_pod_status,
    get_pod_logs,
    get_hpa_status,
    get_recent_events,
    get_deployment_status,
    check_anthropic_status,
    check_endpoint_health,
    log,
    NAMESPACE,
)

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env automatically

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# -- tool definitions for Claude tool-use --
# these describe what diagnostic tools Claude can call. each one maps to a
# real function in utils.py that runs kubectl or checks an external service.
#
# the key insight: different alerts need different diagnostics. a latency alert
# should check anthropic status first. a crash loop should look at pod describe
# and previous logs. by letting Claude pick the tools, we avoid hardcoding
# decision trees for every alert type.
TOOLS = [
    {
        "name": "get_pod_status",
        "description": "get the status of all pods in the underwriting-agent namespace. shows pod names, status, restarts, age, node, and IP.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_pod_logs",
        "description": "get recent logs from the underwriting-agent pods. optionally filter by a grep pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tail": {
                    "type": "integer",
                    "description": "number of recent log lines to fetch. default 50.",
                    "default": 50,
                },
                "grep": {
                    "type": "string",
                    "description": "optional pattern to filter log lines (case-insensitive).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_hpa_status",
        "description": "get the horizontal pod autoscaler status — current/min/max replicas, CPU/memory utilization.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_recent_events",
        "description": "get recent kubernetes events in the namespace — useful for spotting OOM kills, scheduling failures, probe failures.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "number of recent events to fetch. default 20.",
                    "default": 20,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_deployment_status",
        "description": "get the rollout status of the deployment — whether its rolling out, how many replicas are ready/updated/available.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_anthropic_status",
        "description": "check the anthropic API status page to see if the LLM provider is degraded or down.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_endpoint_health",
        "description": "check the /healthz and /readyz endpoints of the service. readyz checks postgres and S3 connectivity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {
                    "type": "string",
                    "description": "base URL of the service. default http://localhost:8080 (use port-forward).",
                    "default": "http://localhost:8080",
                },
            },
            "required": [],
        },
    },
]

SYSTEM_PROMPT = """you are an SRE incident triage agent for the underwriting-assist service at Saaf Finance.

the service is a FastAPI app that processes underwriting items on mortgage loans using Claude (LLM).
it runs on kubernetes (EKS) with postgres (RDS) and S3 as backing stores.

when you receive an alert, your job is to:
1. gather diagnostic information using the available tools
2. identify the likely root cause
3. assess severity and impact
4. recommend specific actions

key context:
- LLM calls dominate latency (3-6 seconds typical). if latency is high, check anthropic status first.
- traffic is bursty — peaks at 9-11am ET and 2-4pm ET. HPA handles this.
- the service is stateless. pod restarts are generally safe.
- readiness probe checks postgres + S3. if its failing, one of those is down.
- the service handles borrower PII — be mindful of data sensitivity in your output.

be concise and direct. the on-call engineer reading your output is probably tired.

output format:
- root cause (1-2 sentences)
- severity assessment (is this impacting users right now?)
- recommended action (what should the engineer do, or what you already did)
- whether auto-remediation is safe (yes/no and why)
"""


def execute_tool(tool_name: str, tool_input: dict) -> str:
    """execute a tool call and return the result as a string.

    maps Claude's tool_use requests to actual functions. returns strings
    because thats what the Anthropic API expects in tool_result blocks.
    """
    match tool_name:
        case "get_pod_status":
            return get_pod_status()
        case "get_pod_logs":
            return get_pod_logs(
                tail=tool_input.get("tail", 50),
                grep=tool_input.get("grep"),
            )
        case "get_hpa_status":
            return get_hpa_status()
        case "get_recent_events":
            return get_recent_events(limit=tool_input.get("limit", 20))
        case "get_deployment_status":
            return get_deployment_status()
        case "check_anthropic_status":
            return check_anthropic_status()
        case "check_endpoint_health":
            return json.dumps(
                check_endpoint_health(
                    endpoint=tool_input.get("endpoint", "http://localhost:8080")
                ),
                indent=2,
            )
        case _:
            return f"unknown tool: {tool_name}"


def run_triage(alert_name: str, severity: str, description: str) -> str:
    """run the triage agent for a given alert. returns the diagnosis text."""

    user_message = f"""alert fired:
- name: {alert_name}
- severity: {severity}
- description: {description}
- namespace: {NAMESPACE}
- time: now

please diagnose this alert. use the available tools to gather information, then provide your assessment."""

    messages = [{"role": "user", "content": user_message}]

    # -- agentic loop --
    # this is the core pattern for Claude tool-use: we keep calling the API
    # in a loop. each iteration, Claude either requests tools (stop_reason="tool_use")
    # or produces a final text response (stop_reason="end_turn").
    #
    # typical flow for a latency alert:
    #   iteration 1: Claude calls check_anthropic_status + get_pod_status
    #   iteration 2: Claude calls get_pod_logs(grep="latency") based on what it saw
    #   iteration 3: Claude produces final diagnosis text
    #
    # max_iterations is a safety valve — in practice it converges in 2-4 iterations.
    max_iterations = 10
    for i in range(max_iterations):
        log.info(f"triage iteration {i + 1}...")

        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            # Claude wants to gather more info — append its response to the
            # conversation and execute whatever tools it asked for.
            messages.append({"role": "assistant", "content": response.content})

            # execute each tool call and send results back
            # (Claude can request multiple tools in one turn)
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    log.info(f"  calling tool: {block.name}")
                    result = execute_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )

            # tool results go in a "user" message — this is how the Anthropic
            # API expects tool results to be returned
            messages.append({"role": "user", "content": tool_results})

        else:
            # Claude produced a final text response — we're done
            diagnosis = ""
            for block in response.content:
                if hasattr(block, "text"):
                    diagnosis += block.text
            return diagnosis

    return "triage agent hit max iterations without producing a final diagnosis. check logs."


def handle_alertmanager_payload(payload: dict) -> str:
    """parse alertmanager webhook payload and run triage for each alert.

    alertmanager sends both "firing" and "resolved" alerts in the same webhook.
    we only care about firing ones — resolved alerts dont need diagnosis.
    """
    alerts = payload.get("alerts", [])
    results = []

    for alert in alerts:
        if alert.get("status") != "firing":
            continue

        name = alert.get("labels", {}).get("alertname", "unknown")
        severity = alert.get("labels", {}).get("severity", "unknown")
        description = alert.get("annotations", {}).get("description", "")
        if not description:
            description = alert.get("annotations", {}).get("summary", "no description")

        log.info(f"triaging alert: {name} ({severity})")
        diagnosis = run_triage(name, severity, description)
        results.append((name, severity, diagnosis))

        # post to slack
        slack_title = f"🔍 auto-triage: {name} ({severity})"
        post_to_slack(
            text=f"{slack_title}\n\n{diagnosis}",
            blocks=format_slack_block(slack_title, diagnosis),
        )

    return "\n---\n".join(
        f"[{name}] ({severity})\n{diag}" for name, severity, diag in results
    )


class WebhookHandler(BaseHTTPRequestHandler):
    """simple HTTP handler for alertmanager webhooks."""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            payload = json.loads(body)
            result = handle_alertmanager_payload(payload)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(result.encode())
        except Exception as e:
            log.exception("webhook handler error")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def log_message(self, format, *args):
        log.info(f"webhook: {format % args}")


def main():
    parser = argparse.ArgumentParser(description="AI-powered incident triage agent")
    parser.add_argument("--alert", help="alert name (e.g. HighP95Latency)")
    parser.add_argument("--severity", default="warning", help="alert severity")
    parser.add_argument("--description", default="", help="alert description")
    parser.add_argument("--serve", action="store_true", help="run as webhook server")
    parser.add_argument("--port", type=int, default=9095, help="webhook server port")

    args = parser.parse_args()

    if args.serve:
        # webhook mode — alertmanager posts here when alerts fire.
        # in production this runs as a k8s deployment (or sidecar) with a
        # ClusterIP service so alertmanager can reach it.
        server = HTTPServer(("0.0.0.0", args.port), WebhookHandler)
        log.info(f"incident triage webhook server listening on port {args.port}")
        server.serve_forever()

    elif args.alert:
        # manual mode — useful for testing or re-triaging old alerts
        diagnosis = run_triage(args.alert, args.severity, args.description)
        print(diagnosis)

        post_to_slack(
            text=f"🔍 auto-triage: {args.alert}\n\n{diagnosis}",
            blocks=format_slack_block(f"auto-triage: {args.alert}", diagnosis),
        )

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
