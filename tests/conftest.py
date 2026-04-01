"""
Pytest configuration and fixtures for cost-anomaly-detector tests.

Provides moto mocking for AWS services (DynamoDB, SNS, Cost Explorer).
Uses mock_aws for moto >= 5.0.
"""

from __future__ import annotations

import os
import sys

# Add repo root to sys.path so src imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Set AWS env vars at module level (before fixtures)
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("DYNAMODB_TABLE", "cost-anomaly-detector")
os.environ.setdefault(
    "SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:cost-anomaly-alerts"
)
os.environ.setdefault("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
os.environ.setdefault("LOG_LEVEL", "INFO")

import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def aws_credentials():
    """Fixture: mock AWS credentials."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def dynamodb_table(aws_credentials):
    """Fixture: mock DynamoDB table."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="cost-anomaly-detector",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield table


@pytest.fixture
def sns_topic(aws_credentials):
    """Fixture: mock SNS topic."""
    with mock_aws():
        sns = boto3.client("sns", region_name="us-east-1")
        response = sns.create_topic(Name="cost-anomaly-alerts")
        yield response["TopicArn"]


@pytest.fixture
def ce_client(aws_credentials):
    """Fixture: mock Cost Explorer client."""
    with mock_aws():
        yield boto3.client("ce", region_name="us-east-1")
