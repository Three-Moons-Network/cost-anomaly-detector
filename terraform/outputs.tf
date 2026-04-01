output "analyzer_function_arn" {
  description = "ARN of the analyzer Lambda function"
  value       = aws_lambda_function.analyzer.arn
}

output "analyzer_function_name" {
  description = "Name of the analyzer Lambda function"
  value       = aws_lambda_function.analyzer.function_name
}

output "query_function_arn" {
  description = "ARN of the query Lambda function"
  value       = aws_lambda_function.query.arn
}

output "query_function_name" {
  description = "Name of the query Lambda function"
  value       = aws_lambda_function.query.function_name
}

output "api_endpoint" {
  description = "Base URL for the cost query API"
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "query_url" {
  description = "Full URL for the cost query endpoint"
  value       = "${aws_apigatewayv2_api.main.api_endpoint}/costs"
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table storing cost data"
  value       = aws_dynamodb_table.costs.table_name
}

output "sns_topic_arn" {
  description = "ARN of the SNS topic for cost anomaly alerts"
  value       = aws_sns_topic.alerts.arn
}

output "eventbridge_rule_name" {
  description = "Name of the EventBridge rule triggering daily analysis"
  value       = aws_cloudwatch_event_rule.daily_analysis.name
}

output "eventbridge_rule_arn" {
  description = "ARN of the EventBridge rule"
  value       = aws_cloudwatch_event_rule.daily_analysis.arn
}
