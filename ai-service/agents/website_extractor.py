"""
Website Extractor Agent
------------------------
Comprehensive, query-driven website information extraction.

Given a URL and a user query, this agent:
  1. Crawls the target URL (+ internal links up to configurable depth)
  2. Extracts structured data: text, images, links, metadata, contact
     info, JSON-LD / Schema.org, and tables
  3. Sends the raw extraction + user query to Gemini AI for a focused
     summary that highlights only the parts relevant to the query

Uses requests + BeautifulSoup for crawling (no JS execution).
"""

import json
import re
import time
from urllib.parse import urljoin, urlparse
from typing import List, Optional

import requests
from bs4 import BeautifulSoup, Comment

import google.generativeai as genai
from config import GEMINI_API_KEY, GEMINI_MODEL

genai.configure(api_key=GEMINI_API_KEY)


class WebsiteExtractorAgent:
    """
    Crawls a URL (up to a configurable depth) and extracts comprehensive
    structured data, then optionally summarises it via Gemini AI based on
    a user query.
    """

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    TIMEOUT = 12          # seconds per HTTP request
    MAX_PAGES = 6         # max internal pages to crawl
    MAX_TEXT_CHARS = 8000  # cap text sent to Gemini to avoid token overflow

    # ── Regex patterns for contact extraction ──────────────────────────
    _EMAIL_RE = re.compile(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    )
    _PHONE_RE = re.compile(
        r"(?:\+?\d{1,3}[\s\-.]?)?"          # optional country code
        r"(?:\(?\d{2,4}\)?[\s\-.]?)?"        # optional area code
        r"\d{3,4}[\s\-.]?\d{3,4}"            # main number
    )
    _ADDRESS_HINTS = [
        "street", "avenue", "ave", "road", "rd", "boulevard", "blvd",
        "lane", "drive", "dr", "court", "ct", "suite", "floor",
        "zip", "postal", "city", "state", "country",
    ]

    # ── Public API ─────────────────────────────────────────────────────

    def extract(self, url: str, query: str = "", depth: int = 1) -> dict:
        """
        Main entry point.

        Args:
            url:   The starting URL to extract from.
            query: An optional natural-language query (e.g. "find pricing").
            depth: 0 = start page only, 1 = start + direct internal links.

        Returns a dict with keys:
            start_url, pages (raw extraction per page),
            aggregated (merged data across all pages),
            ai_summary (Gemini response if query is non-empty).
        """
        url = self._normalise_url(url)
        base_domain = urlparse(url).netloc

        visited: set = set()
        queue: list = [(url, 0)]
        page_results: list = []

        # Suppress SSL warnings for self-signed certs
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        while queue and len(visited) < self.MAX_PAGES:
            current_url, current_depth = queue.pop(0)
            if current_url in visited:
                continue
            visited.add(current_url)

            page_data = self._extract_page(current_url)

            # If the initial URL fails, raise immediately
            if current_depth == 0 and "error" in page_data:
                raise ValueError(
                    f"Could not connect to {url}. "
                    f"Error: {page_data['error']}"
                )

            page_results.append(page_data)

            # Queue internal links for deeper crawl
            if current_depth < depth and "error" not in page_data:
                for link in page_data.get("links", {}).get("internal", []):
                    href = link.get("href", "")
                    if href and urlparse(href).netloc == base_domain and href not in visited:
                        queue.append((href, current_depth + 1))

        # Aggregate data across all crawled pages
        aggregated = self._aggregate(page_results)

        # AI summary (if a query was provided)
        ai_summary = ""
        if query.strip():
            ai_summary = self._ai_summarise(aggregated, query)

        return {
            "start_url":  url,
            "query":      query,
            "pages":      page_results,
            "aggregated": aggregated,
            "ai_summary": ai_summary,
        }

    # ── Single-page extraction ─────────────────────────────────────────

    def _extract_page(self, url: str) -> dict:
        try:
            resp = requests.get(
                url, headers=self.HEADERS,
                timeout=self.TIMEOUT, verify=False,
            )
            resp.raise_for_status()
            html = resp.text
        except requests.RequestException as exc:
            return {"url": url, "error": str(exc)}

        soup = BeautifulSoup(html, "html.parser")
        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

        return {
            "url":             url,
            "metadata":        self._extract_metadata(soup, url),
            "text":            self._extract_text(soup),
            "images":          self._extract_images(soup, base),
            "links":           self._extract_links(soup, base),
            "contact_info":    self._extract_contact(soup),
            "tables":          self._extract_tables(soup),
            "structured_data": self._extract_structured_data(soup),
        }

    # ── Metadata ───────────────────────────────────────────────────────

    def _extract_metadata(self, soup: BeautifulSoup, url: str) -> dict:
        meta: dict = {"url": url}

        # Title
        title_tag = soup.find("title")
        meta["title"] = title_tag.get_text(strip=True) if title_tag else ""

        # Meta tags
        for tag in soup.find_all("meta"):
            name = (
                tag.get("name", "")
                or tag.get("property", "")
            ).lower()
            content = tag.get("content", "")
            if not name or not content:
                continue

            if name in ("description",):
                meta["description"] = content
            elif name in ("keywords",):
                meta["keywords"] = [k.strip() for k in content.split(",")]
            elif name.startswith("og:"):
                meta.setdefault("og", {})[name] = content
            elif name.startswith("twitter:"):
                meta.setdefault("twitter", {})[name] = content
            elif name in ("author",):
                meta["author"] = content
            elif name in ("robots",):
                meta["robots"] = content

        # Language
        html_tag = soup.find("html")
        if html_tag:
            meta["language"] = html_tag.get("lang", "")

        # Canonical
        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href"):
            meta["canonical"] = canonical["href"]

        return meta

    # ── Text ───────────────────────────────────────────────────────────

    def _extract_text(self, soup: BeautifulSoup) -> dict:
        # Remove non-content tags
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()
        # Remove comments
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        headings: dict = {}
        for level in range(1, 7):
            tag_name = f"h{level}"
            found = [h.get_text(strip=True) for h in soup.find_all(tag_name)]
            if found:
                headings[tag_name] = found

        paragraphs = [
            p.get_text(strip=True)
            for p in soup.find_all("p")
            if p.get_text(strip=True)
        ][:50]  # cap to 50 paragraphs

        list_items = [
            li.get_text(strip=True)
            for li in soup.find_all("li")
            if li.get_text(strip=True)
        ][:60]

        return {
            "headings":   headings,
            "paragraphs": paragraphs,
            "list_items": list_items,
        }

    # ── Images ─────────────────────────────────────────────────────────

    def _extract_images(self, soup: BeautifulSoup, base: str) -> list:
        images: list = []
        seen: set = set()
        for img in soup.find_all("img"):
            src = img.get("src", "").strip()
            if not src or src.startswith("data:"):
                continue
            full_src = urljoin(base, src)
            if full_src in seen:
                continue
            seen.add(full_src)

            # Gather context (parent text or sibling text)
            parent_text = ""
            parent = img.find_parent(["figure", "div", "section", "article"])
            if parent:
                parent_text = parent.get_text(strip=True)[:120]

            images.append({
                "src":     full_src,
                "alt":     img.get("alt", ""),
                "width":   img.get("width", ""),
                "height":  img.get("height", ""),
                "context": parent_text,
            })
            if len(images) >= 30:
                break
        return images

    # ── Links ──────────────────────────────────────────────────────────

    def _extract_links(self, soup: BeautifulSoup, base: str) -> dict:
        base_domain = urlparse(base).netloc
        internal: list = []
        external: list = []
        seen: set = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            full = urljoin(base, href)
            if full in seen:
                continue
            seen.add(full)

            text = a.get_text(strip=True) or href
            entry = {"text": text, "href": full}

            if urlparse(full).netloc == base_domain:
                internal.append(entry)
