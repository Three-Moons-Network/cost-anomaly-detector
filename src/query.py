"""
Cost Anomaly Detector — Query Lambda

Provides HTTP API endpoint for querying historical cost data and trends.
GET /costs returns snapshots, aggregations, and trend analysis.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "cost-anomaly-detector")

# AWS clients
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMODB_TABLE)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_cost_snapshots(days: int = 30) -> list[dict[str, Any]]:
    """Retrieve cost snapshots for the last N days."""
    try:
        snapshots = []
        # Query snapshots from the past N days
        today = datetime.utcnow()
        for i in range(days):
            date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            response = table.query(
                KeyConditionExpression="pk = :pk",
                ExpressionAttributeValues={":pk": f"snapshot#{date_str}"},
            )
            for item in response.get("Items", []):
                snapshots.append({
                    "date": item.get("date"),
                    "total_cost": item.get("total_cost", 0.0),
                    "by_service": item.get("by_service", {}),
                    "timestamp": item.get("timestamp"),
                })
        return sorted(snapshots, key=lambda x: x["date"])
    except Exception as exc:
        logger.error(f"Failed to query snapshots: {exc}")
        return []


def get_baseline_metrics(days: int = 30) -> dict[str, Any]:
    """Retrieve baseline metrics for the specified period."""
    try:
        baselines = []
        today = datetime.utcnow()
        for i in range(days):
            date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            response = table.query(
                KeyConditionExpression="pk = :pk",
                ExpressionAttributeValues={":pk": f"baseline#{date_str}"},
            )
            for item in response.get("Items", []):
                baselines.append({
                    "date": item.get("date"),
                    "average": item.get("average", 0.0),
                    "min": item.get("min", 0.0),
                    "max": item.get("max", 0.0),
                })
        return {
            "period_days": len(baselines),
            "data": sorted(baselines, key=lambda x: x["date"]),
        }
    except Exception as exc:
        logger.error(f"Failed to query baselines: {exc}")
        return {}


def calculate_trend(snapshots: list[dict[str, Any]]) -> dict[str, float]:
    """Calculate trend metrics from snapshots."""
    if not snapshots:
        return {"trend": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}

    costs = [s["total_cost"] for s in snapshots]
    first_half = costs[: len(costs) // 2]
    second_half = costs[len(costs) // 2 :]

    avg_first = sum(first_half) / len(first_half) if first_half else 0.0
    avg_second = sum(second_half) / len(second_half) if second_half else 0.0
    trend = ((avg_second - avg_first) / avg_first * 100) if avg_first > 0 else 0.0

    return {
        "trend_pct": trend,
        "min": min(costs),
        "max": max(costs),
        "avg": sum(costs) / len(costs),
        "latest": costs[-1] if costs else 0.0,
    }


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context: Any) -> dict:
    """
    API Gateway handler for cost query endpoint.

    Query parameters:
      - days: Number of days to retrieve (default: 30, max: 90)

    Returns historical costs, baselines, and trend analysis.
    """
    logger.info("Received cost query request")

    try:
        # Parse query parameters
        query_params = event.get("queryStringParameters") or {}
        days = int(query_params.get("days", "30"))
        days = min(max(days, 1), 90)  # Clamp to 1-90 days

        logger.info(f"Querying costs for last {days} days")

        # Retrieve data
        snapshots = get_cost_snapshots(days=days)
        baselines = get_baseline_metrics(days=days)
        trend = calculate_trend(snapshots)

        # Build response
        response_body = {
            "period_days": days,
            "snapshots": snapshots,
            "baselines": baselines,
            "trend": trend,
            "generated_at": datetime.utcnow().isoformat(),
        }

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps(response_body),
        }

    except (ValueError, KeyError) as exc:
        logger.warning(f"Validation error: {exc}")
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(exc)}),
        }

    except Exception:
        logger.exception("Unexpected error during cost query")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Failed to retrieve cost data"}),
        }
