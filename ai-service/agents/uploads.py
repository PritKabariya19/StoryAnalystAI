"""Sample files for upload steps, and a look at pages as a browser shows them."""
import logging

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from agents.url_guard import browser_options, check_url

log = logging.getLogger(__name__)

UPLOAD_SETTLE_MS = 6000  # time a page gets to show its controls after an upload

_TEXT_FILES = {
    "txt": ("text/plain", b"StoryAnalystAI sample file\n"),
    "csv": ("text/csv", b"name,value\nsample,1\n"),
}
_MIME_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/*": "jpg",
               "application/pdf": "pdf", "text/plain": "txt", "text/csv": "csv"}
_SUPPORTED = {"jpg", "jpeg", "png", "pdf", *_TEXT_FILES}

# Buttons that appeared after an upload are found by comparing these lists
_CONTROLS_JS = r"""els => els.map(e => ({
    tag: e.tagName.toLowerCase(), id: e.id,
    text: (e.innerText || e.value || '').trim().slice(0, 60)}))"""
_CONTROLS = "button:visible, [role=button]:visible, input[type=submit]:visible, a[id]:visible"


def accepted_type(accept: str):
    """The first file type in an `accept` attribute that we can create, or None.
    A field with no `accept` takes anything, so it gets a JPEG."""
    if not accept.strip():
        return "jpg"
    for item in accept.lower().split(","):
        item = item.strip()
        kind = _MIME_TYPES.get(item, item.lstrip("."))
        if kind in _SUPPORTED:
            return kind
    return None


def sample_file(browser_context, kind: str, number: int = 1) -> dict:
    """A small, real file of the given type, in the form set_input_files takes."""
    name = f"sample{number}.{kind}"
    if kind in _TEXT_FILES:
        mime, data = _TEXT_FILES[kind]
        return {"name": name, "mimeType": mime, "buffer": data}
    page = browser_context.new_page()
    try:
        page.set_viewport_size({"width": 600, "height": 400})
        page.set_content(f"<h1>StoryAnalystAI sample {number}</h1>")
        if kind == "pdf":  # headless Chromium only
            return {"name": name, "mimeType": "application/pdf", "buffer": page.pdf()}
        image = "png" if kind == "png" else "jpeg"
        return {"name": name, "mimeType": f"image/{image}", "buffer": page.screenshot(type=image)}
    finally:
        page.close()


def look_at_page(url: str):
    """Open the page in Chromium. Returns None if that fails, else its HTML after
    scripts have run, its upload fields, and the controls that appear once a
    sample file is uploaded. Nothing is clicked."""
    try:
        check_url(url)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**browser_options())
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightError:
                pass
            seen = {"html": page.content(), "file_uploads": [], "after_upload": []}
            inputs = page.locator("input[type=file]")
            seen["file_uploads"] = [
                {"accept": el.get_attribute("accept") or "",
                 "multiple": el.get_attribute("multiple") is not None}
                for el in inputs.all()[:5]]
            kind = seen["file_uploads"] and accepted_type(seen["file_uploads"][0]["accept"])
            if kind:
                controls = lambda: page.locator(_CONTROLS).evaluate_all(_CONTROLS_JS)
                before = controls()
                inputs.first.set_input_files(sample_file(context, kind))
                page.wait_for_timeout(UPLOAD_SETTLE_MS)
                seen["after_upload"] = [c for c in controls() if c not in before][:15]
            return seen
    except Exception:
        log.warning("Could not open %s in the browser; using its plain HTML.", url, exc_info=True)
        return None
