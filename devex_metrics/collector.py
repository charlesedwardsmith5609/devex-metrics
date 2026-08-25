"""
DORA metrics collector using the GitHub API.

Measures the four DORA (DevOps Research and Assessment) metrics:
  1. Deployment Frequency   — how often you deploy to production
  2. Lead Time for Changes  — time from commit to production
  3. Change Failure Rate    — % of deployments causing a production failure
  4. Time to Restore (MTTR) — how long to recover from a failure

Reference: https://dora.dev/guides/dora-metrics-four-keys/
"""

from __future__ import annotations
import os
import statistics
import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import urllib.request
import urllib.parse
import json


@dataclass
class Deployment:
    sha: str
    deployed_at: datetime.datetime
    environment: str
    status: str          # success / failure / in_progress


@dataclass
class PullRequest:
    number: int
    title: str
    created_at: datetime.datetime
    merged_at: Optional[datetime.datetime]
    first_commit_at: Optional[datetime.datetime] = None

    @property
    def lead_time_hours(self) -> Optional[float]:
        if self.merged_at and self.first_commit_at:
            return (self.merged_at - self.first_commit_at).total_seconds() / 3600
        return None


@dataclass
class DORAMetrics:
    """Computed DORA metrics for a time window."""
    window_days: int
    deployment_frequency_per_day: float
    lead_time_p50_hours: Optional[float]
    lead_time_p95_hours: Optional[float]
    change_failure_rate_pct: float
    mttr_hours: Optional[float]
    deployments_total: int
    failed_deployments: int
    pull_requests_analyzed: int
    generated_at: datetime.datetime = field(
        default_factory=datetime.datetime.utcnow
    )

    @property
    def dora_level(self) -> str:
        """
        Classify performance level per DORA research benchmarks.
        Returns: Elite / High / Medium / Low
        """
        score = 0
        # Deployment frequency
        if self.deployment_frequency_per_day >= 1:        score += 3  # Elite: multiple/day
        elif self.deployment_frequency_per_day >= 1/7:    score += 2  # High: 1/week–1/day
        elif self.deployment_frequency_per_day >= 1/30:   score += 1  # Medium: 1/month–1/week
        # Lead time
        if self.lead_time_p50_hours is not None:
            if self.lead_time_p50_hours < 1:              score += 3  # Elite: < 1h
            elif self.lead_time_p50_hours < 24:           score += 2  # High: < 1d
            elif self.lead_time_p50_hours < 168:          score += 1  # Medium: < 1w
        # Change failure rate
        if self.change_failure_rate_pct < 5:              score += 3
        elif self.change_failure_rate_pct < 10:           score += 2
        elif self.change_failure_rate_pct < 15:           score += 1
        # MTTR
        if self.mttr_hours is not None:
            if self.mttr_hours < 1:                       score += 3
            elif self.mttr_hours < 24:                    score += 2
            elif self.mttr_hours < 168:                   score += 1

        if score >= 10:   return "Elite"
        elif score >= 7:  return "High"
        elif score >= 4:  return "Medium"
        else:             return "Low"

    def summary(self) -> str:
        lt50  = f"{self.lead_time_p50_hours:.1f}h" if self.lead_time_p50_hours else "N/A"
        lt95  = f"{self.lead_time_p95_hours:.1f}h" if self.lead_time_p95_hours else "N/A"
        mttr  = f"{self.mttr_hours:.1f}h" if self.mttr_hours else "N/A"
        return (
            f"DORA Metrics ({self.window_days}d window) — {self.dora_level.upper()} performer\n"
            f"{'─' * 50}\n"
            f"Deployment Frequency:  {self.deployment_frequency_per_day:.2f}/day "
            f"({self.deployments_total} total)\n"
            f"Lead Time (p50/p95):   {lt50} / {lt95}\n"
            f"Change Failure Rate:   {self.change_failure_rate_pct:.1f}%  "
            f"({self.failed_deployments}/{self.deployments_total} deploys)\n"
            f"MTTR:                  {mttr}\n"
            f"PRs analyzed:          {self.pull_requests_analyzed}\n"
        )


class GitHubMetricsCollector:
    """
    Collect DORA metrics from the GitHub API.

    Requires a GitHub token with repo/workflow read permissions.
    Set via GITHUB_TOKEN env var or pass directly.

    Args:
        owner:   GitHub org or user (e.g. "zillow")
        repo:    Repository name (e.g. "middleware-platform")
        token:   GitHub personal access token (falls back to GITHUB_TOKEN env)
    """

    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        owner: str,
        repo: str,
        token: Optional[str] = None,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        if not self.token:
            raise ValueError(
                "GitHub token required. Set GITHUB_TOKEN env var or pass token= argument."
            )

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.BASE_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def _paginate(self, path: str, params: Optional[Dict] = None) -> List[Any]:
        results = []
        page = 1
        while True:
            p = {**(params or {}), "per_page": 100, "page": page}
            data = self._get(path, p)
            if not data:
                break
            results.extend(data)
            if len(data) < 100:
                break
            page += 1
        return results

    def _parse_dt(self, s: Optional[str]) -> Optional[datetime.datetime]:
        if not s:
            return None
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))

    def fetch_deployments(
        self,
        since: datetime.datetime,
        environment: str = "production",
    ) -> List[Deployment]:
        """Fetch GitHub Deployments API records since `since`."""
        raw = self._paginate(
            f"/repos/{self.owner}/{self.repo}/deployments",
            {"environment": environment},
        )
        deployments = []
        for d in raw:
            created = self._parse_dt(d.get("created_at"))
            if created and created >= since:
                # Fetch latest status
                statuses = self._get(
                    f"/repos/{self.owner}/{self.repo}/deployments/{d['id']}/statuses"
                )
                status = statuses[0]["state"] if statuses else "unknown"
                deployments.append(Deployment(
                    sha=d["sha"],
                    deployed_at=created,
                    environment=d.get("environment", ""),
                    status=status,
                ))
        return deployments

    def fetch_merged_prs(self, since: datetime.datetime) -> List[PullRequest]:
        """Fetch merged pull requests since `since`."""
        raw = self._paginate(
            f"/repos/{self.owner}/{self.repo}/pulls",
            {"state": "closed", "sort": "updated", "direction": "desc"},
        )
        prs = []
        for pr in raw:
            merged_at = self._parse_dt(pr.get("merged_at"))
            if not merged_at or merged_at < since:
                continue
            # Fetch first commit in PR to compute lead time
            commits = self._get(
                f"/repos/{self.owner}/{self.repo}/pulls/{pr['number']}/commits"
            )
            first_commit_at = None
            if commits:
                first_commit_at = self._parse_dt(
                    commits[0].get("commit", {}).get("committer", {}).get("date")
                )
            prs.append(PullRequest(
                number=pr["number"],
                title=pr["title"],
                created_at=self._parse_dt(pr["created_at"]),
                merged_at=merged_at,
                first_commit_at=first_commit_at,
            ))
        return prs

    def collect(self, window_days: int = 30) -> DORAMetrics:
        """
        Collect and compute DORA metrics for the past `window_days` days.
        """
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=window_days)

        deployments = self.fetch_deployments(since=since)
        prs = self.fetch_merged_prs(since=since)

        # Deployment frequency
        deploy_freq = len(deployments) / window_days if window_days > 0 else 0

        # Lead times
        lead_times = [pr.lead_time_hours for pr in prs if pr.lead_time_hours is not None]
        lt_p50 = statistics.median(lead_times) if lead_times else None
        lt_p95 = (
            sorted(lead_times)[int(len(lead_times) * 0.95)] if len(lead_times) >= 2 else None
        )

        # Change failure rate
        failed = [d for d in deployments if d.status in ("failure", "error")]
        cfr = (len(failed) / len(deployments) * 100) if deployments else 0.0

        # MTTR: simplified — average time between a failure and the next success
        mttr_samples = []
        sorted_deps = sorted(deployments, key=lambda d: d.deployed_at)
        i = 0
        while i < len(sorted_deps):
            if sorted_deps[i].status in ("failure", "error"):
                fail_time = sorted_deps[i].deployed_at
                for j in range(i + 1, len(sorted_deps)):
                    if sorted_deps[j].status == "success":
                        restore_hours = (
                            sorted_deps[j].deployed_at - fail_time
                        ).total_seconds() / 3600
                        mttr_samples.append(restore_hours)
                        break
            i += 1
        mttr = statistics.mean(mttr_samples) if mttr_samples else None

        return DORAMetrics(
            window_days=window_days,
            deployment_frequency_per_day=deploy_freq,
            lead_time_p50_hours=lt_p50,
            lead_time_p95_hours=lt_p95,
            change_failure_rate_pct=cfr,
            mttr_hours=mttr,
            deployments_total=len(deployments),
            failed_deployments=len(failed),
            pull_requests_analyzed=len(prs),
        )
