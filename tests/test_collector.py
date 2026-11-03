"""Unit tests: no network. GitHub responses are faked by replacing the collector's _get."""

import datetime as dt
import json

import pytest

from devex_metrics import DORAMetrics, GitHubMetricsCollector, cli, percentile

NOW = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.timezone.utc)


def iso(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGitHub:
    """Maps URL path -> JSON, honouring per_page/page so pagination is exercised."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        data = self.routes[path]
        if params and "page" in params:
            size = params["per_page"]
            start = (params["page"] - 1) * size
            return data[start : start + size]
        return data


def make_collector(routes, monkeypatch):
    c = GitHubMetricsCollector("o", "r", token="t")
    fake = FakeGitHub(routes)
    monkeypatch.setattr(c, "_get", fake)
    return c, fake


def deployments_route(statuses):
    deps = [
        {"id": i, "sha": f"sha{i}", "environment": "production", "created_at": iso(h)}
        for i, (h, _) in enumerate(statuses)
    ]
    routes = {"/repos/o/r/deployments": deps}
    for i, (_, st) in enumerate(statuses):
        routes[f"/repos/o/r/deployments/{i}/statuses"] = [{"state": st}]
    return routes


def test_requires_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ValueError):
        GitHubMetricsCollector("o", "r")


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([10, 20], 95) == pytest.approx(19.5)
    with pytest.raises(ValueError):
        percentile([], 50)


def test_collect_end_to_end(monkeypatch):
    routes = deployments_route([(2, "success"), (5, "failure"), (6, "failure"), (10, "success")])
    routes["/repos/o/r/pulls"] = [
        {
            "number": 1,
            "title": "a",
            "created_at": iso(30),
            "merged_at": iso(3),
            "updated_at": iso(3),
        },
        {
            "number": 2,
            "title": "b",
            "created_at": iso(60),
            "merged_at": iso(8),
            "updated_at": iso(8),
        },
    ]
    routes["/repos/o/r/pulls/1/commits"] = [{"commit": {"committer": {"date": iso(5)}}}]
    routes["/repos/o/r/pulls/2/commits"] = [{"commit": {"committer": {"date": iso(14)}}}]
    c, _ = make_collector(routes, monkeypatch)
    m = c.collect(window_days=1, now=NOW)
    assert m.deployments_total == 4
    assert m.failed_deployments == 2
    assert m.change_failure_rate_pct == 50.0
    # one incident: first failure at 6h ago, restored by the success at 2h ago
    assert m.mttr_hours == pytest.approx(4.0)
    assert m.lead_time_p50_hours == pytest.approx(4.0)  # leads: 2h and 6h
    assert m.pull_requests_analyzed == 2


def test_pagination_stops_at_window(monkeypatch):
    old = [
        {
            "number": n,
            "title": "x",
            "created_at": iso(900),
            "merged_at": iso(900),
            "updated_at": iso(900),
        }
        for n in range(10, 15)
    ]
    recent = [
        {"number": 1, "title": "x", "created_at": iso(5), "merged_at": iso(2), "updated_at": iso(2)}
    ]
    routes = {
        "/repos/o/r/pulls": recent + old,
        "/repos/o/r/pulls/1/commits": [{"commit": {"committer": {"date": iso(4)}}}],
    }
    c, fake = make_collector(routes, monkeypatch)
    prs = c.fetch_merged_prs(NOW - dt.timedelta(days=1))
    assert [p.number for p in prs] == [1]
    assert not any("/commits" in p and "/pulls/1" not in p for p, _ in fake.calls)


def test_zero_lead_time_is_not_reported_as_na():
    m = DORAMetrics(30, 1.0, 0.0, 0.0, 0.0, 0.0, 5, 0, 5)
    assert "N/A" not in m.summary()


@pytest.mark.parametrize(
    "freq,lt,cfr,mttr,level",
    [
        (2, 0.5, 2, 0.5, "Elite"),
        (0.2, 12, 8, 12, "High"),
        (0.05, 100, 12, 100, "Medium"),
        (0.01, 500, 40, 500, "Low"),
    ],
)
def test_dora_level_bands(freq, lt, cfr, mttr, level):
    assert DORAMetrics(30, freq, lt, lt, cfr, mttr, 10, 1, 10).dora_level == level


def test_cli_json_output(monkeypatch, capsys):
    m = DORAMetrics(30, 1.5, 2.0, 4.0, 5.0, 1.0, 45, 2, 12)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(GitHubMetricsCollector, "collect", lambda self, **kw: m)
    assert cli.main(["--owner", "o", "--repo", "r", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["deployments_total"] == 45
    assert out["dora_level"] in {"Elite", "High", "Medium", "Low"}


def test_cli_missing_token_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli.main(["--owner", "o", "--repo", "r"]) == 2
    assert "token" in capsys.readouterr().err.lower()
