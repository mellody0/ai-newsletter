#!/usr/bin/env python3
"""
Daily AI & Tech News Digest
────────────────────────────
Collects headlines from major RSS feeds and news pages, then uses Claude to
curate, rank, and summarize the top 8–10 stories into a Markdown digest.

Usage:
    python digest.py

Environment:
    ANTHROPIC_API_KEY  — required. Your Anthropic API key.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from calendar import timegm
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import anthropic
import feedparser
import requests
from bs4 import BeautifulSoup

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

OUTPUTS_DIR = Path("outputs")
HOURS_BACK = 24
REQUEST_TIMEOUT = 15       # seconds per HTTP request
REQUEST_DELAY = 1.0        # polite delay between outbound requests (seconds)
MAX_ARTICLES_TO_CLAUDE = 120  # cap article pool to avoid token limits

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Source Configuration ──────────────────────────────────────────────────────

RSS_SOURCES = [
    # Editorial signal: what's already trending in tech
    {"name": "Techmeme",              "url": "https://www.techmeme.com/feed.xml"},
    # AI-first publications
    {"name": "VentureBeat AI",        "url": "https://venturebeat.com/category/ai/feed/"},
    {"name": "MIT Technology Review", "url": "https://www.technologyreview.com/feed/"},
    # Broad tech press
    {"name": "The Verge",             "url": "https://www.theverge.com/rss/index.xml"},
    {"name": "Wired",                 "url": "https://www.wired.com/feed/rss"},
    {"name": "Ars Technica",          "url": "https://feeds.arstechnica.com/arstechnica/index"},
    {"name": "TechCrunch",            "url": "https://techcrunch.com/feed/"},
    {"name": "Hacker News",           "url": "https://hnrss.org/best"},
    # Business / mainstream press
    {"name": "Reuters Technology",    "url": "https://feeds.reuters.com/reuters/technologyNews"},
    {"name": "NYT Technology",        "url": "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"},
    {"name": "Washington Post Tech",  "url": "https://feeds.washingtonpost.com/rss/business/technology"},
    {"name": "WSJ Technology",        "url": "https://feeds.wsj.com/wsj/xml/rss/3_7085.xml"},
    {"name": "The Economist S&T",     "url": "https://www.economist.com/science-and-technology/rss.xml"},
    {"name": "BBC Technology",        "url": "http://feeds.bbci.co.uk/news/technology/rss.xml"},
]

# Sources scraped from their homepages (no reliable public RSS)
SCRAPE_SOURCES = [
    {"name": "Bloomberg Technology",  "url": "https://www.bloomberg.com/technology"},
    {"name": "Axios Technology",      "url": "https://www.axios.com/technology"},
    {"name": "AP Technology",         "url": "https://apnews.com/hub/technology"},
    {"name": "Semafor Technology",    "url": "https://www.semafor.com/topic/technology"},
    {"name": "Financial Times Tech",  "url": "https://www.ft.com/technology"},
]

# ── Date Helpers ──────────────────────────────────────────────────────────────


def parse_entry_date(entry) -> Optional[datetime]:
    """Return a UTC-aware datetime from a feedparser entry, or None."""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime.fromtimestamp(timegm(val), tz=timezone.utc)
            except Exception:
                continue
    return None


def is_recent(dt: Optional[datetime], hours: int = HOURS_BACK) -> bool:
    """True if dt falls within the lookback window, or if dt is None (undatable)."""
    if dt is None:
        return True  # keep undated articles; Claude will filter by relevance
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    return dt >= cutoff


# ── HTML / Text Helpers ───────────────────────────────────────────────────────


def strip_html(raw: str, max_chars: int = 400) -> str:
    """Strip HTML tags and collapse whitespace, truncated to max_chars."""
    if not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    return text[:max_chars]


def get_entry_snippet(entry) -> str:
    """Return the best available text snippet from a feedparser entry."""
    if hasattr(entry, "content") and entry.content:
        return strip_html(entry.content[0].get("value", ""))
    for attr in ("summary", "description"):
        val = getattr(entry, attr, None)
        if val:
            return strip_html(val)
    return ""


def extract_techmeme_source_link(entry) -> Optional[str]:
    """
    Techmeme RSS entries use a techmeme.com discussion URL as `link`, but embed
    the primary source URL (e.g. CNBC, NYT) as the first external href inside
    the summary HTML. Extract and return that URL, or None if not found.
    """
    summary_html = getattr(entry, "summary", "") or ""
    if not summary_html:
        return None
    soup = BeautifulSoup(summary_html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("http") and "techmeme.com" not in href:
            return href
    return None


# ── Fetchers ──────────────────────────────────────────────────────────────────


def fetch_rss(source: dict) -> list:
    """Fetch and parse an RSS/Atom feed. Returns a list of article dicts."""
    name = source["name"]
    url = source["url"]
    articles = []

    try:
        log.info(f"  [RSS]    {name}")
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)

        if feed.bozo and not feed.entries:
            log.warning(f"           ↳ malformed feed, skipping")
            return articles

        for entry in feed.entries:
            pub_dt = parse_entry_date(entry)
            if not is_recent(pub_dt):
                continue

            title = strip_html(getattr(entry, "title", ""), max_chars=200)
            raw_link = getattr(entry, "link", "").strip()
            # For Techmeme, swap the discussion-page URL for the real source URL
            if "techmeme.com" in raw_link:
                link = extract_techmeme_source_link(entry) or raw_link
            else:
                link = raw_link
            snippet = get_entry_snippet(entry)

            if not title or not link:
                continue

            articles.append({
                "source": name,
                "title": title,
                "link": link,
                "snippet": snippet,
                "published": pub_dt.isoformat() if pub_dt else None,
            })

        log.info(f"           ↳ {len(articles)} recent articles")

    except Exception as exc:
        log.warning(f"           ↳ failed ({type(exc).__name__}): {exc}")

    time.sleep(REQUEST_DELAY)
    return articles


def fetch_scraped(source: dict) -> list:
    """Scrape article headlines from a news homepage using structural heuristics."""
    name = source["name"]
    url = source["url"]
    articles = []

    try:
        log.info(f"  [SCRAPE] {name}")
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        base_netloc = urlparse(url).netloc
        base_url = f"{urlparse(url).scheme}://{base_netloc}"
        seen: set = set()
        candidates = []

        # Strategy 1: explicit <article> elements with a heading + link
        for article_tag in soup.find_all("article"):
            heading = article_tag.find(["h1", "h2", "h3", "h4"])
            link_tag = article_tag.find("a", href=True)
            if heading and link_tag:
                candidates.append((heading.get_text(strip=True), link_tag["href"]))

        # Strategy 2: standalone headings that are or contain a link
        if not candidates:
            for heading in soup.find_all(["h2", "h3"]):
                a = heading.find("a", href=True) or heading.find_parent("a")
                if a:
                    candidates.append((heading.get_text(strip=True), a["href"]))

        for title, href in candidates[:40]:
            title = title.strip()
            if len(title) < 15:
                continue
            link = urljoin(base_url, href) if not href.startswith("http") else href
            # Discard links pointing off-site (nav/promo links)
            if urlparse(link).netloc != base_netloc:
                continue
            if link in seen:
                continue
            seen.add(link)
            articles.append({
                "source": name,
                "title": title,
                "link": link,
                "snippet": "",
                "published": None,
            })

        log.info(f"           ↳ {len(articles)} headlines scraped")

    except Exception as exc:
        log.warning(f"           ↳ failed ({type(exc).__name__}): {exc}")

    time.sleep(REQUEST_DELAY)
    return articles


# ── Collection & Deduplication ────────────────────────────────────────────────


def collect_articles() -> list:
    """Gather articles from all sources, deduplicate by URL, and trim to cap."""
    all_articles = []

    log.info("── Fetching RSS feeds ──────────────────────────────────────────")
    for source in RSS_SOURCES:
        all_articles.extend(fetch_rss(source))

    log.info("── Scraping fallback sources ───────────────────────────────────")
    for source in SCRAPE_SOURCES:
        all_articles.extend(fetch_scraped(source))

    # Deduplicate by normalised URL
    seen_urls: set = set()
    unique = []
    for article in all_articles:
        key = article["link"].rstrip("/").lower()
        if key not in seen_urls:
            seen_urls.add(key)
            unique.append(article)

    # Prioritise dated articles so Claude has better temporal context
    dated = [a for a in unique if a["published"]]
    undated = [a for a in unique if not a["published"]]
    trimmed = (dated + undated)[:MAX_ARTICLES_TO_CLAUDE]

    log.info(f"── {len(unique)} unique articles collected → sending {len(trimmed)} to Claude")
    return trimmed


# ── Claude Curation ───────────────────────────────────────────────────────────


def curate_with_claude(articles: list, client: anthropic.Anthropic) -> dict:
    """
    Send the article pool to Claude. Returns a dict with:
      - "narrative_pulse":  short editorial overview paragraph
      - "stories":          list of ranked, summarised story dicts
    """
    article_block = "\n\n".join(
        f"[{i + 1}]\n"
        f"SOURCE:  {a['source']}\n"
        f"TITLE:   {a['title']}\n"
        f"URL:     {a['link']}\n"
        f"DATE:    {a['published'] or 'unknown'}\n"
        f"SNIPPET: {a['snippet'] or '(none)'}"
        for i, a in enumerate(articles)
    )

    today = datetime.now().strftime("%B %d, %Y")

    prompt = f"""You are the editor of a daily AI and technology news digest. Today is {today}.

Below is a raw pool of articles collected from major news sources in the past 24 hours.

---

## STYLE GUIDE

Write in the manner of The Economist, adapted for American conventions. These rules govern every word you write — headlines, summaries, and the Narrative Pulse alike.

### Voice and tone
- Be direct, confident, and precise. Say what you mean on the first attempt.
- Assume an intelligent, busy reader — a senior professional who follows tech and business closely but does not need basics explained.
- Aim for authority without pomposity. Wit is welcome when it sharpens a point; avoid it when it obscures one.
- Never be vague when you can be specific. "IBM shares fell 12%" beats "IBM shares fell sharply."

### Sentences and structure
- Prefer short sentences. If a sentence needs a second comma, consider splitting it.
- Use the active voice. "Anthropic accused three firms" not "Three firms were accused by Anthropic."
- Lead with the most important fact. Do not bury the news.
- One idea per sentence. One theme per paragraph.
- Avoid throat-clearing openers: never start with "In a significant development," "It is worth noting," or similar.
- Do not start a summary by repeating the headline verbatim or paraphrasing it word-for-word.

### Word choice
- Prefer plain words: "use" not "utilize," "start" not "commence," "show" not "demonstrate."
- Be specific rather than evaluative: instead of "major announcement," say what the announcement was.
- Avoid: "key," "iconic," "landmark," "groundbreaking," "revolutionary," "game-changing," "transformative," "cutting-edge," "robust," "leverage" (as a verb), "going forward," "at the end of the day," "in terms of," "a number of," "space" (as a sector noun), "arguably," "essentially," "basically," "very."
- Do not use "impact" as a verb. Use "affect," "hit," or "hurt."
- "More than" not "over" for quantities. "Fewer" not "less" for countable things.
- Use "said" for attribution. Avoid "stated," "noted," "opined," "penned."
- Avoid euphemisms. If a company fired workers, say it fired them.

### Grammar and punctuation
- Use the Oxford (serial) comma: "chips, software, and services."
- American spelling throughout: "organization" not "organisation," "recognize" not "recognise."
- Punctuation goes inside quotation marks: He called it "a disaster."
- Use the % symbol, not "percent" or "per cent."
- Numbers: spell out one through nine; use numerals for 10 and above. Always use numerals for percentages, dollar amounts, and statistics.
- Billions and millions: "$4 billion," not "$4,000,000,000." Do not abbreviate as "4B" or "4bn."
- Dates: "February 23, 2026" — no ordinal suffixes (not "February 23rd").
- Em dashes (—) for parenthetical asides, with no spaces on either side.
- Avoid exclamation marks entirely.

### What to avoid
- Jargon unexplained to a non-specialist: define acronyms on first use unless they are universally known (AI, CEO, GDP are fine; MoE, RLHF, LoRA are not).
- Clichés: "needle-moving," "paradigm shift," "sea change," "watershed moment."
- Filler phrases: "It is important to note that," "This comes as," "This follows."
- Passive constructions where an active alternative is available.
- Editorializing about your own choices ("Importantly," "Notably," "Significantly").

---

## YOUR TASKS

### 1. Select the top 8–10 stories
Prioritize:
- Stories that appear across multiple sources (editorial consensus signals importance)
- News from high-credibility outlets (Reuters, AP, NYT, WSJ, Bloomberg, FT, The Economist)
- Major AI developments: model releases, product launches, funding rounds, policy decisions, research breakthroughs
- Significant business impact or societal consequence
- Stories featured prominently on Techmeme or at the top of major homepages

Exclude: opinion pieces, listicles, evergreen how-to content. When the same story appears from multiple sources, keep only the strongest version.

### 2. Write each story as a single cohesive paragraph with a lead sentence and body
- The **lead sentence** is the pseudo-headline: it must stand alone as the most important fact, written as a crisp declarative statement. It will be rendered in bold.
- The **body** is 1–2 sentences that add context, cause, or consequence — information the lead does not already contain. Do not restate or paraphrase the lead.
- Together, lead + body must read as one smooth, unified paragraph.
- Apply the full style guide to every sentence.

### 3. Write a brief intro (1–2 short paragraphs)
A quick, direct orientation to the day — not an essay, not a summary.

Guidelines:
- 1–2 paragraphs maximum. Each paragraph is 2–3 short sentences.
- Lead with the single most important thing happening today, stated plainly.
- You may reference 1–2 other stories, but do not summarize them — the stories follow immediately below.
- Conversational and human. Short sentences. No throat-clearing, no grand pronouncements, no clichés.
- Do not use "Good morning." Do not address the reader as "you."

---

## OUTPUT FORMAT

Return ONLY valid JSON — no markdown fences, no extra commentary — matching this schema exactly:

{{
  "intro": "<2–3 short paragraphs written in the style of The Morning; separate paragraphs with \\n\\n inside the JSON string>",
  "stories": [
    {{
      "rank": 1,
      "sources": [
        {{"name": "<source name>", "url": "<article URL>"}},
        {{"name": "<second source name if applicable>", "url": "<second article URL>"}}
      ],
      "lead": "<single pseudo-headline sentence>",
      "body": "<1–2 sentence follow-on that adds context not already in the lead>"
    }}
  ]
}}

## Article Pool

{article_block}"""

    log.info("── Calling Claude (claude-sonnet-4-6) ─────────────────────────")
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # Strip accidental markdown code fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    json_match = re.search(r"\{[\s\S]*\}", raw)
    if json_match:
        raw = json_match.group(0)

    result = json.loads(raw)
    log.info(f"           ↳ {len(result.get('stories', []))} stories selected")
    return result


# ── Editorial Review ─────────────────────────────────────────────────────────


def find_source_article(url: str, articles: list) -> dict:
    """Match a curated story URL back to its original article dict."""
    def normalise(u: str) -> str:
        return u.rstrip("/").lower().split("?")[0]

    target = normalise(url)
    for a in articles:
        if normalise(a["link"]) == target:
            return a

    # Fallback: substring match on the URL path
    for a in articles:
        norm = normalise(a["link"])
        if target in norm or norm in target:
            return a

    return {}


def edit_with_claude(curated: dict, articles: list, client: anthropic.Anthropic) -> dict:
    """
    Second Claude pass: an independent editor reviews every summary and the
    intro for (1) accuracy against source material and (2) style guide
    compliance, then returns a corrected version of the curated dict.
    """
    stories = curated.get("stories", [])
    pulse = curated.get("intro", "")

    # Pair each story with its original headline + snippet
    review_block = "\n\n".join(
        "\n".join([
            f"[STORY {s.get('rank')}]",
            f"SOURCES:          {'; '.join(src['name'] + ' (' + src['url'] + ')' for src in s.get('sources', []))}",
            f"DRAFT LEAD:       {s.get('lead', '')}",
            f"DRAFT BODY:       {s.get('body', '')}",
            f"── Source material ──",
            f"ORIGINAL TITLE:   {find_source_article(s.get('sources', [{}])[0].get('url', ''), articles).get('title', '(not found in pool)')}",
            f"ORIGINAL SNIPPET: {find_source_article(s.get('sources', [{}])[0].get('url', ''), articles).get('snippet', '(none)')}",
        ])
        for s in stories
    )

    prompt = f"""You are a senior editor reviewing a draft AI and technology news digest before publication. A junior editor has already selected and summarised the top stories. Your job is to catch errors before they reach readers.

IMPORTANT: Output ONLY the corrected JSON object. Do not write any analysis, notes, reasoning, or explanation before or after it. Apply your judgment silently and emit the result directly.

## Your Four Responsibilities

### 1. Accuracy
Check each draft summary against its source material (the original title and snippet provided below).

Rules:
- The lead and body together form one paragraph. The lead is bolded as a pseudo-headline; the body follows immediately.
- A lead or body sentence may only assert facts present in the original title or snippet. If a specific number, name, quote, or claim appears in the draft but not in the source material, remove or soften it.
- If the snippet is absent or too thin to verify a claim, rewrite to reflect only what is certain from the headline. Do not invent hedges like "reportedly" to paper over a gap — cut the unverifiable claim instead.
- Do not add new information that was not in the draft. Your job is to remove or correct, not to expand.
- If the lead and body are accurate and well-written, leave them unchanged.
- Do not describe a valuation change as a sudden "jump" or "surge" unless the source explicitly attributes it to a single transaction. If the change occurred over a period (e.g., year-over-year), write "has grown" or "rose over the past year" instead.
- Do not describe a product update, feature addition, or expansion as a "launch" unless the source explicitly says the product itself is new. If an existing product gained new features or tools, use "added," "expanded," or "updated" instead.

### 2. Originality
Check the lead and body against the original title and snippet for verbatim copying.

Rules:
- No run of five or more consecutive words from the original title or snippet may appear unchanged in the lead or body. Proper nouns, people's names, company names, and product names are exempt — those cannot be paraphrased without losing meaning.
- If a verbatim match exists, rephrase the sentence in your own words while preserving the factual meaning exactly. Do not alter the facts to fix the wording.
- This check also applies to the intro: no phrase lifted from any snippet should appear unchanged.

### 3. Redundancy
- The body must not restate or paraphrase the lead. Every sentence must add information the previous one does not contain.
- If the body partly repeats the lead, cut the repeated portion and preserve only the new information.

### 4. Style
Apply The Economist style guide (American conventions) to the story lead and body sentences. The intro uses a different, more conversational register — do not apply Economist formality to it, but do fix any grammar errors or clichés.

Key rules — enforce all of them:
- Active voice. Short sentences. One idea per sentence.
- Specific over vague: numbers and names beat adjectives.
- Banned words: "key," "significant," "major," "iconic," "landmark," "groundbreaking," "revolutionary," "game-changing," "transformative," "cutting-edge," "robust," "leverage" (verb), "going forward," "at the end of the day," "in terms of," "a number of," "space" (sector noun), "arguably," "essentially," "basically," "very," "notably," "importantly."
- Do not use "impact" as a verb.
- "More than" not "over" for quantities. "Fewer" not "less" for countable things.
- Use "said" for attribution.
- Oxford comma. American spelling. % symbol. Spell out one–nine, numerals for 10+.
- No exclamation marks. No clichés.

Only fix what violates a rule. Do not rewrite for the sake of it.

## Output Format

Return ONLY valid JSON — no markdown fences, no extra text — using this schema exactly:

{{
  "intro": "<reviewed and corrected intro; separate paragraphs with \\n\\n inside the JSON string>",
  "stories": [
    {{
      "rank": 1,
      "sources": "<unchanged array of {{name, url}} objects>",
      "lead": "<corrected lead sentence>",
      "body": "<corrected body — no repetition of the lead>"
    }}
  ]
}}

## Intro to Review

{pulse}

## Stories to Review

{review_block}"""

    log.info("── Calling Claude for editorial review ─────────────────────")
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    # Find the start of the JSON object in case Claude adds any preamble
    json_start = raw.find('{"intro"')
    if json_start != -1:
        raw = raw[json_start:]

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning(f"           ↳ JSON parse failed ({exc}); using unedited draft")
        log.warning(f"           ↳ First 300 chars of response: {raw[:300]!r}")
        return curated

    log.info(f"           ↳ editorial review complete")
    return result


# ── Markdown Rendering ────────────────────────────────────────────────────────


def render_markdown(data: dict, date: datetime) -> str:
    """Render the curated data dict as the final Markdown digest."""
    date_str = date.strftime("%B %d, %Y")
    stories = data.get("stories", [])
    pulse = data.get("intro", "").replace("\\n\\n", "\n\n")

    lines = [
        f"# AI & Tech News Digest — {date_str}",
        "",
        f"*{len(stories)} stories · Curated {date_str}*",
        "",
        "---",
        "",
        "## Today",
        "",
        pulse,
        "",
        "---",
        "",
        "## Top Stories",
        "",
    ]

    for story in stories:
        rank = story.get("rank", "")
        sources = story.get("sources", [])
        lead = story.get("lead", "").strip()
        body = story.get("body", "").strip()

        source_links = " / ".join(
            f"[{s['name']}]({s['url']})" for s in sources if s.get("name") and s.get("url")
        )
        citation = f"({source_links})" if source_links else ""

        lines += [
            f"{rank}. {lead} {body} {citation}".strip(),
            "",
            "---",
            "",
        ]

    lines += [
        "",
        f"*Generated {date_str} using [Claude](https://anthropic.com) (claude-sonnet-4-6).*",
    ]

    return "\n".join(lines)


# ── Entry Point ───────────────────────────────────────────────────────────────


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY is not set. Export it and try again.")
        sys.exit(1)

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    client = anthropic.Anthropic(api_key=api_key)
    today = datetime.now()
    output_path = OUTPUTS_DIR / f"digest-{today.strftime('%Y-%m-%d')}.md"

    separator = "═" * 52
    log.info(separator)
    log.info(f"  Daily Digest — {today.strftime('%Y-%m-%d')}")
    log.info(separator)

    # 1. Collect
    articles = collect_articles()
    if not articles:
        log.error("No articles collected. Check your internet connection.")
        sys.exit(1)

    # 2. Curate + summarise via Claude
    curated = curate_with_claude(articles, client)

    # 3. Editorial review pass
    curated = edit_with_claude(curated, articles, client)

    # 4. Render
    markdown = render_markdown(curated, today)

    # 5. Save
    output_path.write_text(markdown, encoding="utf-8")

    log.info(separator)
    log.info(f"  Saved → {output_path}")
    log.info(separator)
    print(f"\nDigest saved to: {output_path}\n")


if __name__ == "__main__":
    main()
