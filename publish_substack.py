#!/usr/bin/env python3
"""
Substack Publisher — Email to Post
────────────────────────────────────
Sends today's digest to your Substack publication's secret import address.
Substack receives the email and creates a draft post (subject → title, body → content).

How to find your import address:
  Substack dashboard → Settings → Import → "Email a post"

Required environment variables:
  SUBSTACK_POST_EMAIL    — your secret Substack address, e.g. abc123@posts.substack.com
  SENDER_EMAIL           — the Gmail (or other) address you're sending from
  SENDER_APP_PASSWORD    — app password for that account (NOT your login password)

Optional:
  SMTP_HOST              — SMTP server (default: smtp.gmail.com)
  SMTP_PORT              — SMTP port  (default: 587)
"""

from __future__ import annotations

import logging
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import markdown

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

OUTPUTS_DIR        = Path("outputs")
SUBSTACK_POST_EMAIL = os.environ.get("SUBSTACK_POST_EMAIL", "").strip()
SENDER_EMAIL        = os.environ.get("SENDER_EMAIL", "").strip()
SENDER_APP_PASSWORD = os.environ.get("SENDER_APP_PASSWORD", "").strip()
SMTP_HOST           = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT           = int(os.environ.get("SMTP_PORT", "587"))

# ── Helpers ───────────────────────────────────────────────────────────────────


def find_todays_digest() -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    path = OUTPUTS_DIR / f"digest-{today}.md"
    if not path.exists():
        raise FileNotFoundError(f"No digest found for today at: {path}")
    return path


def md_to_title_and_html(md_content: str) -> tuple[str, str]:
    """Extract the H1 as the email subject; convert the rest to HTML."""
    lines = md_content.split("\n")
    title = ""
    body_lines = []
    title_consumed = False

    for line in lines:
        if not title_consumed and line.startswith("# "):
            title = line[2:].strip()
            title_consumed = True
            continue
        body_lines.append(line)

    body_md = "\n".join(body_lines).strip()
    md_converter = markdown.Markdown(extensions=["extra", "nl2br"])
    html = md_converter.convert(body_md)

    return title, html


def build_email(title: str, html_body: str) -> MIMEMultipart:
    """Construct a multipart email with HTML body."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = title
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = SUBSTACK_POST_EMAIL
    msg.attach(MIMEText(html_body, "html"))
    return msg


def send_email(msg: MIMEMultipart) -> None:
    """Send the message via SMTP with STARTTLS."""
    log.info(f"Connecting to {SMTP_HOST}:{SMTP_PORT}...")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
        server.sendmail(SENDER_EMAIL, SUBSTACK_POST_EMAIL, msg.as_string())
    log.info("Email sent.")


# ── Entry Point ───────────────────────────────────────────────────────────────


def main() -> None:
    missing = [name for name, val in [
        ("SUBSTACK_POST_EMAIL",  SUBSTACK_POST_EMAIL),
        ("SENDER_EMAIL",         SENDER_EMAIL),
        ("SENDER_APP_PASSWORD",  SENDER_APP_PASSWORD),
    ] if not val]

    if missing:
        log.error(f"Missing required environment variables: {', '.join(missing)}")
        log.error("See README.md for setup instructions.")
        sys.exit(1)

    separator = "═" * 52
    log.info(separator)
    log.info(f"  Substack Publisher — {datetime.now().strftime('%Y-%m-%d')}")
    log.info(separator)

    # 1. Find today's digest
    try:
        digest_path = find_todays_digest()
    except FileNotFoundError as exc:
        log.error(str(exc))
        log.error("Run digest.py first to generate today's digest.")
        sys.exit(1)

    log.info(f"Reading {digest_path}")
    md_content = digest_path.read_text(encoding="utf-8")

    # 2. Convert to HTML
    title, html_body = md_to_title_and_html(md_content)
    log.info(f"Subject: {title}")

    # 3. Build and send
    msg = build_email(title, html_body)
    send_email(msg)

    log.info(separator)
    log.info(f"  Draft queued in Substack → check your dashboard")
    log.info(separator)
    print(f"\nDone. Check your Substack dashboard to review and send the draft.\n")


if __name__ == "__main__":
    main()
