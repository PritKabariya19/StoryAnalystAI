import re
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext, Error as PlaywrightError, sync_playwright


class WebsiteExplorerAgent:
    """
    Powerful website explorer that crawls pages and extracts
    comprehensive, structured data for QA test generation.
    Works reliably across all types of websites.
    """

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    TIMEOUT = 15          # seconds per page load
    RENDER_WAIT = 5       # max seconds to let JavaScript finish rendering
    MAX_RETRIES = 3       # retry failed requests
    MAX_PAGES = 20        # max internal pages to crawl
    MAX_LINKS = 50        # max links to return per page
    MAX_IMAGES = 40       # max images per page
    MAX_HEADINGS = 50     # max headings per page

    # ── Tech stack signatures ──────────────────────────────────────────────
    TECH_SIGNATURES = {
        "React":       [r"react", r"__react", r"_reactRoot", r"data-reactroot", r"data-reactid"],
        "Next.js":     [r"__next", r"_next/", r"__NEXT_DATA__"],
        "Vue.js":      [r"vue", r"__vue__", r"data-v-", r"v-bind", r"v-model"],
        "Nuxt.js":     [r"__nuxt", r"_nuxt/"],
        "Angular":     [r"ng-version", r"ng-app", r"ng-controller", r"_nghost", r"_ngcontent"],
        "jQuery":      [r"jquery", r"jQuery"],
        "Bootstrap":   [r"bootstrap", r"class=\"[^\"]*\b(container|row|col-|btn-|navbar)\b"],
        "Tailwind CSS":[r"tailwindcss", r"class=\"[^\"]*\b(flex|grid|text-|bg-|p-|m-|rounded)\b[^\"]*\b(flex|text-|bg-|p-)\b"],
        "WordPress":   [r"wp-content", r"wp-includes", r"wp-json", r"wordpress"],
        "Shopify":     [r"shopify", r"cdn.shopify"],
        "Wix":         [r"wix.com", r"wixstatic"],
        "Squarespace": [r"squarespace"],
        "Django":      [r"csrfmiddlewaretoken", r"django"],
        "Laravel":     [r"laravel", r"csrf-token"],
        "Express":     [r"express"],
        "Flask":       [r"flask"],
        "ASP.NET":     [r"__VIEWSTATE", r"__EVENTVALIDATION", r"aspnet"],
        "Font Awesome":[r"font-awesome", r"fontawesome", r'class="[^"]*\bfa\b'],
        "Google Analytics": [r"google-analytics", r"gtag", r"GoogleAnalyticsObject", r"ga\("],
        "Google Fonts":[r"fonts.googleapis", r"fonts.gstatic"],
        "reCAPTCHA":   [r"recaptcha", r"g-recaptcha"],
    }

    # ══════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════════

    def explore(self, start_url: str, depth: int = 1) -> dict:
        """
        Main entry point.
        depth=0 → only the start page
        depth=1 → start page + its direct internal links (default)
        depth=2 → 2 levels deep
        """
        start_url = self._normalise(start_url)
        base_domain = urlparse(start_url).netloc

        visited = set()
        queue = [(start_url, 0)]
        pages = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            # Certificate errors are ignored so staging sites can be explored.
            context = browser.new_context(
                user_agent=self.USER_AGENT, locale="en-US",
                ignore_https_errors=True,
            )
            while queue and len(visited) < self.MAX_PAGES:
                url, current_depth = queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)

                page_data = self._scrape_page(context, url)
                pages.append(page_data)

                # Queue internal links for next depth level
                if current_depth < depth:
                    for link in page_data.get("links", []):
                        href = link.get("href", "")
                        if (href
                            and link.get("is_internal", False)
                            and href not in visited
                            and len(visited) + len(queue) < self.MAX_PAGES * 2):
                            queue.append((href, current_depth + 1))

        # Build site-wide summary
        summary = self._build_site_summary(pages)

        return {
            "start_url": start_url,
            "pages_crawled": len(pages),
            "site_summary": summary,
            "pages": pages,
        }

    # ══════════════════════════════════════════════════════════════════════
    # SCRAPE A SINGLE PAGE (with retry)
    # ══════════════════════════════════════════════════════════════════════

    def _scrape_page(self, context: BrowserContext, url: str) -> dict:
        html = None
        status_code = None
        error_msg = None

        for attempt in range(self.MAX_RETRIES):
            page = context.new_page()
            try:
                resp = page.goto(url, wait_until="domcontentloaded",
                                 timeout=self.TIMEOUT * 1000)
                status_code = resp.status if resp else None
                if status_code and status_code >= 400:
                    error_msg = f"HTTP {status_code} error for url: {url}"
                else:
                    try:
                        page.wait_for_load_state("networkidle",
                                                 timeout=self.RENDER_WAIT * 1000)
                    except PlaywrightError:
                        pass  # busy pages never go idle; use what rendered
                    html = page.content()
                    break
            except PlaywrightError as exc:
                error_msg = str(exc)
            finally:
                page.close()
            if attempt < self.MAX_RETRIES - 1:
                time.sleep(1 * (attempt + 1))  # backoff

        if html is None:
            return {
                "url": url,
                "status_code": status_code,
                "title": "Error",
                "error": error_msg or "Failed to fetch page",
                "meta": {}, "headings": [], "forms": [], "buttons": [],
                "images": [], "links": [], "navigations": [], "tables": [],
                "interactive_elements": [], "media": [],
                "tech_stack": [], "accessibility_issues": [], "statistics": {},
            }

        soup = BeautifulSoup(html, "html.parser")
        base = "{uri.scheme}://{uri.netloc}".format(uri=urlparse(url))
        base_domain = urlparse(url).netloc

        return {
            "url": url,
            "status_code": status_code,
            "title": self._get_title(soup),
            "meta": self._get_meta(soup),
            "headings": self._get_headings(soup),
            "forms": self._get_forms(soup),
            "standalone_inputs": self._get_standalone_inputs(soup),
            "buttons": self._get_standalone_buttons(soup),
            "images": self._get_images(soup, base),
            "links": self._get_links(soup, base, base_domain),
            "navigations": self._get_navigations(soup, base),
            "tables": self._get_tables(soup),
            "interactive_elements": self._get_interactive_elements(soup),
            "media": self._get_media(soup, base),
            "tech_stack": self._detect_tech_stack(html, soup),
            "accessibility_issues": self._audit_accessibility(soup),
            "statistics": self._get_statistics(soup),
        }

    # ══════════════════════════════════════════════════════════════════════
    # EXTRACTION METHODS
    # ══════════════════════════════════════════════════════════════════════

    def _generate_selector(self, el) -> str:
        """Generate a basic, readable CSS selector for an element."""
        if el.get("id"):
            return f"#{el.get('id')}"
        if el.get("name"):
            return f"{el.name}[name='{el.get('name')}']"
        classes = el.get("class")
        if classes:
            return f"{el.name}." + ".".join(classes)
        return el.name

    def _get_title(self, soup: BeautifulSoup) -> str:
        tag = soup.find("title")
        if tag and tag.get_text(strip=True):
            return tag.get_text(strip=True)
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)
        return "Untitled"

    # ── Meta / SEO ─────────────────────────────────────────────────────────

    def _get_meta(self, soup: BeautifulSoup) -> dict:
        meta = {}
        # Standard meta tags
        for tag in soup.find_all("meta"):
            name = (tag.get("name") or tag.get("property") or "").lower()
            content = tag.get("content", "")
            if name == "description":
                meta["description"] = content
            elif name == "keywords":
                meta["keywords"] = content
            elif name == "viewport":
                meta["viewport"] = content
            elif name == "robots":
                meta["robots"] = content
            elif name == "author":
                meta["author"] = content
            elif name.startswith("og:"):
                meta.setdefault("og_tags", {})[name] = content
            elif name.startswith("twitter:"):
                meta.setdefault("twitter_tags", {})[name] = content

        # Canonical
        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href"):
            meta["canonical"] = canonical["href"]

        # Favicon
        favicon = soup.find("link", rel=lambda r: r and "icon" in (r if isinstance(r, str) else " ".join(r)))
        if favicon and favicon.get("href"):
            meta["favicon"] = favicon["href"]

        # Language
        html_tag = soup.find("html")
        if html_tag and html_tag.get("lang"):
            meta["language"] = html_tag["lang"]

        # Charset
        charset_tag = soup.find("meta", charset=True)
        if charset_tag:
            meta["charset"] = charset_tag["charset"]

        return meta

    # ── Headings ───────────────────────────────────────────────────────────

    def _get_headings(self, soup: BeautifulSoup) -> list:
        headings = []
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            text = tag.get_text(strip=True)
            if text:
                headings.append({
                    "level": int(tag.name[1]),
                    "text": text[:200],
                })
            if len(headings) >= self.MAX_HEADINGS:
                break
        return headings

    # ── Forms (enhanced) ────────────────────────────────────────────────────

    def _get_forms(self, soup: BeautifulSoup) -> list:
        forms = []
        for form_tag in soup.find_all("form"):
            form_name = (
                form_tag.get("id")
                or form_tag.get("name")
                or form_tag.get("aria-label")
                or (form_tag.get("class", [""])[0] if form_tag.get("class") else "")
                or "form"
            )

            fields = []
            for inp in form_tag.find_all(["input", "select", "textarea"]):
                field_type = inp.get("type", inp.name).lower()
                if field_type in ("hidden", "submit", "button", "image", "reset"):
                    continue

                field_info = {
                    "name": inp.get("name") or inp.get("id") or inp.get("placeholder") or field_type,
                    "type": field_type,
                    "required": inp.has_attr("required"),
                    "placeholder": inp.get("placeholder", ""),
                    "selector": self._generate_selector(inp),
                }

                # Validation attributes
                if inp.get("minlength"):
                    field_info["minlength"] = inp["minlength"]
                if inp.get("maxlength"):
                    field_info["maxlength"] = inp["maxlength"]
                if inp.get("min"):
                    field_info["min"] = inp["min"]
                if inp.get("max"):
                    field_info["max"] = inp["max"]
                if inp.get("pattern"):
                    field_info["pattern"] = inp["pattern"]
                if inp.get("autocomplete"):
                    field_info["autocomplete"] = inp["autocomplete"]
                if inp.get("aria-label"):
                    field_info["aria_label"] = inp["aria-label"]
                if inp.get("disabled") is not None and inp.has_attr("disabled"):
                    field_info["disabled"] = True
                if inp.get("readonly") is not None and inp.has_attr("readonly"):
                    field_info["readonly"] = True

                # For selects, get options
                if inp.name == "select":
                    options = []
                    for opt in inp.find_all("option"):
                        opt_text = opt.get_text(strip=True)
                        opt_val = opt.get("value", opt_text)
                        if opt_text:
                            options.append({"value": opt_val, "text": opt_text[:100]})
                    field_info["options"] = options[:20]  # cap options

                fields.append(field_info)

            buttons = []
            for btn in form_tag.find_all(["button", "input"]):
                btn_type = btn.get("type", "button").lower()
                if btn_type in ("submit", "button", "reset"):
                    text = btn.get_text(strip=True) or btn.get("value") or btn.get("aria-label") or "Button"
                    if text:
                        buttons.append({
                            "text": text,
                            "type": btn_type,
                            "selector": self._generate_selector(btn),
                        })

            forms.append({
                "name": form_name,
                "action": form_tag.get("action", ""),
                "method": form_tag.get("method", "get").upper(),
                "enctype": form_tag.get("enctype", ""),
                "autocomplete": form_tag.get("autocomplete", ""),
                "fields": fields,
                "buttons": buttons,
            })
        return forms

    # ── Standalone Buttons (outside forms) ──────────────────────────────────

    def _get_standalone_buttons(self, soup: BeautifulSoup) -> list:
        buttons = []
        for btn in soup.find_all(["button", "a", "div", "span"]):
            # Skip if inside a form
            if btn.find_parent("form"):
                continue
            # Check if it's a button-like element
            is_button = (
                btn.name == "button"
                or btn.get("role") == "button"
                or (btn.name == "a" and "btn" in " ".join(btn.get("class", [])).lower())
                or (btn.name in ("div", "span") and btn.get("role") == "button")
            )
            if is_button:
                text = btn.get_text(strip=True)
                if text and len(text) < 100:
                    buttons.append({
                        "text": text,
                        "tag": btn.name,
                        "href": btn.get("href", "") if btn.name == "a" else "",
                        "id": btn.get("id", ""),
                        "selector": self._generate_selector(btn),
                    })
            if len(buttons) >= 30:
                break
        return buttons

    # ── Standalone Inputs (outside forms) ──────────────────────────────────

    def _get_standalone_inputs(self, soup: BeautifulSoup) -> list:
        inputs = []
        for el in soup.find_all(["input", "select", "textarea"]):
            if el.find_parent("form"):
                continue
            
            el_type = el.get("type", el.name).lower()
            if el_type in ("hidden", "submit", "button", "image", "reset"):
                continue
                
            info = {
                "tag": el.name,
                "type": el_type,
                "name": el.get("name") or el.get("id") or el.get("placeholder") or el_type,
                "required": el.has_attr("required"),
                "placeholder": el.get("placeholder", ""),
                "selector": self._generate_selector(el),
            }
            inputs.append(info)
            if len(inputs) >= 30:
                break
        return inputs

    # ── Images ─────────────────────────────────────────────────────────────

    def _get_images(self, soup: BeautifulSoup, base: str) -> list:
        images = []
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
