"""
Cost Anomaly Detector — Analyzer Lambda

Fetches daily and weekly AWS cost data from Cost Explorer API, compares against
historical baselines stored in DynamoDB, and uses Claude to generate natural-language
analysis of anomalies, trends, and recommendations.

Triggered daily via EventBridge cron.
Stores cost snapshots and baseline calculations in DynamoDB.
Sends alerts via SNS when anomalies exceed the configured threshold.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

import anthropic
import boto3

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "cost-anomaly-detector")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
ANOMALY_THRESHOLD_PCT = float(os.environ.get("ANOMALY_THRESHOLD_PCT", "20"))
BASELINE_DAYS = int(os.environ.get("BASELINE_DAYS", "30"))

# AWS clients
ce_client = boto3.client("ce")
dynamodb = boto3.resource("dynamodb")
sns_client = boto3.client("sns")

table = dynamodb.Table(DYNAMODB_TABLE)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CostData:
    """Daily cost breakdown by service."""
    date: str
    total_cost: float
    by_service: dict[str, float]
    timestamp: int


@dataclass
class AnalysisResult:
    """Result of cost analysis and Claude interpretation."""
    analysis_date: str
    current_total: float
    baseline_avg: float
    variance_pct: float
    is_anomaly: bool
    analysis_text: str
    services_summary: dict[str, Any]


# ---------------------------------------------------------------------------
# Cost Explorer interaction
# ---------------------------------------------------------------------------

def fetch_daily_costs(days_back: int = 1) -> CostData:
    """
    Fetch daily cost data from Cost Explorer API, aggregated by service.
    Returns data for the most recent N days.
    """
    end_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    start_date = (datetime.utcnow() - timedelta(days=days_back + 1)).strftime("%Y-%m-%d")

    try:
        response = ce_client.get_cost_and_usage(
            TimePeriod={"Start": start_date, "End": end_date},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
    except Exception as exc:
        logger.error(f"Cost Explorer API error: {exc}")
        raise

    # Parse response: aggregate by service across the date range
    by_service: dict[str, float] = {}
    total_cost = 0.0

    for result in response.get("ResultsByTime", []):
        for group in result.get("Groups", []):
            service_name = group["Keys"][0]
            cost_str = group["Metrics"]["UnblendedCost"]["Amount"]
            cost = float(cost_str)

            by_service[service_name] = by_service.get(service_name, 0) + cost
            total_cost += cost

    # Return data for the end of the range (latest day)
    return CostData(
        date=end_date,
        total_cost=total_cost,
        by_service={k: v for k, v in sorted(by_service.items(), key=lambda x: x[1], reverse=True)},
        timestamp=int(time.time()),
    )


# ---------------------------------------------------------------------------
# DynamoDB baseline and snapshot storage
# ---------------------------------------------------------------------------

def store_cost_snapshot(cost_data: CostData) -> None:
    """Store daily cost snapshot in DynamoDB for trend analysis."""
    try:
        table.put_item(
            Item={
                "pk": f"snapshot#{cost_data.date}",
                "sk": f"ts#{cost_data.timestamp}",
                "date": cost_data.date,
                "total_cost": cost_data.total_cost,
                "by_service": cost_data.by_service,
                "timestamp": cost_data.timestamp,
                "ttl": int(time.time()) + (90 * 86400),  # 90 day retention
            }
        )
        logger.info(f"Stored cost snapshot for {cost_data.date}: ${cost_data.total_cost:.2f}")
    except Exception as exc:
        logger.error(f"Failed to store snapshot: {exc}")
        raise


def get_historical_costs(days: int = 30) -> list[float]:
    """Retrieve historical daily costs for baseline calculation."""
    try:
        # Query for all snapshots from the last N days
        response = table.query(
            KeyConditionExpression="pk = :pk",
            ExpressionAttributeValues={":pk": f"snapshot#{(datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')}"},
        )

        # In production, this would scan/query more smartly; for MVP, we fetch snapshots directly
        # This is a simplified approach — in real usage, you'd store rolling-window baseline
        costs = [float(item["total_cost"]) for item in response.get("Items", []) if "total_cost" in item]
        return sorted(costs) if costs else [0.0]
    except Exception as exc:
        logger.warning(f"Failed to retrieve historical costs: {exc}. Using empty baseline.")
        return []


def calculate_baseline(days: int = 30) -> dict[str, float]:
    """Calculate rolling 30-day average cost baseline."""
    historical = get_historical_costs(days)
    if not historical:
        return {"average": 0.0, "min": 0.0, "max": 0.0}

    return {
        "average": sum(historical) / len(historical),
        "min": min(historical),
        "max": max(historical),
        "count": len(historical),
    }


def store_baseline(date: str, baseline: dict[str, float]) -> None:
    """Store baseline metrics in DynamoDB."""
    try:
        table.put_item(
            Item={
                "pk": f"baseline#{date}",
                "sk": "metrics",
                "date": date,
                "average": baseline["average"],
                "min": baseline.get("min", 0.0),
                "max": baseline.get("max", 0.0),
                "count": baseline.get("count", 0),
                "timestamp": int(time.time()),
            }
        )
    except Exception as exc:
        logger.error(f"Failed to store baseline: {exc}")


# ---------------------------------------------------------------------------
# Claude analysis
# ---------------------------------------------------------------------------

def analyze_with_claude(
    cost_data: CostData,
    baseline: dict[str, float],
    variance_pct: float,
) -> str:
    """Use Claude to generate natural-language analysis of cost data."""
    client = anthropic.Anthropic()

    service_details = "\n".join(
        [f"  - {service}: ${cost:.2f}" for service, cost in list(cost_data.by_service.items())[:10]]
    )

    system_prompt = (
        "You are an AWS cost optimization expert. Analyze the provided daily cost data "
        "and identify anomalies, trends, and actionable recommendations for cost reduction. "
        "Be concise but informative. Focus on the biggest spenders and unusual changes."
    )

    user_message = f"""
Cost Data for {cost_data.date}:
- Total cost: ${cost_data.total_cost:.2f}
- Baseline (30-day avg): ${baseline.get('average', 0.0):.2f}
- Variance: {variance_pct:.1f}%

Top Services:
{service_details}

Provide:
1. A brief anomaly assessment (is this spike expected?)
2. Top 3 cost drivers
3. One specific optimization recommendation
"""

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=512,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    return response.content[0].text


# ---------------------------------------------------------------------------
# SNS alerting
# ---------------------------------------------------------------------------

def send_alert(analysis: AnalysisResult) -> None:
    """Send cost anomaly alert via SNS."""
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN not set; skipping alert.")
        return

    try:
        subject = f"AWS Cost Anomaly Alert - {analysis.analysis_date}"
        message = f"""
Cost Anomaly Detected!

Date: {analysis.analysis_date}
Current Total Cost: ${analysis.current_total:.2f}
Baseline (30-day avg): ${analysis.baseline_avg:.2f}
Variance: {analysis.variance_pct:.1f}%

Analysis:
{analysis.analysis_text}

---
Cost Anomaly Detector
Three Moons Network
"""

        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message,
        )
        logger.info(f"Sent alert for {analysis.analysis_date} (variance: {analysis.variance_pct:.1f}%)")
    except Exception as exc:
        logger.error(f"Failed to send alert: {exc}")


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context: Any) -> dict:
    """
    EventBridge-triggered handler.
    - Fetches daily costs from Cost Explorer
    - Compares against 30-day rolling baseline
    - Stores snapshot in DynamoDB
    - Uses Claude to analyze and generate recommendations
    - Sends SNS alert if variance exceeds threshold
    """
    logger.info("Starting cost anomaly analysis")

    try:
        # Fetch latest daily costs
        cost_data = fetch_daily_costs(days_back=1)
        logger.info(f"Fetched costs for {cost_data.date}: ${cost_data.total_cost:.2f}")

        # Store snapshot
        store_cost_snapshot(cost_data)

        # Calculate baseline and check for anomaly
        baseline = calculate_baseline(days=BASELINE_DAYS)
        store_baseline(cost_data.date, baseline)

        baseline_avg = baseline.get("average", 0.0)
        variance_pct = ((cost_data.total_cost - baseline_avg) / baseline_avg * 100) if baseline_avg > 0 else 0

        is_anomaly = variance_pct > ANOMALY_THRESHOLD_PCT

        logger.info(
            f"Analysis: baseline=${baseline_avg:.2f}, current=${cost_data.total_cost:.2f}, "
            f"variance={variance_pct:.1f}%, anomaly={is_anomaly}"
        )

        # Generate analysis
        analysis_text = analyze_with_claude(cost_data, baseline, variance_pct)

        analysis = AnalysisResult(
            analysis_date=cost_data.date,
            current_total=cost_data.total_cost,
            baseline_avg=baseline_avg,
            variance_pct=variance_pct,
            is_anomaly=is_anomaly,
            analysis_text=analysis_text,
            services_summary={
                "top_service": next(iter(cost_data.by_service.items())) if cost_data.by_service else None,
                "total_services": len(cost_data.by_service),
            },
        )

        # Alert if anomaly
        if is_anomaly:
            send_alert(analysis)

        return {
            "statusCode": 200,
            "body": json.dumps(asdict(analysis), default=str),
        }

    except Exception:
        logger.exception("Unexpected error during cost analysis")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Cost analysis failed"}),
        }
