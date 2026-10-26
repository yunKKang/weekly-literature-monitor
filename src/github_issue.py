#!/usr/bin/env python3
"""GitHub Issue notification module.

Creates GitHub Issues with formatted lists of new relevant papers.
Uses the GitHub REST API via environment variables for authentication.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config import config
from paper_utils import fetch_url, normalize_doi

logger = logging.getLogger(__name__)


def list_report_issues(repo: str, token: str) -> list[dict[str, Any]]:
    """Read all report Issues, including closed ones; fail closed on read errors."""
    issues = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/repos/{repo}/issues"
            f"?state=all&labels=literature-monitor&per_page=100&page={page}"
        )
        status, body = fetch_url(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if status != 200:
            raise RuntimeError(f"Cannot reconcile report Issues: HTTP {status}")
        batch = json.loads(body)
        if not isinstance(batch, list):
            raise ValueError("Unexpected GitHub Issues response")
        issues.extend(
            issue
            for issue in batch
            if not issue.get("pull_request")
            and issue.get("title", "").startswith("[Weekly Report]")
        )
        if len(batch) < 100:
            return issues
        page += 1


def reported_dois(issues: list[dict[str, Any]]) -> set[str]:
    """Read DOI links in legacy reports and complete DOI manifests in new reports."""
    dois = set()
    for issue in issues:
        body = issue.get("body") or ""
        values = re.findall(r"\*\*DOI:\*\*\s*\[([^\]]+)\]", body)
        for manifest in re.findall(r"<!-- literature-monitor-dois: (.*?) -->", body):
            values.extend(json.loads(manifest))
        for value in values:
            info = normalize_doi(value)
            if info:
                dois.add(info.full)
    return dois


@dataclass
class PaperInfo:
    doi: str
    title: str
    authors: list[str]
    journal: str | None
    year: str | None
    score: int
    priority: str
    matched_tiers: list[str]
    matched_keywords: list[str]
    url: str
    ai_status: str = ""
    ai_reason: str = ""
    ai_model: str = ""


def format_paper_markdown(paper: PaperInfo, index: int) -> str:
    authors_str = ", ".join(paper.authors[:3])
    if len(paper.authors) > 3:
        authors_str += " et al."

    tier_badges = " ".join(
        [
            f"`{t.replace('tier', 'T').replace('_', ' ').title()}`"
            for t in paper.matched_tiers[:3]
        ]
    )
    keywords_str = ", ".join(paper.matched_keywords[:5])

    lines = [
        f"### {index}. {paper.title}",
        "",
        f"**Authors:** {authors_str}",
        f"**Journal:** {paper.journal or 'N/A'} ({paper.year or 'N/A'})",
        f"**DOI:** [{paper.doi}]({paper.url})",
        f"**Relevance Score:** {paper.score} ({paper.priority})",
        f"**Matched Tiers:** {tier_badges}",
        f"**Keywords:** {keywords_str}",
        "",
    ]
    if paper.ai_status:
        lines.insert(
            -1,
            f"**AI review:** {paper.ai_status} ({paper.ai_model}) — {paper.ai_reason}",
        )

    return "\n".join(lines)


def format_issue_body(
    papers: list[PaperInfo],
    from_date: str,
    to_date: str,
    total_fetched: int,
    journals_count: int,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    high_priority = [p for p in papers if p.priority == "HIGH"]
    medium_priority = [p for p in papers if p.priority == "MEDIUM"]
    low_priority = [p for p in papers if p.priority == "LOW"]

    lines = [
        f"# Weekly Literature Monitor Report",
        "",
        f"**Period:** {from_date} to {to_date}",
        f"**Generated:** {now}",
        "",
        "## Summary",
        "",
        f"- **Total papers fetched:** {total_fetched}",
        f"- **Relevant papers found:** {len(papers)}",
        f"- **Journals monitored:** {journals_count}",
        "",
        "| Priority | Count |",
        "|----------|-------|",
        f"| HIGH | {len(high_priority)} |",
        f"| MEDIUM | {len(medium_priority)} |",
        f"| LOW | {len(low_priority)} |",
        "",
    ]

    if high_priority:
        lines.append("---")
        lines.append("")
        lines.append("## HIGH Priority Papers")
        lines.append("")
        for i, paper in enumerate(high_priority, 1):
            lines.append(format_paper_markdown(paper, i))

    if medium_priority:
        lines.append("---")
        lines.append("")
        lines.append("## MEDIUM Priority Papers")
        lines.append("")
        for i, paper in enumerate(medium_priority, 1):
            lines.append(format_paper_markdown(paper, i))

    if low_priority and len(low_priority) <= 20:
        lines.append("---")
        lines.append("")
        lines.append("## LOW Priority Papers")
        lines.append("")
        for i, paper in enumerate(low_priority, 1):
            lines.append(format_paper_markdown(paper, i))
    elif low_priority:
        lines.append("---")
        lines.append("")
        lines.append(
            f"## LOW Priority Papers ({len(low_priority)} total, showing top 20)"
        )
        lines.append("")
        for i, paper in enumerate(low_priority[:20], 1):
            lines.append(format_paper_markdown(paper, i))

    lines.append("---")
    lines.append("")
    source = "Weekly Literature Monitor"
    if config.GITHUB_REPOSITORY:
        source = f"[{source}](https://github.com/{config.GITHUB_REPOSITORY})"
    lines.append(f"*Generated by {source}*")

    return "\n".join(lines)


def create_github_issue(
    title: str,
    body: str,
    labels: list[str] | None = None,
    repo: str | None = None,
    token: str | None = None,
) -> dict[str, Any] | None:
    if token is None:
        token = config.GITHUB_TOKEN
    if repo is None:
        repo = config.GITHUB_REPOSITORY

    if not token or not repo:
        logger.error(
            "GITHUB_TOKEN and GITHUB_REPOSITORY environment variables required"
        )
        return None

    url = f"https://api.github.com/repos/{repo}/issues"

    payload: dict[str, Any] = {
        "title": title,
        "body": body,
    }
    if labels:
        payload["labels"] = labels

    data = json.dumps(payload).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    # POST may succeed even when the response is lost or is 5xx. Never retry
    # this non-idempotent request; the next run reconciles published Issues.
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result if isinstance(result, dict) and result.get("number") else None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode(errors="replace") if hasattr(e, "read") else ""
        logger.error("GitHub API error: %s - %s", e.code, error_body)
    except (urllib.error.URLError, OSError, ValueError) as e:
        logger.error("Issue creation outcome unknown; reconcile before retry: %s", e)
    return None


def notify_new_papers(
    papers: list[PaperInfo],
    from_date: str,
    to_date: str,
    total_fetched: int,
    journals_count: int,
) -> bool:
    if not papers:
        logger.info("No relevant papers to report.")
        return True

    if not config.GITHUB_REPOSITORY or not config.GITHUB_TOKEN:
        logger.error("GitHub credentials required to reconcile reports")
        return False
    try:
        already_reported = reported_dois(
            list_report_issues(config.GITHUB_REPOSITORY, config.GITHUB_TOKEN)
        )
    except (RuntimeError, ValueError, TypeError) as exc:
        logger.error("Cannot verify published DOIs; leaving state unchanged: %s", exc)
        return False

    unique = {}
    for paper in papers:
        info = normalize_doi(paper.doi)
        if info and info.full not in already_reported:
            unique.setdefault(info.full, paper)
    papers = list(unique.values())
    if not papers:
        logger.info("All relevant papers already exist in report Issues")
        return True

    # Stay below GitHub's Issue body limit, including the complete DOI manifest.
    def body_for(batch):
        manifest = sorted(normalize_doi(p.doi).full for p in batch)
        return (
            format_issue_body(batch, from_date, to_date, total_fetched, journals_count)
            + "\n\n<!-- literature-monitor-dois: "
            + json.dumps(manifest)
            + " -->"
        )

    batches, batch = [], []
    for paper in papers:
        proposed = batch + [paper]
        if batch and len(body_for(proposed)) > 50000:
            batches.append(batch)
            batch = [paper]
        else:
            batch = proposed
    if batch:
        batches.append(batch)
    for number, batch in enumerate(batches, 1):
        high_count = sum(p.priority == "HIGH" for p in batch)
        title = f"[Weekly Report] {len(batch)} papers, {high_count} HIGH priority ({from_date} to {to_date})"
        if len(batches) > 1:
            title += f" — part {number}/{len(batches)}"
        labels = ["literature-monitor", "automated"]
        if high_count:
            labels.append("high-priority")
        body = body_for(batch)
        if len(body) > 65500:
            logger.error(
                "A single paper exceeds the Issue body limit; leaving checkpoint unchanged"
            )
            return False
        result = create_github_issue(title, body, labels)
        if not result:
            logger.error(
                "Report part %d failed; reconcile published parts before resuming",
                number,
            )
            return False
        logger.info("Created GitHub Issue: %s", result.get("html_url"))
    return True
