"""
Tests for the cost analyzer Lambda.

Uses mocking for AWS and Anthropic APIs.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.analyzer import (
    CostData,
    analyze_with_claude,
    calculate_baseline,
    fetch_daily_costs,
    lambda_handler,
)


# ---------------------------------------------------------------------------
# CostData and helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_cost_data() -> CostData:
    """Fixture: sample cost data."""
    return CostData(
        date="2026-04-01",
        total_cost=1234.56,
        by_service={
            "Amazon Elastic Compute Cloud": 450.00,
            "Amazon Relational Database Service": 350.00,
            "AWS Lambda": 50.00,
            "AWS Key Management Service": 25.00,
        },
        timestamp=1712059200,
    )


@pytest.fixture
def sample_baseline() -> dict:
    """Fixture: sample baseline metrics."""
    return {
        "average": 1000.00,
        "min": 800.00,
        "max": 1200.00,
        "count": 30,
    }


# ---------------------------------------------------------------------------
# fetch_daily_costs
# ---------------------------------------------------------------------------

class TestFetchDailyCosts:
    @patch("src.analyzer.ce_client")
    def test_fetch_succeeds(self, mock_ce):
        """Successful fetch of daily costs."""
        mock_ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-03-31", "End": "2026-04-01"},
                    "Groups": [
                        {
                            "Keys": ["Amazon Elastic Compute Cloud"],
                            "Metrics": {"UnblendedCost": {"Amount": "500.00"}},
                        },
                        {
                            "Keys": ["AWS Lambda"],
                            "Metrics": {"UnblendedCost": {"Amount": "50.00"}},
                        },
                    ],
                }
            ]
        }

        result = fetch_daily_costs(days_back=1)
        assert result.total_cost == 550.00
        assert len(result.by_service) == 2

    @patch("src.analyzer.ce_client")
    def test_fetch_api_error_raises(self, mock_ce):
        """API error propagates."""
        mock_ce.get_cost_and_usage.side_effect = Exception("API error")
        with pytest.raises(Exception):
            fetch_daily_costs()


# ---------------------------------------------------------------------------
# calculate_baseline
# ---------------------------------------------------------------------------

class TestCalculateBaseline:
    @patch("src.analyzer.get_historical_costs")
    def test_baseline_with_data(self, mock_hist):
        """Baseline calculation with historical data."""
        mock_hist.return_value = [800.0, 900.0, 1000.0, 1100.0, 1200.0]
        baseline = calculate_baseline(days=30)
        assert baseline["average"] == 1000.0
        assert baseline["min"] == 800.0
        assert baseline["max"] == 1200.0

    @patch("src.analyzer.get_historical_costs")
    def test_baseline_empty_history(self, mock_hist):
        """Baseline with no historical data."""
        mock_hist.return_value = []
        baseline = calculate_baseline()
        assert baseline["average"] == 0.0


# ---------------------------------------------------------------------------
# analyze_with_claude
# ---------------------------------------------------------------------------

class TestAnalyzeWithClaude:
    @patch("src.analyzer.anthropic.Anthropic")
    def test_analysis_succeeds(self, mock_anthropic_cls, sample_cost_data, sample_baseline):
        """Successful Claude analysis."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Analysis of cost spike...")]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_cls.return_value = mock_client

        result = analyze_with_claude(sample_cost_data, sample_baseline, variance_pct=23.5)
        assert "Analysis of cost spike" in result
        assert mock_client.messages.create.called


# ---------------------------------------------------------------------------
# lambda_handler (integration)
# ---------------------------------------------------------------------------

class TestLambdaHandler:
    @patch("src.analyzer.send_alert")
    @patch("src.analyzer.analyze_with_claude")
    @patch("src.analyzer.store_baseline")
    @patch("src.analyzer.store_cost_snapshot")
    @patch("src.analyzer.calculate_baseline")
    @patch("src.analyzer.fetch_daily_costs")
    def test_handler_no_anomaly(
        self,
        mock_fetch,
        mock_baseline,
        mock_store_snap,
        mock_store_base,
        mock_analyze,
        mock_alert,
        sample_cost_data,
        sample_baseline,
    ):
        """Handler processes normal costs (no anomaly)."""
        sample_cost_data.total_cost = 1020.0  # 2% above baseline
        mock_fetch.return_value = sample_cost_data
        mock_baseline.return_value = sample_baseline
        mock_analyze.return_value = "Costs are within normal range."

        result = lambda_handler({}, None)
        assert result["statusCode"] == 200

        body = json.loads(result["body"])
        assert body["is_anomaly"] is False
        assert not mock_alert.called

    @patch("src.analyzer.send_alert")
    @patch("src.analyzer.analyze_with_claude")
    @patch("src.analyzer.store_baseline")
    @patch("src.analyzer.store_cost_snapshot")
    @patch("src.analyzer.calculate_baseline")
    @patch("src.analyzer.fetch_daily_costs")
    def test_handler_detects_anomaly(
        self,
        mock_fetch,
        mock_baseline,
        mock_store_snap,
        mock_store_base,
        mock_analyze,
        mock_alert,
        sample_cost_data,
        sample_baseline,
    ):
        """Handler detects anomaly and sends alert."""
        sample_cost_data.total_cost = 1250.0  # 25% above baseline
        mock_fetch.return_value = sample_cost_data
        mock_baseline.return_value = sample_baseline
        mock_analyze.return_value = "Significant cost spike detected due to EC2 usage."

        result = lambda_handler({}, None)
        assert result["statusCode"] == 200

        body = json.loads(result["body"])
        assert body["is_anomaly"] is True
        assert body["variance_pct"] > 20.0
        assert mock_alert.called

    @patch("src.analyzer.fetch_daily_costs")
    def test_handler_error_propagates(self, mock_fetch):
        """Handler returns 500 on unexpected error."""
        mock_fetch.side_effect = Exception("Unexpected failure")

        result = lambda_handler({}, None)
        assert result["statusCode"] == 500
        assert "error" in json.loads(result["body"])
