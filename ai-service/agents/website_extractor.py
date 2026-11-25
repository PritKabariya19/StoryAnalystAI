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
            else:
                external.append(entry)

            if len(internal) + len(external) >= 60:
                break

        return {"internal": internal[:30], "external": external[:30]}

    # ── Contact info ───────────────────────────────────────────────────

    def _extract_contact(self, soup: BeautifulSoup) -> dict:
        page_text = soup.get_text(separator=" ", strip=True)

        emails = list(set(self._EMAIL_RE.findall(page_text)))[:10]

        raw_phones = self._PHONE_RE.findall(page_text)
        phones = list(set(
            p.strip() for p in raw_phones
            if len(re.sub(r"\D", "", p)) >= 7
        ))[:10]

        # Address heuristic: look for text blocks containing address keywords
        addresses: list = []
        for tag in soup.find_all(["address", "p", "span", "div"]):
            txt = tag.get_text(strip=True)
            if txt and len(txt) < 300:
                lower = txt.lower()
                if any(h in lower for h in self._ADDRESS_HINTS):
                    if txt not in addresses:
                        addresses.append(txt)
            if len(addresses) >= 5:
                break

        # Also grab mailto: and tel: links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("mailto:"):
                email = href[7:].split("?")[0].strip()
                if email and email not in emails:
                    emails.append(email)
            elif href.startswith("tel:"):
                phone = href[4:].strip()
                if phone and phone not in phones:
                    phones.append(phone)

        return {
            "emails":    emails,
            "phones":    phones,
            "addresses": addresses,
        }

    # ── Tables ─────────────────────────────────────────────────────────

    def _extract_tables(self, soup: BeautifulSoup) -> list:
        tables: list = []
        for table_tag in soup.find_all("table"):
            rows: list = []
            for tr in table_tag.find_all("tr"):
                cells = [
                    td.get_text(strip=True)
                    for td in tr.find_all(["th", "td"])
                ]
                if cells:
                    rows.append(cells)
            if rows:
                # Use first row as header if it has <th> elements
                header_row = table_tag.find("tr")
                has_header = bool(header_row and header_row.find("th"))
                tables.append({
                    "has_header": has_header,
                    "rows":       rows,
                })
            if len(tables) >= 10:
                break
        return tables

    # ── Structured Data (JSON-LD, Microdata) ───────────────────────────

    def _extract_structured_data(self, soup: BeautifulSoup) -> list:
        items: list = []

        # JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                items.append({"type": "json-ld", "data": data})
            except (json.JSONDecodeError, TypeError):
                pass

        # Microdata (itemscope)
        for el in soup.find_all(attrs={"itemscope": True}):
            item_type = el.get("itemtype", "")
            props: dict = {}
            for prop in el.find_all(attrs={"itemprop": True}):
                name = prop.get("itemprop", "")
                value = (
                    prop.get("content")
                    or prop.get("href")
                    or prop.get("src")
                    or prop.get_text(strip=True)
                )
                if name and value:
                    props[name] = value
            if props:
                items.append({
                    "type": "microdata",
                    "item_type": item_type,
                    "properties": props,
                })
            if len(items) >= 10:
                break

        return items

    # ── Aggregate across pages ─────────────────────────────────────────

    def _aggregate(self, pages: list) -> dict:
        """Merge data from all crawled pages into a single summary dict."""
        all_headings: dict = {}
        all_paragraphs: list = []
        all_list_items: list = []
        all_images: list = []
        all_internal_links: list = []
        all_external_links: list = []
        all_emails: set = set()
        all_phones: set = set()
        all_addresses: list = []
        all_tables: list = []
        all_structured: list = []
        all_metadata: list = []

        for page in pages:
            if "error" in page:
                continue

            # Text
            text = page.get("text", {})
            for level, items in text.get("headings", {}).items():
                all_headings.setdefault(level, []).extend(items)
            all_paragraphs.extend(text.get("paragraphs", []))
            all_list_items.extend(text.get("list_items", []))

            # Images
            all_images.extend(page.get("images", []))

            # Links
            links = page.get("links", {})
            all_internal_links.extend(links.get("internal", []))
            all_external_links.extend(links.get("external", []))

            # Contact
            contact = page.get("contact_info", {})
            all_emails.update(contact.get("emails", []))
            all_phones.update(contact.get("phones", []))
            for addr in contact.get("addresses", []):
                if addr not in all_addresses:
                    all_addresses.append(addr)

            # Tables
            all_tables.extend(page.get("tables", []))

            # Structured data
            all_structured.extend(page.get("structured_data", []))

            # Metadata
            meta = page.get("metadata", {})
            if meta:
                all_metadata.append(meta)

        return {
            "pages_crawled": len([p for p in pages if "error" not in p]),
            "metadata":      all_metadata,
            "text": {
                "headings":   all_headings,
                "paragraphs": all_paragraphs[:80],
                "list_items": all_list_items[:80],
            },
            "images":          all_images[:40],
            "links": {
                "internal": all_internal_links[:40],
                "external": all_external_links[:40],
            },
            "contact_info": {
                "emails":    sorted(all_emails),
                "phones":    sorted(all_phones),
                "addresses": all_addresses[:10],
            },
            "tables":          all_tables[:15],
            "structured_data": all_structured[:15],
        }

    # ── Gemini AI summarisation ────────────────────────────────────────

    def _ai_summarise(self, aggregated: dict, query: str) -> str:
        """
        Send the aggregated extraction data + user query to Gemini
        and get a focused, structured summary.
        """
        # Build a compact representation to stay within token limits
        compact = self._compact_for_llm(aggregated)

        prompt = (
            "You are an expert web data analyst. A user has asked the following question "
            "about a website that was just crawled:\n\n"
            f"USER QUERY: \"{query}\"\n\n"
            "Below is the structured data extracted from the website. "
            "Analyze it and provide a clear, comprehensive answer to the user's query. "
            "Focus ONLY on information relevant to the query. "
            "If the requested information is not found, say so clearly.\n\n"
            "Format your response in clean markdown with headers, bullet points, "
            "and tables where appropriate.\n\n"
            f"EXTRACTED DATA:\n```json\n{compact}\n```"
        )

        try:
            model = genai.GenerativeModel(model_name=GEMINI_MODEL)
            response = model.generate_content(prompt)
            return response.text.strip()
        except Exception as exc:
            return f"⚠️ AI summarization failed: {str(exc)[:200]}"

    def _compact_for_llm(self, aggregated: dict) -> str:
        """
        Create a JSON string from the aggregated data, truncated to fit
        within Gemini's context window comfortably.
        """
        compact: dict = {}

        # Metadata
        metas = aggregated.get("metadata", [])
        if metas:
            compact["page_metadata"] = metas[:3]

        # Text (truncated)
        text = aggregated.get("text", {})
        compact["headings"] = text.get("headings", {})
        paragraphs = text.get("paragraphs", [])
        compact["paragraphs"] = paragraphs[:30]
        list_items = text.get("list_items", [])
        compact["list_items"] = list_items[:30]

        # Contact
        contact = aggregated.get("contact_info", {})
        if any(contact.values()):
            compact["contact_info"] = contact

        # Tables (first 5 only, rows capped)
        tables = aggregated.get("tables", [])
        if tables:
            compact["tables"] = [
                {"has_header": t.get("has_header"), "rows": t["rows"][:15]}
                for t in tables[:5]
            ]

        # Structured data
        structured = aggregated.get("structured_data", [])
        if structured:
            compact["structured_data"] = structured[:5]

        # Links (just counts + first few)
        links = aggregated.get("links", {})
        compact["links_summary"] = {
            "internal_count": len(links.get("internal", [])),
            "external_count": len(links.get("external", [])),
            "sample_internal": links.get("internal", [])[:10],
            "sample_external": links.get("external", [])[:10],
        }

        # Images (just alt + src, first 10)
        images = aggregated.get("images", [])
        if images:
            compact["images"] = [
                {"src": img["src"], "alt": img.get("alt", "")}
                for img in images[:10]
            ]

        raw = json.dumps(compact, ensure_ascii=False, default=str)
        # Hard-cap to avoid token overflow
        if len(raw) > self.MAX_TEXT_CHARS:
            raw = raw[:self.MAX_TEXT_CHARS] + "...(truncated)"
        return raw

    # ── Utilities ──────────────────────────────────────────────────────

    def _normalise_url(self, url: str) -> str:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url
