###############################################################################
# Cost Anomaly Detector — Infrastructure
#
# Deploys:
#   - EventBridge rule (daily cron) → Analyzer Lambda
#   - Two Lambda functions (analyzer + query)
#   - DynamoDB table for cost snapshots and baselines
#   - API Gateway HTTP API for cost queries
#   - SNS topic for cost anomaly alerts
#   - SSM parameters for configuration
#   - IAM roles + policies
#   - CloudWatch log groups and alarms
###############################################################################

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Owner       = "Three-Moons-Network"
    }
  }
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile name"
  type        = string
  default     = "default"
}

variable "project_name" {
  description = "Project identifier used in resource naming"
  type        = string
  default     = "cost-anomaly-detector"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "uat", "prod"], var.environment)
    error_message = "Environment must be dev, uat, or prod."
  }
}

variable "anthropic_api_key" {
  description = "Anthropic API key for Claude inference"
  type        = string
  sensitive   = true
}

variable "anthropic_model" {
  description = "Claude model to use for cost analysis"
  type        = string
  default     = "claude-sonnet-4-20250514"
}

variable "anomaly_threshold_pct" {
  description = "Cost variance threshold (%) to trigger alert"
  type        = number
  default     = 20
}

variable "baseline_days" {
  description = "Number of days for rolling baseline calculation"
  type        = number
  default     = 30
}

variable "alert_email" {
  description = "Email address for cost anomaly alerts"
  type        = string
  default     = ""
}

variable "lambda_memory" {
  description = "Lambda memory in MB"
  type        = number
  default     = 256
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 60
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

variable "dynamodb_billing_mode" {
  description = "DynamoDB billing mode (PAY_PER_REQUEST or PROVISIONED)"
  type        = string
  default     = "PAY_PER_REQUEST"
}

locals {
  prefix = "${var.project_name}-${var.environment}"
}

# ---------------------------------------------------------------------------
# SSM Parameters
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "anthropic_api_key" {
  name        = "/${var.project_name}/${var.environment}/anthropic-api-key"
  description = "Anthropic API key for Claude inference"
  type        = "SecureString"
  value       = var.anthropic_api_key

  tags = {
    Name = "${local.prefix}-anthropic-api-key"
  }
}

resource "aws_ssm_parameter" "anomaly_threshold" {
  name        = "/${var.project_name}/${var.environment}/anomaly-threshold-pct"
  description = "Cost variance threshold for alerts"
  type        = "String"
  value       = tostring(var.anomaly_threshold_pct)

  tags = {
    Name = "${local.prefix}-anomaly-threshold"
  }
}

# ---------------------------------------------------------------------------
# DynamoDB Table
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "costs" {
  name           = local.prefix
  billing_mode   = var.dynamodb_billing_mode
  hash_key       = "pk"
  range_key      = "sk"
  stream_enabled = false

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.environment == "prod" ? true : false
  }

  tags = {
    Name = "${local.prefix}-table"
  }
}

# ---------------------------------------------------------------------------
# SNS Topic for Alerts
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name              = "${local.prefix}-alerts"
  display_name      = "Cost Anomaly Alerts"
  kms_master_key_id = "alias/aws/sns"

  tags = {
    Name = "${local.prefix}-alerts"
  }
}

resource "aws_sns_topic_subscription" "alert_email" {
  count         = var.alert_email != "" ? 1 : 0
  topic_arn     = aws_sns_topic.alerts.arn
  protocol      = "email"
  endpoint      = var.alert_email
  filter_policy = jsonencode({})
}

# ---------------------------------------------------------------------------
# IAM Roles
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "analyzer" {
  name               = "${local.prefix}-analyzer-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role" "query" {
  name               = "${local.prefix}-query-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

# Analyzer permissions
data "aws_iam_policy_document" "analyzer_permissions" {
  # CloudWatch Logs
  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.analyzer.arn}:*"]
  }

  # SSM Parameter Store — read config and API key
  statement {
    actions = ["ssm:GetParameter"]
    resources = [
      aws_ssm_parameter.anthropic_api_key.arn,
      aws_ssm_parameter.anomaly_threshold.arn,
    ]
  }

  # Cost Explorer API
  statement {
    actions   = ["ce:GetCostAndUsage"]
    resources = ["*"]
  }

  # DynamoDB — store snapshots and baselines
  statement {
    actions = [
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]
    resources = [aws_dynamodb_table.costs.arn]
  }

  # SNS — send alerts
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }
}

resource "aws_iam_role_policy" "analyzer" {
  name   = "${local.prefix}-analyzer-policy"
  role   = aws_iam_role.analyzer.id
  policy = data.aws_iam_policy_document.analyzer_permissions.json
}

# Query permissions
data "aws_iam_policy_document" "query_permissions" {
  # CloudWatch Logs
  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.query.arn}:*"]
  }

  # DynamoDB — read only
  statement {
    actions = [
      "dynamodb:Query",
      "dynamodb:Scan",
    ]
    resources = [aws_dynamodb_table.costs.arn]
  }
}

resource "aws_iam_role_policy" "query" {
  name   = "${local.prefix}-query-policy"
  role   = aws_iam_role.query.id
  policy = data.aws_iam_policy_document.query_permissions.json
}

# ---------------------------------------------------------------------------
# CloudWatch Log Groups
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "analyzer" {
  name              = "/aws/lambda/${local.prefix}-analyzer"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "query" {
  name              = "/aws/lambda/${local.prefix}-query"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "api_gw" {
  name              = "/aws/apigateway/${local.prefix}"
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------------------
# Lambda Functions
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "analyzer" {
  function_name = "${local.prefix}-analyzer"
  description   = "Analyze daily AWS costs and detect anomalies"
  runtime       = "python3.11"
  handler       = "analyzer.lambda_handler"
  memory_size   = var.lambda_memory
  timeout       = var.lambda_timeout
  role          = aws_iam_role.analyzer.arn

  filename         = "${path.module}/../dist/analyzer.zip"
  source_code_hash = fileexists("${path.module}/../dist/analyzer.zip") ? filebase64sha256("${path.module}/../dist/analyzer.zip") : null

  environment {
    variables = {
      ENVIRONMENT           = var.environment
      DYNAMODB_TABLE        = aws_dynamodb_table.costs.name
      SNS_TOPIC_ARN         = aws_sns_topic.alerts.arn
      ANTHROPIC_MODEL       = var.anthropic_model
      ANTHROPIC_API_KEY     = var.anthropic_api_key
      ANOMALY_THRESHOLD_PCT = tostring(var.anomaly_threshold_pct)
      BASELINE_DAYS         = tostring(var.baseline_days)
      LOG_LEVEL             = var.environment == "prod" ? "WARNING" : "INFO"
    }
  }

  depends_on = [
    aws_iam_role_policy.analyzer,
    aws_cloudwatch_log_group.analyzer,
  ]
}

resource "aws_lambda_function" "query" {
  function_name = "${local.prefix}-query"
  description   = "Query historical cost data and trends"
  runtime       = "python3.11"
  handler       = "query.lambda_handler"
  memory_size   = 256
  timeout       = 30
  role          = aws_iam_role.query.arn

  filename         = "${path.module}/../dist/query.zip"
  source_code_hash = fileexists("${path.module}/../dist/query.zip") ? filebase64sha256("${path.module}/../dist/query.zip") : null

  environment {
    variables = {
      ENVIRONMENT    = var.environment
      DYNAMODB_TABLE = aws_dynamodb_table.costs.name
      LOG_LEVEL      = var.environment == "prod" ? "WARNING" : "INFO"
    }
  }

  depends_on = [
    aws_iam_role_policy.query,
    aws_cloudwatch_log_group.query,
  ]
}

# ---------------------------------------------------------------------------
# EventBridge Rule (Daily Cron)
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "daily_analysis" {
  name                = "${local.prefix}-daily-analysis"
  description         = "Trigger cost analysis daily at 8 AM UTC"
  schedule_expression = "cron(0 8 * * ? *)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "analyzer_lambda" {
  rule      = aws_cloudwatch_event_rule.daily_analysis.name
  target_id = "${local.prefix}-analyzer"
  arn       = aws_lambda_function.analyzer.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.analyzer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_analysis.arn
}

# ---------------------------------------------------------------------------
# API Gateway (HTTP API)
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_api" "main" {
  name          = "${local.prefix}-api"
  protocol_type = "HTTP"
  description   = "Cost data query API"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "OPTIONS"]
    allow_headers = ["Content-Type", "Authorization"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_integration" "query" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.query.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "get_costs" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "GET /costs"
  target    = "integrations/${aws_apigatewayv2_integration.query.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = 10
    throttling_burst_limit = 20
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gw.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      method         = "$context.httpMethod"
      path           = "$context.path"
      status         = "$context.status"
      latency        = "$context.responseLatency"
      integrationErr = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_lambda_permission" "api_gw_query" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}

# ---------------------------------------------------------------------------
# CloudWatch Alarms
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "analyzer_errors" {
  alarm_name          = "${local.prefix}-analyzer-errors"
  alarm_description   = "Analyzer Lambda error rate exceeded"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.analyzer.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "analyzer_duration" {
  alarm_name          = "${local.prefix}-analyzer-duration"
  alarm_description   = "Analyzer Lambda duration exceeded threshold"
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  extended_statistic  = "p99"
  period              = 3600
  evaluation_periods  = 2
  threshold           = var.lambda_timeout * 1000 * 0.8
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.analyzer.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "query_errors" {
  alarm_name          = "${local.prefix}-query-errors"
  alarm_description   = "Query Lambda error rate exceeded"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.query.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "dynamodb_throttle" {
  alarm_name          = "${local.prefix}-dynamodb-throttle"
  alarm_description   = "DynamoDB read/write throttling detected"
  namespace           = "AWS/DynamoDB"
  metric_name         = "ConsumedWriteCapacityUnits"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 100
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.costs.name
  }
}
