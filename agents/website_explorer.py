import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


class WebsiteExplorerAgent:
    """
    Powerful website explorer that crawls pages and extracts
    comprehensive, structured data for QA test generation.
    Works reliably across all types of websites.
    """

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    TIMEOUT = 15          # seconds per request
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

        while queue and len(visited) < self.MAX_PAGES:
            url, current_depth = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)

            page_data = self._scrape_page(url)
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

    def _scrape_page(self, url: str) -> dict:
        html = None
        status_code = None
        error_msg = None

        for attempt in range(self.MAX_RETRIES):
            try:
                resp = requests.get(
                    url,
                    headers=self.HEADERS,
                    timeout=self.TIMEOUT,
                    allow_redirects=True,
                    verify=True,
                )
                status_code = resp.status_code
                resp.raise_for_status()
                html = resp.text
                break
            except requests.exceptions.SSLError:
                # Retry without SSL verification
                try:
                    resp = requests.get(
                        url,
                        headers=self.HEADERS,
                        timeout=self.TIMEOUT,
                        allow_redirects=True,
                        verify=False,
                    )
                    status_code = resp.status_code
                    resp.raise_for_status()
                    html = resp.text
                    break
                except requests.RequestException as exc:
                    error_msg = f"SSL and non-SSL both failed: {exc}"
            except requests.RequestException as exc:
                error_msg = str(exc)
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
            if not src:
                continue
            full_src = urljoin(base, src)
            images.append({
                "src": full_src,
                "alt": img.get("alt", ""),
                "has_alt": bool(img.get("alt")),
                "width": img.get("width", ""),
                "height": img.get("height", ""),
                "loading": img.get("loading", ""),
            })
            if len(images) >= self.MAX_IMAGES:
                break
        return images

    # ── Links (enhanced) ───────────────────────────────────────────────────

    def _get_links(self, soup: BeautifulSoup, base: str, base_domain: str) -> list:
        seen = set()
        links = []
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            full_href = urljoin(base, href)
            if full_href not in seen:
                seen.add(full_href)
                link_domain = urlparse(full_href).netloc
                links.append({
                    "text": text or href,
                    "href": full_href,
                    "is_internal": link_domain == base_domain,
                    "rel": a.get("rel", []),
                    "target": a.get("target", ""),
                })
            if len(links) >= self.MAX_LINKS:
                break
        return links

    # ── Navigation Structure ───────────────────────────────────────────────

    def _get_navigations(self, soup: BeautifulSoup, base: str) -> list:
        navs = []
        for nav in soup.find_all("nav"):
            nav_links = []
            for a in nav.find_all("a", href=True):
                text = a.get_text(strip=True)
                if text:
                    nav_links.append({
                        "text": text,
                        "href": urljoin(base, a["href"]),
                    })
            label = nav.get("aria-label") or nav.get("id") or "navigation"
            navs.append({
                "label": label,
                "links": nav_links[:30],
            })
        return navs

    # ── Tables ─────────────────────────────────────────────────────────────

    def _get_tables(self, soup: BeautifulSoup) -> list:
        tables = []
        for table in soup.find_all("table"):
            headers = []
            for th in table.find_all("th"):
                text = th.get_text(strip=True)
                if text:
                    headers.append(text)
            rows = len(table.find_all("tr"))
            tables.append({
                "id": table.get("id", ""),
                "class": " ".join(table.get("class", [])),
                "headers": headers[:20],
                "row_count": rows,
                "has_caption": bool(table.find("caption")),
            })
            if len(tables) >= 10:
                break
        return tables

    # ── Interactive Elements ───────────────────────────────────────────────

    def _get_interactive_elements(self, soup: BeautifulSoup) -> list:
        elements = []

        # Dropdowns (standalone selects not in forms)
        for sel in soup.find_all("select"):
            if not sel.find_parent("form"):
                options_count = len(sel.find_all("option"))
                elements.append({
                    "type": "dropdown",
                    "name": sel.get("name") or sel.get("id") or "select",
                    "options_count": options_count,
                })

        # Details/Summary (accordions)
        for det in soup.find_all("details"):
            summary = det.find("summary")
            elements.append({
                "type": "accordion",
                "summary": summary.get_text(strip=True) if summary else "",
                "open": det.has_attr("open"),
            })

        # Dialog elements (modals)
        for dlg in soup.find_all("dialog"):
            elements.append({
                "type": "dialog/modal",
                "id": dlg.get("id", ""),
                "open": dlg.has_attr("open"),
            })

        # Tabs (role="tab")
        tabs = soup.find_all(attrs={"role": "tab"})
        if tabs:
            tab_texts = [t.get_text(strip=True) for t in tabs[:10]]
            elements.append({
                "type": "tab_group",
                "tabs": tab_texts,
                "count": len(tabs),
            })

        # Tooltips / popovers
        for el in soup.find_all(attrs={"data-toggle": "tooltip"}):
            elements.append({
                "type": "tooltip",
                "text": el.get("title") or el.get_text(strip=True)[:80],
            })
            if len(elements) >= 30:
                break

        return elements[:30]

    # ── Media ──────────────────────────────────────────────────────────────

    def _get_media(self, soup: BeautifulSoup, base: str) -> list:
        media = []
        for video in soup.find_all("video"):
            src = video.get("src") or ""
            source = video.find("source")
            if source:
                src = source.get("src", src)
            media.append({
                "type": "video",
                "src": urljoin(base, src) if src else "",
                "has_controls": video.has_attr("controls"),
                "autoplay": video.has_attr("autoplay"),
            })

        for audio in soup.find_all("audio"):
            src = audio.get("src") or ""
            source = audio.find("source")
            if source:
                src = source.get("src", src)
            media.append({
                "type": "audio",
                "src": urljoin(base, src) if src else "",
            })

        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            media.append({
                "type": "iframe",
                "src": src,
                "title": iframe.get("title", ""),
                "width": iframe.get("width", ""),
                "height": iframe.get("height", ""),
            })
        return media[:20]

    # ── Tech Stack Detection ─────────────────────────────────────────────

    def _detect_tech_stack(self, html: str, soup: BeautifulSoup) -> list:
        detected = []
        html_lower = html.lower()

        for tech, patterns in self.TECH_SIGNATURES.items():
            for pattern in patterns:
                if re.search(pattern, html_lower):
                    detected.append(tech)
                    break

        # Check script sources for additional signals
        for script in soup.find_all("script", src=True):
            src = script.get("src", "").lower()
            if "react" in src and "React" not in detected:
                detected.append("React")
            if "vue" in src and "Vue.js" not in detected:
                detected.append("Vue.js")
            if "angular" in src and "Angular" not in detected:
                detected.append("Angular")
            if "jquery" in src and "jQuery" not in detected:
                detected.append("jQuery")
            if "bootstrap" in src and "Bootstrap" not in detected:
                detected.append("Bootstrap")

        return sorted(set(detected))

    # ── Accessibility Audit ─────────────────────────────────────────────

    def _audit_accessibility(self, soup: BeautifulSoup) -> list:
        issues = []

        # Check lang attribute
        html_tag = soup.find("html")
        if not html_tag or not html_tag.get("lang"):
            issues.append({
                "type": "missing_lang",
                "severity": "high",
                "message": "HTML tag is missing 'lang' attribute",
            })

        # Check images without alt text
        imgs_without_alt = 0
        for img in soup.find_all("img"):
            if not img.get("alt") and not img.get("role") == "presentation":
                imgs_without_alt += 1
        if imgs_without_alt > 0:
            issues.append({
                "type": "missing_alt",
                "severity": "high",
                "message": f"{imgs_without_alt} image(s) missing 'alt' attribute",
                "count": imgs_without_alt,
            })

        # Check form inputs without labels
        inputs_without_labels = 0
        for inp in soup.find_all(["input", "select", "textarea"]):
            inp_type = inp.get("type", "text").lower()
            if inp_type in ("hidden", "submit", "button", "image", "reset"):
                continue
            inp_id = inp.get("id")
            has_label = False
            if inp_id:
                label = soup.find("label", attrs={"for": inp_id})
                if label:
                    has_label = True
            if not has_label and not inp.get("aria-label") and not inp.get("aria-labelledby"):
                # Check if wrapped in a label
                if not inp.find_parent("label"):
                    inputs_without_labels += 1
        if inputs_without_labels > 0:
            issues.append({
                "type": "missing_labels",
                "severity": "high",
                "message": f"{inputs_without_labels} form input(s) missing associated labels",
                "count": inputs_without_labels,
            })

        # Check heading hierarchy
        headings = [int(h.name[1]) for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])]
        h1_count = headings.count(1)
        if h1_count == 0:
            issues.append({
                "type": "no_h1",
                "severity": "medium",
                "message": "Page has no H1 heading",
            })
        elif h1_count > 1:
            issues.append({
                "type": "multiple_h1",
                "severity": "low",
                "message": f"Page has {h1_count} H1 headings (recommend only 1)",
            })

        # Check for skipped heading levels
        for i in range(1, len(headings)):
            if headings[i] > headings[i-1] + 1:
                issues.append({
                    "type": "skipped_heading",
                    "severity": "medium",
                    "message": f"Heading level skipped: h{headings[i-1]} → h{headings[i]}",
                })
                break

        # Check for missing skip navigation
        skip_link = soup.find("a", href="#main") or soup.find("a", href="#content") or soup.find("a", class_="skip-link")
        main_content = soup.find("main") or soup.find(id="main-content") or soup.find(id="content")
        if not skip_link and not main_content:
            issues.append({
                "type": "no_skip_nav",
                "severity": "low",
                "message": "No skip navigation link or <main> landmark found",
            })

        # Check for links without text
        empty_links = 0
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            if not text and not a.get("aria-label") and not a.find("img"):
                empty_links += 1
        if empty_links > 0:
            issues.append({
                "type": "empty_links",
                "severity": "medium",
                "message": f"{empty_links} link(s) have no text or aria-label",
                "count": empty_links,
            })

        # Check for buttons without accessible names
        unnamed_buttons = 0
        for btn in soup.find_all("button"):
            text = btn.get_text(strip=True)
            if not text and not btn.get("aria-label") and not btn.get("title"):
                unnamed_buttons += 1
        if unnamed_buttons > 0:
            issues.append({
                "type": "unnamed_buttons",
                "severity": "medium",
                "message": f"{unnamed_buttons} button(s) have no accessible name",
                "count": unnamed_buttons,
            })

        return issues

    # ── Page Statistics ────────────────────────────────────────────────────

    def _get_statistics(self, soup: BeautifulSoup) -> dict:
        text = soup.get_text(separator=" ", strip=True)
        words = len(text.split())
        total_elements = len(soup.find_all(True))

        return {
            "word_count": words,
            "total_elements": total_elements,
            "total_forms": len(soup.find_all("form")),
            "total_links": len(soup.find_all("a", href=True)),
            "total_images": len(soup.find_all("img")),
            "total_scripts": len(soup.find_all("script")),
            "total_stylesheets": len(soup.find_all("link", rel="stylesheet")),
            "has_footer": bool(soup.find("footer")),
            "has_header": bool(soup.find("header")),
            "has_nav": bool(soup.find("nav")),
            "has_main": bool(soup.find("main")),
            "has_aside": bool(soup.find("aside")),
        }

    # ══════════════════════════════════════════════════════════════════════
    # SITE-WIDE SUMMARY
    # ══════════════════════════════════════════════════════════════════════

    def _build_site_summary(self, pages: list) -> dict:
        total_forms = sum(len(p.get("forms", [])) for p in pages)
        total_fields = sum(
            len(f.get("fields", []))
            for p in pages
            for f in p.get("forms", [])
        )
        total_links = sum(len(p.get("links", [])) for p in pages)
        total_images = sum(len(p.get("images", [])) for p in pages)
        total_buttons = sum(len(p.get("buttons", [])) for p in pages)
        total_standalone = sum(len(p.get("standalone_inputs", [])) for p in pages)
        total_tables = sum(len(p.get("tables", [])) for p in pages)
        total_media = sum(len(p.get("media", [])) for p in pages)
        total_navs = sum(len(p.get("navigations", [])) for p in pages)
        total_interactive = sum(len(p.get("interactive_elements", [])) for p in pages)

        # Aggregate tech stack across pages
        all_tech = set()
        for p in pages:
            all_tech.update(p.get("tech_stack", []))

        # Aggregate accessibility issues
        all_a11y = []
        for p in pages:
            for issue in p.get("accessibility_issues", []):
                issue_copy = dict(issue)
                issue_copy["page_url"] = p.get("url", "")
                all_a11y.append(issue_copy)

        # Count error pages
        error_pages = sum(1 for p in pages if p.get("error"))

        return {
            "total_pages": len(pages),
            "error_pages": error_pages,
            "total_forms": total_forms,
            "total_fields": total_fields,
            "total_links": total_links,
            "total_images": total_images,
            "total_buttons": total_buttons,
            "total_standalone": total_standalone,
            "total_tables": total_tables,
            "total_media": total_media,
            "total_navigations": total_navs,
            "total_interactive_elements": total_interactive,
            "detected_tech_stack": sorted(all_tech),
            "total_accessibility_issues": len(all_a11y),
            "accessibility_issues": all_a11y[:50],  # cap
        }

    # ══════════════════════════════════════════════════════════════════════
    # UTILITIES
    # ══════════════════════════════════════════════════════════════════════

    def _normalise(self, url: str) -> str:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url
