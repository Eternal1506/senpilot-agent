"""Scrape the Nova Scotia UARB public documents database (FileMaker WebDirect).

Key findings from live DOM analysis:
 - Input fields are custom `div.fm-textarea-prompt .text` elements (not <input>)
 - VAADIN re-renders after each keystroke; coordinate-based clicking is most reliable
 - "GO GET IT" does not trigger a Playwright download event
 - "Preview" button causes the browser to load a streaming PDF URL:
       https://uarb.novascotia.ca/Streaming/Additional_1/<HASH>.pdf?R
   We capture that URL from response events and download it directly.
 - Public documents do not require session cookies for download.
"""
import io
import re
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

# Windows console default is CP1252 which can't render Playwright's → arrows
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import config

LONG  = 60_000
MED   = 30_000
SHORT = 8_000


@dataclass
class MatterResult:
    matter: str
    requested_type: str
    counts: Dict[str, int] = field(default_factory=dict)
    title: Optional[str] = None
    type_: Optional[str] = None
    category: Optional[str] = None
    date_received: Optional[str] = None
    date_final: Optional[str] = None
    downloaded_files: List[Path] = field(default_factory=list)
    zip_path: Optional[Path] = None
    error: Optional[str] = None

    @property
    def total_files(self) -> int:
        return sum(self.counts.values())

    @property
    def requested_total(self) -> int:
        return self.counts.get(self.requested_type, 0)


# ── helpers ──────────────────────────────────────────────────────────────────

def _unique_path(directory: Path, fname: str) -> Path:
    dest = directory / fname
    counter = 1
    while dest.exists():
        dest = directory / f"{counter}_{fname}"
        counter += 1
    return dest


def _read_tab_counts(page: Page) -> Dict[str, int]:
    """Read document counts from tab labels like 'Other Documents - 42'."""
    counts: Dict[str, int] = {}
    for dt in config.DOC_TYPES:
        counts[dt] = 0
        try:
            el = page.get_by_text(re.compile(rf"{re.escape(dt)}\s*-\s*\d+")).first
            txt = el.inner_text(timeout=SHORT)
            m = re.search(r"-\s*(\d+)", txt)
            if m:
                counts[dt] = int(m.group(1))
        except Exception:
            pass
    return counts


def _read_metadata(page: Page, result: MatterResult) -> None:
    """Extract matter header metadata from full page inner text.

    All fields degrade to None so a missing field never breaks the download.
    """
    try:
        body = page.locator("body").inner_text(timeout=SHORT)
    except Exception:
        return

    # Title: "Halifax Regional Water Commission - Windsor Street ... - $69,275,000"
    m = re.search(r"([A-Z][^\n$]{5,300} - \$[\d,]+)", body)
    if m:
        result.title = m.group(1).strip()

    # Dates: first two MM/DD/YYYY = Date Received, Date Final
    dates = re.findall(r"\b(\d{2}/\d{2}/\d{4})\b", body)
    if len(dates) >= 1:
        result.date_received = dates[0]
    if len(dates) >= 2:
        result.date_final = dates[1]

    # Type / Category: scan text that follows those label words
    type_m = re.search(r"\bType\b\s*[\t\n]+\s*([^\t\n]+)", body)
    if type_m:
        val = type_m.group(1).strip()
        if val not in ("Category", "Date Received", "Status", "Outcome", "Matter No"):
            result.type_ = val

    cat_m = re.search(r"\bCategory\b\s*[\t\n]+\s*([^\t\n]+)", body)
    if cat_m:
        val = cat_m.group(1).strip()
        if val not in ("Type", "Date Received", "Status", "Outcome", "Matter No"):
            result.category = val


def _zip_files(result: MatterResult) -> None:
    if not result.downloaded_files:
        return
    safe_type = result.requested_type.replace(" ", "_")
    zip_path = config.DOWNLOAD_DIR / f"{result.matter}_{safe_type}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in result.downloaded_files:
            if f.exists():
                zf.write(f, arcname=f.name)
    result.zip_path = zip_path
    print(f"[scraper] wrote {zip_path.name} ({len(result.downloaded_files)} files)")


# ── navigation ────────────────────────────────────────────────────────────────

def _navigate_to_matter(page: Page, matter: str) -> None:
    """Navigate to a specific matter via 'Go Directly to Matter'.

    FileMaker WebDirect uses custom div elements (not <input>) and VAADIN
    re-renders the DOM after each keystroke. Coordinate-based clicking is the
    most reliable approach.

    Strategy:
      1. Capture bounding boxes while the DOM is fresh (no prior interaction).
      2. Click the input center by mouse coordinate.
      3. Type with a delay so VAADIN processes each keydown event.
      4. Click the Search button closest to the input by coordinate.
    """
    print(f"[scraper] loading {config.UARB_URL}", flush=True)
    page.goto(config.UARB_URL, wait_until="domcontentloaded")
    page.wait_for_selector(".fm-textarea-prompt", timeout=LONG)

    # ── 1. Capture bounding boxes while DOM is stable ─────────────────────────
    input_bb = page.locator(".fm-textarea-prompt").first.bounding_box()
    if not input_bb:
        raise RuntimeError("Could not find the 'Go Directly to Matter' input field")
    input_cx = input_bb["x"] + input_bb["width"] / 2
    input_cy = input_bb["y"] + input_bb["height"] / 2

    # Find the Search button closest to the input field
    btns = page.get_by_role("button", name=re.compile(r"^Search$", re.IGNORECASE))
    search_bb = None
    min_dist = float("inf")
    for i in range(btns.count()):
        bb = btns.nth(i).bounding_box()
        if not bb:
            continue
        dist = (((bb["x"] + bb["width"] / 2) - input_cx) ** 2 +
                ((bb["y"] + bb["height"] / 2) - input_cy) ** 2) ** 0.5
        if dist < min_dist:
            min_dist = dist
            search_bb = bb
    if not search_bb:
        raise RuntimeError("Could not find the Search button")

    # ── 2. Click the input and type the matter number ─────────────────────────
    page.mouse.click(input_cx, input_cy)
    page.wait_for_timeout(400)
    # delay=200ms: lets VAADIN register each keystroke without racing
    page.keyboard.type(matter, delay=200)
    page.wait_for_timeout(400)

    # ── 3. Click Search by coordinate ─────────────────────────────────────────
    search_cx = search_bb["x"] + search_bb["width"] / 2
    search_cy = search_bb["y"] + search_bb["height"] / 2
    page.mouse.click(search_cx, search_cy)

    # ── 4. Wait for matter page (tabs appear) ─────────────────────────────────
    page.wait_for_selector("text=Exhibits -", timeout=LONG)
    print(f"[scraper] matter page loaded for {matter}", flush=True)


# ── download ─────────────────────────────────────────────────────────────────

def _click_doc_tab(page: Page, doc_type: str) -> None:
    """Click the document-type tab and wait for the document list to appear."""
    tab = page.get_by_text(re.compile(rf"{re.escape(doc_type)}\s*-\s*\d+")).first
    tab.click()
    try:
        page.wait_for_selector("text=Preview", timeout=LONG)
        # Extra settle time so VAADIN flushes any pending retries from the
        # previous route.abort() before the next route handler is registered.
        page.wait_for_timeout(1500)
    except PWTimeout:
        print(f"[scraper] no Preview buttons appeared for tab '{doc_type}'", flush=True)


def _download_doc_via_preview(
    page: Page, btn_index: int, dest_prefix: str, seen_hashes: set
) -> Optional[Path]:
    """Click the nth Preview button, capture the streaming URL, download it.

    VAADIN sometimes replays the previously-aborted streaming request right
    after a tab re-click, so the route handler may receive that stale request
    BEFORE the actual new-document request.  We:
      1. Collect ALL streaming URLs that arrive in a 3-second window.
      2. Skip any URL whose content-hash we've already downloaded.
      3. Use the last remaining URL (most recently arrived = current doc).
    """
    captured_urls: List[str] = []

    def handle_route(route):
        url = route.request.url
        if "/Streaming/" in url and ".pdf" in url:
            captured_urls.append(url)
            route.abort()
        else:
            route.continue_()

    page.route("**", handle_route)
    try:
        previews = page.get_by_text(re.compile(r"^Preview$", re.IGNORECASE))
        previews.nth(btn_index).click()

        # Wait at least 3 s to let VAADIN retries fire before reading results.
        # Keep waiting beyond 3 s until settled (no new URL for 1.5 s).
        start = time.time()
        last_count = 0
        while time.time() - start < 3 or time.time() - start < 15:
            page.wait_for_timeout(300)
            if time.time() - start >= 3:
                if len(captured_urls) == last_count and captured_urls:
                    break
            last_count = len(captured_urls)

        if not captured_urls:
            print(f"[scraper] doc {btn_index + 1}: no PDF URL captured", flush=True)
            return None

        # Pick the last URL whose hash hasn't been downloaded yet
        pdf_url = None
        for url in reversed(captured_urls):
            h = url.split("/")[-1].split("?")[0]
            if h not in seen_hashes:
                pdf_url = url
                seen_hashes.add(h)
                break
        if pdf_url is None:
            print(f"[scraper] doc {btn_index + 1}: all captured URLs already seen", flush=True)
            return None

        print(f"[scraper] doc {btn_index + 1}: {pdf_url[:80]}...", flush=True)

        # Download via page.request (uses the browser session / cookies)
        fname = pdf_url.split("/")[-1].split("?")[0] or f"{dest_prefix}.pdf"
        dest = _unique_path(config.DOWNLOAD_DIR, fname)
        try:
            resp = page.request.get(pdf_url, timeout=120_000)
            if resp.ok:
                dest.write_bytes(resp.body())
                return dest
            print(f"[scraper] request returned HTTP {resp.status}", flush=True)
        except Exception as e:
            print(f"[scraper] download failed: {str(e).encode('ascii', errors='replace').decode()}", flush=True)
        return None

    finally:
        page.unroute("**", handle_route)


# ── main entry point ──────────────────────────────────────────────────────────

def fetch_matter(matter: str, doc_type: str) -> MatterResult:
    result = MatterResult(matter=matter, requested_type=doc_type)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)
        # Set viewport to 1280px — required for coordinate-based clicking to
        # align with the WebDirect layout (designed for 1280px wide).
        ctx = browser.new_context(
            accept_downloads=True,
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        page.set_default_timeout(LONG)
        try:
            # ── 1. Navigate to the matter ────────────────────────────────────
            _navigate_to_matter(page, matter)

            # ── 2. Read counts + metadata ─────────────────────────────────────
            result.counts = _read_tab_counts(page)
            _read_metadata(page, result)
            print(f"[scraper] counts: {result.counts}", flush=True)
            print(f"[scraper] title:  {result.title}", flush=True)

            if result.requested_total == 0:
                print(f"[scraper] {doc_type}: 0 documents — nothing to download", flush=True)
                return result

            # ── 3. Navigate to the requested tab ─────────────────────────────
            _click_doc_tab(page, doc_type)

            # ── 4. Download up to MAX_DOCS via Preview ────────────────────────
            n = min(config.MAX_DOCS, result.requested_total)
            print(f"[scraper] downloading {n}/{result.requested_total} {doc_type}", flush=True)
            seen_hashes: set = set()
            for i in range(n):
                # VAADIN clears the doc list after each Preview click (even though
                # route.abort() keeps the URL on UARB). Re-click the tab before each
                # download to restore the full list, then click Preview[i].
                # (For i=0 the tab was already clicked by _click_doc_tab above.)
                if i > 0:
                    _click_doc_tab(page, doc_type)
                dest = _download_doc_via_preview(page, i, f"{matter}_{i + 1}", seen_hashes)
                if dest:
                    result.downloaded_files.append(dest)
                    print(f"[scraper] [{i + 1}/{n}] saved {dest.name}", flush=True)
                else:
                    print(f"[scraper] [{i + 1}/{n}] failed (skipping)", flush=True)

            # ── 5. Zip ────────────────────────────────────────────────────────
            _zip_files(result)

        except Exception as e:
            result.error = str(e)
            print(f"[scraper] fatal: {e}", flush=True)
        finally:
            ctx.close()
            browser.close()
    return result
