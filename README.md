# AI & Tech News Digest

A Python script that pulls headlines from major tech and business news sources, uses Claude to curate and summarize the top 8–10 AI/tech stories of the day, and publishes the result as a Substack newsletter post.

---

## What it produces

`outputs/digest-YYYY-MM-DD.md` containing:

- **Today** — a short conversational intro in the style of The New York Times' The Morning
- **Top 8–10 stories** — ranked by significance, each as a single paragraph with an inline source link

Stories are ordered by significance (cross-source coverage, outlet credibility, impact), not by publication time. A second Claude pass acts as an editor, checking each summary for accuracy, originality, redundancy, and style before the file is saved.

---

## Sources

**RSS feeds:** Techmeme, VentureBeat AI, MIT Technology Review, The Verge, Wired, Ars Technica, TechCrunch, Hacker News, Reuters Technology, NYT Technology, Washington Post Technology, WSJ Technology, The Economist, BBC Technology

**Scraped pages (fallback):** Bloomberg Technology, Axios Technology, AP Technology, Semafor Technology, Financial Times Technology

> Paywalled sources (Bloomberg, WSJ, FT) may only yield headlines. Claude summarises based on whatever text is available in the RSS snippet or scraped excerpt.

---

## Prerequisites

- Python 3.9 or higher
- An [Anthropic API key](https://console.anthropic.com/)
- A Substack publication (for publishing)

---

## Installation

### 1. Clone or download

```bash
git clone <your-repo-url>
cd ai-newsletter
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set environment variables

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GOOGLE_SERVICE_ACCOUNT_FILE="/path/to/service-account-key.json"
export GOOGLE_DRIVE_FOLDER_ID="your-folder-id"   # optional, but recommended
```

To make these permanent, add them to your shell profile (`~/.zshrc`, `~/.bashrc`, etc.).

---

## Google Docs setup

The publisher uploads your digest as a Google Doc using a Service Account — no browser auth, works indefinitely in cron.

### Step 1: Create a Google Cloud project and enable APIs

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or select an existing one)
3. Go to **APIs & Services → Enable APIs**
4. Enable **Google Drive API**

### Step 2: Create a Service Account

1. Go to **APIs & Services → Credentials → Create Credentials → Service Account**
2. Give it a name (e.g. "ai-digest-publisher") and click through to finish
3. Click your new service account → **Keys → Add Key → Create new key → JSON**
4. Download the JSON file and save it somewhere safe (e.g. `~/.config/ai-digest-sa.json`)
5. Set `GOOGLE_SERVICE_ACCOUNT_FILE` to that path

### Step 3: Share a Drive folder with the service account

So the doc appears in your own Drive (not just the service account's storage):

1. Create a folder in Google Drive for your digests (e.g. "AI Digest")
2. Click **Share** on the folder
3. Paste the service account's email address (found in the JSON file under `client_email`)
4. Give it **Editor** access
5. Copy the folder ID from the URL (`drive.google.com/drive/folders/THIS_PART`) and set it as `GOOGLE_DRIVE_FOLDER_ID`

---

## Running manually

### Generate the digest only

```bash
python3 digest.py
```

Saves to `outputs/digest-YYYY-MM-DD.md`. Takes 2–3 minutes.

### Publish to Google Docs (after generating the digest)

```bash
python3 publish_google.py
```

Creates a new Google Doc in your shared Drive folder and prints the URL.

### Run both in sequence

```bash
./run.sh
```

---

## Setting up the 4am daily cron job

### Step 1: Add environment variables to run.sh

Open `run.sh` and add your credentials near the top, after `set -euo pipefail`:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GOOGLE_SERVICE_ACCOUNT_FILE="/path/to/service-account-key.json"
export GOOGLE_DRIVE_FOLDER_ID="your-folder-id"
```

### Step 2: Register the cron job

Open your crontab:

```bash
crontab -e
```

Add this line. **4am EST = 9am UTC** (use `0 8 * * *` in summer when daylight saving is in effect):

```cron
0 9 * * * /absolute/path/to/ai-newsletter/run.sh
```

To find the absolute path:

```bash
pwd   # run from inside the ai-newsletter directory
```

Full example:

```cron
0 9 * * * /Users/yourname/ai-newsletter/run.sh
```

Logs are written to `pipeline.log` in the project directory.

### Step 3: Verify the cron job is registered

```bash
crontab -l
```

### macOS note: granting cron disk access

On macOS, cron may need Full Disk Access. Go to **System Settings → Privacy & Security → Full Disk Access** and add `/usr/sbin/cron`.

---

## Project structure

```
ai-newsletter/
├── digest.py              # Fetches sources, curates with Claude, saves markdown
├── publish_google.py      # Uploads digest to Google Docs via Drive API
├── publish_substack.py    # (Future) Email-to-post publisher for Substack
├── run.sh                 # Pipeline runner (digest → publish)
├── requirements.txt
├── README.md
├── pipeline.log           # Created on first run
└── outputs/
    └── digest-YYYY-MM-DD.md
```

---

## Customization

### Add or remove sources

Edit `RSS_SOURCES` and `SCRAPE_SOURCES` near the top of `digest.py`.

### Change the lookback window

Set `HOURS_BACK` (default: `24`) in `digest.py`.

### Change the number of stories

Edit the prompt in `curate_with_claude()` in `digest.py`.

### Change the model

Replace `"claude-sonnet-4-6"` in both `client.messages.create()` calls in `digest.py`.

### Switching to Substack later

When you're ready to publish directly to Substack, use `publish_substack.py` instead of `publish_google.py` in `run.sh`. See the Substack email-to-post instructions in that file's docstring.
