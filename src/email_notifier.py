#!/usr/bin/env python3
"""Email notification module.

Sends email notifications with the weekly literature report.
Uses Python standard library only (smtplib + email).
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from github_issue import PaperInfo

logger = logging.getLogger(__name__)


@dataclass
class EmailConfig:
    """Email configuration from environment variables."""

    smtp_server: str
    smtp_port: int
    username: str
    password: str
    recipient: str
    sender: str | None = None
    use_tls: bool = True

    @classmethod
    def from_env(cls) -> EmailConfig | None:
        """Load email config from environment variables.

        Required env vars:
            EMAIL_SMTP_SERVER - SMTP server address (e.g., smtp.gmail.com)
            EMAIL_SMTP_PORT - SMTP port (e.g., 587 for TLS, 465 for SSL)
            EMAIL_USERNAME - SMTP username (usually email address)
            EMAIL_PASSWORD - SMTP password or app-specific password
            EMAIL_RECIPIENT - Recipient email address

        Optional env vars:
            EMAIL_SENDER - Sender display name (defaults to username)
            EMAIL_USE_TLS - Use TLS (default: true)

        Returns None if required variables are missing.
        """
        smtp_server = os.environ.get("EMAIL_SMTP_SERVER", "")
        smtp_port_str = os.environ.get("EMAIL_SMTP_PORT", "587")
        username = os.environ.get("EMAIL_USERNAME", "")
        password = os.environ.get("EMAIL_PASSWORD", "")
        recipient = os.environ.get("EMAIL_RECIPIENT", "")

        if not all([smtp_server, smtp_port_str, username, password, recipient]):
            return None

        try:
            smtp_port = int(smtp_port_str)
        except ValueError:
            logger.error(f"Invalid EMAIL_SMTP_PORT: {smtp_port_str}")
            return None

        sender = os.environ.get("EMAIL_SENDER", username)
        use_tls = os.environ.get("EMAIL_USE_TLS", "true").lower() in (
            "true",
            "1",
            "yes",
        )

        return cls(
            smtp_server=smtp_server,
            smtp_port=smtp_port,
            username=username,
            password=password,
            recipient=recipient,
            sender=sender,
            use_tls=use_tls,
        )


def format_paper_text(paper: PaperInfo, index: int) -> str:
    """Format a single paper for plain text email."""
    authors_str = ", ".join(paper.authors[:3])
    if len(paper.authors) > 3:
        authors_str += " et al."

    tier_str = ", ".join(paper.matched_tiers[:3])
    keywords_str = ", ".join(paper.matched_keywords[:5])

    lines = [
        f"{index}. {paper.title}",
        f"   Authors: {authors_str}",
        f"   Journal: {paper.journal or 'N/A'} ({paper.year or 'N/A'})",
        f"   DOI: {paper.url}",
        f"   Score: {paper.score} ({paper.priority})",
        f"   Tiers: {tier_str}",
        f"   Keywords: {keywords_str}",
        "",
    ]

    return "\n".join(lines)


def format_paper_html(paper: PaperInfo, index: int) -> str:
    """Format a single paper for HTML email."""
    authors_str = ", ".join(paper.authors[:3])
    if len(paper.authors) > 3:
        authors_str += " et al."

    tier_badges = " ".join(
        [
            f'<span style="background:#e3f2fd;padding:2px 6px;border-radius:3px;font-size:12px;">{t}</span>'
            for t in paper.matched_tiers[:3]
        ]
    )
    keywords_str = ", ".join(paper.matched_keywords[:5])

    return f"""
    <div style="margin-bottom:20px;padding:15px;border-left:4px solid {"#d32f2f" if paper.priority == "HIGH" else "#fb8c00" if paper.priority == "MEDIUM" else "#43a047"};background:#fafafa;">
        <h3 style="margin:0 0 10px 0;color:#333;">{index}. {paper.title}</h3>
        <p style="margin:5px 0;color:#666;"><strong>Authors:</strong> {authors_str}</p>
        <p style="margin:5px 0;color:#666;"><strong>Journal:</strong> {paper.journal or "N/A"} ({paper.year or "N/A"})</p>
        <p style="margin:5px 0;"><strong>DOI:</strong> <a href="{paper.url}" style="color:#1976d2;">{paper.doi}</a></p>
        <p style="margin:5px 0;"><strong>Score:</strong> {paper.score} <span style="background:{"#ffcdd2" if paper.priority == "HIGH" else "#ffe0b2" if paper.priority == "MEDIUM" else "#c8e6c9"};padding:2px 8px;border-radius:3px;">{paper.priority}</span></p>
        <p style="margin:5px 0;">{tier_badges}</p>
        <p style="margin:5px 0;color:#888;font-size:13px;"><em>Keywords: {keywords_str}</em></p>
    </div>
    """


def format_email_body(
    papers: list[PaperInfo],
    from_date: str,
    to_date: str,
    total_fetched: int,
    journals_count: int,
) -> tuple[str, str]:
    """Format email body in both plain text and HTML.

    Returns:
        Tuple of (plain_text, html)
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    high_priority = [p for p in papers if p.priority == "HIGH"]
    medium_priority = [p for p in papers if p.priority == "MEDIUM"]
    low_priority = [p for p in papers if p.priority == "LOW"]

    # Plain text version
    text_lines = [
        "Weekly Literature Monitor Report",
        "=" * 40,
        "",
        f"Period: {from_date} to {to_date}",
        f"Generated: {now}",
        "",
        "SUMMARY",
        "-" * 20,
        f"Total papers fetched: {total_fetched}",
        f"Relevant papers found: {len(papers)}",
        f"Journals monitored: {journals_count}",
        "",
        f"HIGH priority: {len(high_priority)}",
        f"MEDIUM priority: {len(medium_priority)}",
        f"LOW priority: {len(low_priority)}",
        "",
    ]

    if high_priority:
        text_lines.append("=" * 40)
        text_lines.append("HIGH PRIORITY PAPERS")
        text_lines.append("=" * 40)
        text_lines.append("")
        for i, paper in enumerate(high_priority, 1):
            text_lines.append(format_paper_text(paper, i))

    if medium_priority:
        text_lines.append("=" * 40)
        text_lines.append("MEDIUM PRIORITY PAPERS")
        text_lines.append("=" * 40)
        text_lines.append("")
        for i, paper in enumerate(medium_priority, 1):
            text_lines.append(format_paper_text(paper, i))

    if low_priority:
        text_lines.append("=" * 40)
        text_lines.append(f"LOW PRIORITY PAPERS ({len(low_priority)} total)")
        text_lines.append("=" * 40)
        text_lines.append("")
        for i, paper in enumerate(low_priority[:10], 1):
            text_lines.append(format_paper_text(paper, i))
        if len(low_priority) > 10:
            text_lines.append(
                f"... and {len(low_priority) - 10} more LOW priority papers"
            )

    plain_text = "\n".join(text_lines)

    # HTML version
    html_papers = ""

    if high_priority:
        html_papers += '<h2 style="color:#d32f2f;border-bottom:2px solid #d32f2f;padding-bottom:5px;">HIGH Priority Papers</h2>'
        for i, paper in enumerate(high_priority, 1):
            html_papers += format_paper_html(paper, i)

    if medium_priority:
        html_papers += '<h2 style="color:#fb8c00;border-bottom:2px solid #fb8c00;padding-bottom:5px;">MEDIUM Priority Papers</h2>'
        for i, paper in enumerate(medium_priority, 1):
            html_papers += format_paper_html(paper, i)

    if low_priority:
        html_papers += f'<h2 style="color:#43a047;border-bottom:2px solid #43a047;padding-bottom:5px;">LOW Priority Papers ({len(low_priority)} total)</h2>'
        for i, paper in enumerate(low_priority[:10], 1):
            html_papers += format_paper_html(paper, i)
        if len(low_priority) > 10:
            html_papers += f'<p style="color:#888;font-style:italic;">... and {len(low_priority) - 10} more LOW priority papers</p>'

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto;padding:20px;background:#f5f5f5;">
        <div style="background:white;padding:30px;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,0.1);">
            <h1 style="color:#1976d2;margin-top:0;">Weekly Literature Monitor Report</h1>

            <div style="background:#e3f2fd;padding:15px;border-radius:5px;margin-bottom:20px;">
                <p style="margin:5px 0;"><strong>Period:</strong> {from_date} to {to_date}</p>
                <p style="margin:5px 0;"><strong>Generated:</strong> {now}</p>
            </div>

            <h2 style="color:#333;">Summary</h2>
            <table style="border-collapse:collapse;width:100%;margin-bottom:20px;">
                <tr style="background:#f5f5f5;">
                    <td style="padding:10px;border:1px solid #ddd;">Total papers fetched</td>
                    <td style="padding:10px;border:1px solid #ddd;text-align:right;"><strong>{total_fetched}</strong></td>
                </tr>
                <tr>
                    <td style="padding:10px;border:1px solid #ddd;">Relevant papers found</td>
                    <td style="padding:10px;border:1px solid #ddd;text-align:right;"><strong>{len(papers)}</strong></td>
                </tr>
                <tr style="background:#f5f5f5;">
                    <td style="padding:10px;border:1px solid #ddd;">Journals monitored</td>
                    <td style="padding:10px;border:1px solid #ddd;text-align:right;"><strong>{journals_count}</strong></td>
                </tr>
            </table>

            <table style="border-collapse:collapse;width:100%;margin-bottom:30px;">
                <tr>
                    <td style="padding:10px;background:#ffcdd2;border:1px solid #ddd;text-align:center;"><strong>HIGH</strong><br>{len(high_priority)}</td>
                    <td style="padding:10px;background:#ffe0b2;border:1px solid #ddd;text-align:center;"><strong>MEDIUM</strong><br>{len(medium_priority)}</td>
                    <td style="padding:10px;background:#c8e6c9;border:1px solid #ddd;text-align:center;"><strong>LOW</strong><br>{len(low_priority)}</td>
                </tr>
            </table>

            {html_papers}

            <hr style="border:none;border-top:1px solid #ddd;margin:30px 0;">
            <p style="color:#888;font-size:12px;text-align:center;">
                Generated by <a href="https://github.com/yunKKang/weekly-literature-monitor" style="color:#1976d2;">Weekly Literature Monitor</a>
            </p>
        </div>
    </body>
    </html>
    """

    return plain_text, html


def send_email(
    config: EmailConfig,
    subject: str,
    plain_text: str,
    html: str,
) -> bool:
    """Send an email with both plain text and HTML content.

    Args:
        config: Email configuration
        subject: Email subject line
        plain_text: Plain text version of the email
        html: HTML version of the email

    Returns:
        True if email sent successfully, False otherwise
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.sender or config.username
    msg["To"] = config.recipient

    part1 = MIMEText(plain_text, "plain", "utf-8")
    part2 = MIMEText(html, "html", "utf-8")

    msg.attach(part1)
    msg.attach(part2)

    try:
        if config.smtp_port == 465:
            # SSL connection
            with smtplib.SMTP_SSL(
                config.smtp_server, config.smtp_port, timeout=30
            ) as server:
                server.login(config.username, config.password)
                server.sendmail(config.username, config.recipient, msg.as_string())
        else:
            # TLS connection (typically port 587)
            with smtplib.SMTP(
                config.smtp_server, config.smtp_port, timeout=30
            ) as server:
                if config.use_tls:
                    server.starttls()
                server.login(config.username, config.password)
                server.sendmail(config.username, config.recipient, msg.as_string())

        logger.info(f"Email sent successfully to {config.recipient}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP authentication failed: {e}")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def notify_by_email(
    papers: list[PaperInfo],
    from_date: str,
    to_date: str,
    total_fetched: int,
    journals_count: int,
) -> bool:
    """Send email notification with the weekly report.

    Reads configuration from environment variables.
    If email is not configured, logs a message and returns True (non-blocking).

    Args:
        papers: List of relevant papers
        from_date: Start date of the report period
        to_date: End date of the report period
        total_fetched: Total number of papers fetched
        journals_count: Number of journals monitored

    Returns:
        True if email sent successfully or email not configured
        False if email configured but sending failed
    """
    config = EmailConfig.from_env()

    if config is None:
        logger.info("Email notification not configured (missing environment variables)")
        return True  # Not an error, just not configured

    if not papers:
        logger.info("No relevant papers to email.")
        return True

    high_count = len([p for p in papers if p.priority == "HIGH"])

    if high_count > 0:
        subject = f"[Weekly Literature] {len(papers)} papers, {high_count} HIGH priority ({from_date} to {to_date})"
    else:
        subject = f"[Weekly Literature] {len(papers)} relevant papers ({from_date} to {to_date})"

    plain_text, html = format_email_body(
        papers=papers,
        from_date=from_date,
        to_date=to_date,
        total_fetched=total_fetched,
        journals_count=journals_count,
    )

    return send_email(config, subject, plain_text, html)
