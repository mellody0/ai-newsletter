#!/usr/bin/env python3
"""
Google Docs Publisher for AI & Tech News Digest
────────────────────────────────────────────────
Converts today's digest Markdown to HTML and uploads it to Google Drive as a
Google Doc, using an OAuth Client ID for authentication.

Required environment variables:
  GOOGLE_OAUTH_CLIENT_SECRET_FILE  — path to your OAuth client secret JSON
                                      (downloaded from Google Cloud Console)

Optional:
  GOOGLE_DRIVE_FOLDER_ID           — ID of a Drive folder to place the doc in
  GOOGLE_TOKEN_FILE                — where to cache the OAuth token
                                      (default: token.json next to this script)

First run: a browser window will open asking you to authorise access.
Subsequent runs: the saved token is reused (and auto-refreshed when expired).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import markdown
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

OUTPUTS_DIR          = Path("outputs")
CLIENT_SECRET_FILE   = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET_FILE", "").strip()
DRIVE_FOLDER_ID      = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
TOKEN_FILE           = os.environ.get("GOOGLE_TOKEN_FILE",
                           str(Path(__file__).parent / "token.json"))

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# ── Helpers ───────────────────────────────────────────────────────────────────


def find_todays_digest() -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    path = OUTPUTS_DIR / f"digest-{today}.md"
    if not path.exists():
        raise FileNotFoundError(f"No digest found for today at: {path}")
    return path


def md_to_title_and_html(md_content: str) -> tuple[str, str]:
    """Extract the H1 as the doc title; convert the rest to HTML."""
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

    # Pre-process lines
    processed = []
    for line in body_md.splitlines():
        # Remove horizontal rules
        if line.strip() in ("---", "***", "___"):
            continue
        # Remove "N stories · Curated..." italics line
        if line.startswith("*") and "stories" in line and "Curated" in line:
            continue
        # Convert ## headings to bold paragraphs
        if line.startswith("## "):
            line = f"**{line[3:].strip()}**"
        processed.append(line)
    body_md = "\n".join(processed)
    md_converter = markdown.Markdown(extensions=["extra"])
    html_body = md_converter.convert(body_md)
    # Replace <p> tags with double line breaks for blank-line spacing between sections
    html_body = html_body.replace("<p>", "").replace("</p>", "<br><br>")

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title}</title>
</head>
<body>
  <strong>{title}</strong><br><br>
  {html_body}
</body>
</html>"""

    return title, html


# ── Google Drive ──────────────────────────────────────────────────────────────


def build_drive_service(client_secret_file: str):
    creds = None

    # Load cached token if it exists
    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Refresh or run the browser OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
            creds = flow.run_local_server(port=0)
        Path(TOKEN_FILE).write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


def upload_as_google_doc(service, title: str, html: str) -> str:
    """
    Upload HTML to Drive with mimeType=application/vnd.google-apps.document.
    Drive automatically converts the HTML to a native Google Doc.
    Returns the document's web URL.
    """
    file_metadata: dict = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
    }
    if DRIVE_FOLDER_ID:
        file_metadata["parents"] = [DRIVE_FOLDER_ID]

    media = MediaInMemoryUpload(
        html.encode("utf-8"),
        mimetype="text/html",
        resumable=False,
    )

    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,webViewLink",
    ).execute()

    return file.get("webViewLink", f"https://docs.google.com/document/d/{file['id']}/edit")


# ── Entry Point ───────────────────────────────────────────────────────────────


def main() -> None:
    if not CLIENT_SECRET_FILE:
        log.error("GOOGLE_OAUTH_CLIENT_SECRET_FILE is not set.")
        log.error("Set it to the path of your OAuth client secret JSON from Google Cloud Console.")
        sys.exit(1)

    if not Path(CLIENT_SECRET_FILE).exists():
        log.error(f"OAuth client secret file not found: {CLIENT_SECRET_FILE}")
        sys.exit(1)

    separator = "═" * 52
    log.info(separator)
    log.info(f"  Google Docs Publisher — {datetime.now().strftime('%Y-%m-%d')}")
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
    title, html = md_to_title_and_html(md_content)
    log.info(f"Title: {title}")

    # 3. Upload to Drive
    log.info("Uploading to Google Drive...")
    service = build_drive_service(CLIENT_SECRET_FILE)
    doc_url = upload_as_google_doc(service, title, html)

    log.info(separator)
    log.info(f"  Created → {doc_url}")
    log.info(separator)
    print(f"\nGoogle Doc created: {doc_url}\n")


if __name__ == "__main__":
    main()
