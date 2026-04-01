"""
Tests for the cost query Lambda.

Uses mocking for DynamoDB.
"""

from __future__ import annotations

import json
from unittest.mock import patch


from src.query import lambda_handler


class TestQueryHandler:
    @patch("src.query.get_baseline_metrics")
    @patch("src.query.get_cost_snapshots")
    def test_query_default_period(self, mock_snapshots, mock_baselines):
        """Query with default 30-day period."""
        mock_snapshots.return_value = [
            {"date": "2026-03-02", "total_cost": 1000.0, "by_service": {}},
            {"date": "2026-04-01", "total_cost": 1100.0, "by_service": {}},
        ]
        mock_baselines.return_value = {
            "period_days": 30,
            "data": [{"date": "2026-04-01", "average": 1050.0}],
        }

        result = lambda_handler({"queryStringParameters": None}, None)
        assert result["statusCode"] == 200

        body = json.loads(result["body"])
        assert body["period_days"] == 30
        assert len(body["snapshots"]) == 2

    @patch("src.query.get_baseline_metrics")
    @patch("src.query.get_cost_snapshots")
    def test_query_custom_days(self, mock_snapshots, mock_baselines):
        """Query with custom days parameter."""
        mock_snapshots.return_value = []
        mock_baselines.return_value = {"period_days": 7, "data": []}

        result = lambda_handler({"queryStringParameters": {"days": "7"}}, None)
        assert result["statusCode"] == 200

        body = json.loads(result["body"])
        assert body["period_days"] == 7
        mock_snapshots.assert_called_with(days=7)

    @patch("src.query.get_baseline_metrics")
    @patch("src.query.get_cost_snapshots")
    def test_query_days_clamped_max(self, mock_snapshots, mock_baselines):
        """Days parameter clamped to max 90."""
        mock_snapshots.return_value = []
        mock_baselines.return_value = {"period_days": 90, "data": []}

        result = lambda_handler({"queryStringParameters": {"days": "999"}}, None)
        assert result["statusCode"] == 200
        mock_snapshots.assert_called_with(days=90)

    @patch("src.query.get_baseline_metrics")
    @patch("src.query.get_cost_snapshots")
    def test_query_days_clamped_min(self, mock_snapshots, mock_baselines):
        """Days parameter clamped to min 1."""
        mock_snapshots.return_value = []
        mock_baselines.return_value = {"period_days": 1, "data": []}

        result = lambda_handler({"queryStringParameters": {"days": "-5"}}, None)
        assert result["statusCode"] == 200
        mock_snapshots.assert_called_with(days=1)

    @patch("src.query.get_baseline_metrics")
    @patch("src.query.get_cost_snapshots")
    def test_query_invalid_days_returns_400(self, mock_snapshots, mock_baselines):
        """Invalid days parameter returns 400."""
        result = lambda_handler({"queryStringParameters": {"days": "invalid"}}, None)
        assert result["statusCode"] == 400
        assert "error" in json.loads(result["body"])

    @patch("src.query.calculate_trend")
    @patch("src.query.get_baseline_metrics")
    @patch("src.query.get_cost_snapshots")
    def test_query_includes_trend(self, mock_snapshots, mock_baselines, mock_trend):
        """Query response includes trend metrics."""
        mock_snapshots.return_value = []
        mock_baselines.return_value = {"period_days": 30, "data": []}
        mock_trend.return_value = {
            "trend_pct": 5.2,
            "min": 900.0,
            "max": 1200.0,
            "avg": 1050.0,
            "latest": 1100.0,
        }

        result = lambda_handler({"queryStringParameters": None}, None)
        assert result["statusCode"] == 200

        body = json.loads(result["body"])
        assert body["trend"]["trend_pct"] == 5.2
        assert body["trend"]["latest"] == 1100.0
