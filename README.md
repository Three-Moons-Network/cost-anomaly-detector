# Cost Anomaly Detector

Production-ready system for monitoring AWS costs in real time. Detects cost anomalies automatically, analyzes trends with Claude, and sends alerts via email. Ships with a REST API for querying historical costs and trends.

Built as a reference implementation by [Three Moons Network](https://threemoonsnetwork.net) — an AI consulting practice helping small businesses optimize cloud infrastructure.

## Architecture

```
  ┌─────────────────────────────────────────────────────────────────────┐
  │                            AWS Cloud                                 │
  │                                                                      │
  │  EventBridge                                                         │
  │  (Daily @ 8 AM UTC)                                                  │
  │         │                                                            │
  │         ▼                                                            │
  │    ┌─────────────────────────────────────────────────────┐          │
  │    │      Analyzer Lambda (Python + Claude API)          │          │
  │    │  ┌──────────────────────────────────────────────┐   │          │
  │    │  │ 1. Fetch daily costs from Cost Explorer      │   │          │
  │    │  │ 2. Query 30-day baseline from DynamoDB       │   │          │
  │    │  │ 3. Calculate variance % anomaly detection    │   │          │
  │    │  │ 4. Use Claude to analyze & recommend         │   │          │
  │    │  │ 5. Store snapshot & baseline in DynamoDB     │   │          │
  │    │  │ 6. Send SNS alert if anomaly > threshold     │   │          │
  │    │  └──────────────────────────────────────────────┘   │          │
  │    └─────────────────────────────────────────────────────┘          │
  │         │                                                 │           │
  │         ▼                                                 ▼           │
  │    DynamoDB Table                                   SNS Topic        │
  │  (Cost Snapshots,                                  (Alerts via      │
  │   Baselines,                                        Email)           │
  │   Trend Data)                                       │                │
  │         ▲                                           ▼                │
  │         │                                    Email Subscribers      │
  │         │                                                            │
  │         └─────────────────────────────────┐                         │
  │                                            │                         │
  │                                   ┌────────────────────┐            │
  │                       API Gateway │  Query Lambda      │            │
  │                       (GET /costs) │  Returns:          │            │
  │                                   │  - Snapshots       │            │
  │          ◀─────────────────────────  - Baselines       │            │
  │    Browser / Dashboard             │  - Trend metrics  │            │
  │                                   └────────────────────┘            │
  │                                            │                         │
  │                                            ▼                         │
  │                                    DynamoDB (read)                  │
  └─────────────────────────────────────────────────────────────────────┘
```

## What It Does

**Automated cost monitoring:**
- EventBridge triggers the analyzer Lambda daily at 8 AM UTC
- Fetches daily AWS costs from Cost Explorer API, broken down by service
- Compares against a 30-day rolling average baseline
- Calculates variance percentage and detects anomalies (configurable threshold, default 20%)
- Uses Claude (Anthropic API) to generate natural-language analysis: "What caused this spike? Which services are the biggest spenders? What should we optimize?"
- Stores daily snapshots and baseline metrics in DynamoDB
- Sends email alerts via SNS when anomalies exceed the threshold

**Cost querying:**
- HTTP API endpoint (`GET /costs`) for querying historical data
- Supports date range queries (1-90 days)
- Returns snapshots, baselines, and trend analysis
- Useful for building dashboards or integrating with other systems

## Quick Start

### Prerequisites

- AWS account with CLI configured
- Terraform >= 1.5
- Python 3.11+
- Anthropic API key ([console.anthropic.com](https://console.anthropic.com))

### 1. Clone and configure

```bash
git clone git@github.com:Three-Moons-Network/cost-anomaly-detector.git
cd cost-anomaly-detector
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
# Edit terraform.tfvars with your API key and email
```

### 2. Build Lambda packages

```bash
./scripts/deploy.sh
```

### 3. Deploy

```bash
cd terraform
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

### 4. Subscribe to alerts (first time only)

AWS sends a confirmation email to the address you specified in `alert_email`. Click the link to enable email notifications.

### 5. Test the query endpoint

```bash
API_URL=$(terraform output -raw query_url)

# Get last 7 days of cost data
curl "$API_URL?days=7" | jq .

# Example response:
# {
#   "period_days": 7,
#   "snapshots": [
#     {"date": "2026-03-25", "total_cost": 950.00, "by_service": {...}},
#     {"date": "2026-03-26", "total_cost": 1020.00, "by_service": {...}},
#     ...
#   ],
#   "baselines": {...},
#   "trend": {"trend_pct": 3.2, "min": 900.0, "max": 1100.0, "avg": 1050.0},
#   "generated_at": "2026-04-01T08:30:00"
# }
```

### 6. Tear down

```bash
terraform destroy
```

## Project Structure

```
├── src/
│   ├── analyzer.py           # EventBridge-triggered handler
│   │                         # Fetches costs, detects anomalies, invokes Claude
│   └── query.py              # API Gateway handler for cost queries
├── tests/
│   ├── test_analyzer.py      # Unit tests with mocked AWS/Anthropic
│   └── test_query.py         # Unit tests for query handler
├── terraform/
│   ├── main.tf               # All infra: Lambdas, DynamoDB, EventBridge, SNS, API GW, IAM
│   ├── outputs.tf            # Endpoints and resource ARNs
│   ├── backend.tf            # Remote state config (commented for local use)
│   └── terraform.tfvars.example
├── scripts/
│   └── deploy.sh             # Build Lambda packages
├── .github/workflows/
│   └── ci.yml                # Test, lint, TF validate, package
├── requirements.txt          # Runtime: anthropic, boto3
├── requirements-dev.txt      # Dev: pytest, ruff, moto
└── README.md
```

## Infrastructure Details

| Resource | Purpose | Notes |
|----------|---------|-------|
| Lambda: analyzer | Daily cost analysis triggered by EventBridge | 256MB / 60s timeout |
| Lambda: query | HTTP API handler for cost queries | 256MB / 30s timeout |
| EventBridge rule | Cron trigger (daily 8 AM UTC) | Adjustable schedule |
| DynamoDB table | Stores cost snapshots and baselines | PAY_PER_REQUEST billing |
| API Gateway HTTP API | REST endpoint for cost queries | CORS enabled, throttled (10 req/s, 20 burst) |
| SNS topic | Sends cost anomaly alerts via email | KMS encrypted |
| SSM Parameter Store | Stores Anthropic API key and threshold | SecureString encryption |
| CloudWatch Log Groups | Logs for both Lambdas and API Gateway | 30-day retention (configurable) |
| CloudWatch Alarms | Monitor Lambda errors and duration, DynamoDB throttling | Proactive failure detection |
| IAM roles + policies | Least-privilege access | Separate roles for analyzer and query |

All resources are tagged with `Project`, `Environment`, `ManagedBy`, and `Owner` for cost tracking and governance.

## Configuration

Edit `terraform/terraform.tfvars` to customize:

```hcl
environment          = "dev"              # dev, uat, or prod
anthropic_api_key    = "sk-ant-..."       # Your Anthropic API key
alert_email          = "alerts@example.com"  # Email for cost anomalies
anomaly_threshold_pct = 20                # Variance % to trigger alert
baseline_days        = 30                 # Rolling window for baseline
lambda_memory        = 256                # MB
lambda_timeout       = 60                 # seconds
log_retention_days   = 30                 # CloudWatch retention
```

## CI/CD

GitHub Actions runs on every push/PR to `main`:

- **Test** — `pytest` with mocked AWS and Anthropic APIs (no credentials needed)
- **Lint** — `ruff format --check` + `ruff check`
- **Terraform Validate** — `fmt -check`, `init -backend=false`, `validate`
- **Package** — Builds `analyzer.zip` and `query.zip` artifacts on main branch merges

## Customization

**Change the analysis schedule:**

Update the EventBridge cron expression in `main.tf`:

```hcl
schedule_expression = "cron(0 18 * * ? *)"  # 6 PM UTC instead of 8 AM
```

**Change the anomaly threshold:**

```bash
terraform plan -var="anomaly_threshold_pct=25" -out=tfplan
```

Or edit `terraform/terraform.tfvars`.

**Extend the baseline window:**

```bash
terraform plan -var="baseline_days=60" -out=tfplan
```

This calculates the rolling average over 60 days instead of 30.

**Switch Claude models:**

```bash
terraform plan -var="anthropic_model=claude-opus-4-20250514" -out=tfplan
```

**Add custom analysis logic:**

Edit `src/analyzer.py` → `analyze_with_claude()` to modify the system prompt or user message. For example, add service-specific cost optimization rules or multi-cloud comparisons.

## Example Alert Output

When an anomaly is detected, you receive an email like:

```
Subject: AWS Cost Anomaly Alert - 2026-04-01

Cost Anomaly Detected!

Date: 2026-04-01
Current Total Cost: $1,245.67
Baseline (30-day avg): $1,000.00
Variance: 24.6%

Analysis:
Your AWS costs spiked by 24.6% on 2026-04-01 compared to the 30-day average.
The primary driver is Amazon Elastic Compute Cloud (EC2), accounting for $520.00
(+$75 vs baseline). This could be due to:
1. Increased traffic driving more instance usage
2. New deployments or instances that weren't properly scaled down
3. Temporary batch jobs or data processing tasks

Recommendations:
- Review recent EC2 instance launches and ensure they are properly tagged
- Consider using Savings Plans or Reserved Instances for predictable workloads
- Implement auto-scaling policies to terminate idle instances
```

## Cost Estimate

For typical small-business usage (< 1 AWS account, < 20 services):

| Component | Estimated Monthly Cost |
|-----------|----------------------|
| Lambda | ~$0.10 (1 analyzer run/day = ~30 invocations/month, < 1 sec each) |
| API Gateway | ~$0.35 (assuming < 10k queries/month) |
| DynamoDB | ~$0.50 (on-demand, ~50 items stored) |
| CloudWatch | ~$1.00 (logs, metrics, alarms) |
| SNS | ~$0.50 (email alerts) |
| **AWS Infrastructure Total** | **~$2.50/month** |
| **Anthropic API** | **Variable** (depends on analysis frequency and model) |

For context: Claude Sonnet costs roughly $3 per 1M input tokens and $15 per 1M output tokens. A typical daily cost analysis uses ~200 input tokens and ~300 output tokens, so ~$0.01 per analysis.

**Total estimated cost: ~$2.50 - $5.00 per month for infrastructure + API calls.**

## Local Development

```bash
# Set up
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/
ruff format src/ tests/

# Test analyzer locally (requires ANTHROPIC_API_KEY set)
export ANTHROPIC_API_KEY="sk-ant-..."
python -c "
from src.analyzer import fetch_daily_costs, calculate_baseline, analyze_with_claude
import json

# This would fail in dev without real AWS credentials, but shows the flow
# In tests, we mock these calls
"
```

## Monitoring

Use CloudWatch dashboards to track:

- **Analyzer Lambda**: Invocation count, duration, errors, throttles
- **Query Lambda**: API request count, latency, error rate
- **DynamoDB**: Read/write capacity, throttling events
- **Cost data**: Trend graphs, anomaly frequency

Example dashboard JSON can be generated via Terraform:

```bash
terraform show | grep -A 20 "cloudwatch_dashboard"
```

## License

MIT

## Author

Charles Harvey ([linuxlsr](https://github.com/linuxlsr)) — [Three Moons Network LLC](https://threemoonsnetwork.net)
