# devex-metrics

DORA metrics collector for engineering organizations. Measures deployment frequency, lead time for changes, change failure rate, and MTTR using the GitHub API — the four metrics that predict elite software delivery performance.

Built to answer: *are we actually getting faster and more reliable, or does it just feel that way?*

## Metrics collected

| Metric | Description | Elite benchmark |
|--------|-------------|----------------|
| Deployment Frequency | How often you deploy to production | Multiple times/day |
| Lead Time for Changes | Commit → production (p50, p95) | < 1 hour |
| Change Failure Rate | % of deploys causing an incident | < 5% |
| MTTR | Mean time to restore after failure | < 1 hour |

## Install

```bash
pip install -e .
```

## Usage

```bash
# Set your GitHub token
export GITHUB_TOKEN=ghp_...

# Collect metrics for a repo
devex-metrics --owner zillow --repo middleware-platform --window 30

# JSON output for dashboarding
devex-metrics --owner zillow --repo middleware-platform --format json
```

## Python API

```python
from devex_metrics.collector import GitHubMetricsCollector

collector = GitHubMetricsCollector(owner="zillow", repo="middleware-platform")
metrics = collector.collect(window_days=30)

print(metrics.summary())
print(f"DORA level: {metrics.dora_level}")  # Elite / High / Medium / Low
```

## GitHub Actions

The included workflow runs daily at 9am UTC and uploads a `metrics.json` artifact:

```yaml
# .github/workflows/daily_report.yml already configured
# Just add it to any repo and set GITHUB_TOKEN (automatic in Actions)
```

You can also trigger it manually with a custom lookback window from the Actions tab.

## DORA performance levels

Based on the [2023 DORA State of DevOps Report](https://dora.dev/research/2023/dora-report/) benchmarks.

```
Elite:  Deploy multiple times/day, lead time < 1h, CFR < 5%, MTTR < 1h
High:   Deploy 1/week–1/day, lead time < 1d, CFR < 10%, MTTR < 24h  
Medium: Deploy 1/month–1/week, lead time < 1w, CFR < 15%, MTTR < 1w
Low:    Deploy < monthly, longer lead times and recovery
```
