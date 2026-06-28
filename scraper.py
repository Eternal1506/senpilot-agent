"""Scrape the Nova Scotia UARB public documents database (FileMaker WebDirect).

FileMaker WebDirect is a stateful JS app — plain HTTP won't work. Strategy:
 - Locate elements by visible text / placeholder / role (stable across re-renders)
 - Wait explicitly for content rather than relying on networkidle alone
 - All metadata fields degrade to None on failure so downloads still complete

TUNING: Run `playwright codegen https://uarb.novascotia.ca/fmi/webd/UARB15`
and click through the full flow once. Paste the generated selectors at any
line marked  # TUNE:  below. Develop with HEADLESS=false in your .env.
"""
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from playwright.sync_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
    sync_playwright,
)

import config

LONG = 60_000   # WebDirect is genuinely slow
MED  = 30_000
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

def _read_tab_counts(page: Page) -> Dict[str, int]:
    """Tabs are labelled like 'Exhibits - 13' / 'Other Documents - 21'."""
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
    """Extract matter header metadata from the rendered page text.

    Primary approach: pull inner_text() from body and parse with regex.
    This is the most reliable strategy for FileMaker WebDirect because the DOM
    uses dynamic IDs that change across sessions.
    """
    try:
        body = page.locator("body").inner_text(timeout=SHORT)
    except Exception:
        return

    # Title: "Halifax Regional Water Commission - Windsor Street … - $69,275,000"
    m = re.search(r"([A-Z][^\n$]{5,300} - \$[\d,]+)", body)
    if m:
        result.title = m.group(1).strip()

    # Dates: first two MM/DD/YYYY occurrences = "Date Received", "Date Final Submissions"
    dates = re.findall(r"\b(\d{2}/\d{2}/\d{4})\b", body)
    if len(dates) >= 1:
        result.date_received = dates[0]
    if len(dates) >= 2:
        result.date_final = dates[1]

    # Type and Category appear as values following their label text.
    # WebDirect inner_text() separates layout columns with tabs or newlines.
    type_m = re.search(r"\bType\b\s*[\t\n]+\s*([^\t\n]+)", body)
    if type_m:
        val = type_m.group(1).strip()
        # Exclude other known labels that can follow "Type" accidentally
        if val and val not in ("Category", "Date Received", "Status", "Outcome", "Matter No"):
            result.type_ = val

    cat_m = re.search(r"\bCategory\b\s*[\t\n]+\s*([^\t\n]+)", body)
    if cat_m:
        val = cat_m.group(1).strip()
        if val and val not in ("Type", "Date Received", "Status", "Outcome", "Matter No"):
            result.category = val


# ── download helper ───────────────────────────────────────────────────────────

def _try_download(
    page: Page,
    ctx: BrowserContext,
    btn_index: int,
    dest_prefix: str,
) -> Optional[Path]:
    """Click the nth 'GO GET IT' button; handle both direct-download and new-tab."""
    btns = page.get_by_text(re.compile(r"GO GET IT", re.IGNORECASE))

    # Case A: button fires a download directly
    try:
        btn = btns.nth(btn_index)
        with page.expect_download(timeout=MED) as dl_info:
            btn.click()
        dl = dl_info.value
        fname = dl.suggested_filename or f"{dest_prefix}.pdf"
        dest = _unique_path(config.DOWNLOAD_DIR, fname)
        dl.save_as(dest)
        return dest
    except PWTimeout:
        pass  # might have opened a new tab instead
    except Exception as e:
        print(f"[scraper] download btn {btn_index + 1} error: {e}")
        return None

    # Case B: a new tab/page opened (preview viewer pattern)
    others = [p for p in ctx.pages if p != page]
    if not others:
        return None
    new_tab = others[-1]
    url = new_tab.url
    new_tab.close()

    if not url or not url.startswith("http") or url == config.UARB_URL:
        return None

    try:
        dl_page = ctx.new_page()
        with dl_page.expect_download(timeout=MED) as dl_info2:
            dl_page.goto(url, wait_until="commit")
        dl = dl_info2.value
        fname = dl.suggested_filename or f"{dest_prefix}.pdf"
        dest = _unique_path(config.DOWNLOAD_DIR, fname)
        dl.save_as(dest)
        dl_page.close()
        return dest
    except Exception as e:
        print(f"[scraper] new-tab download fallback failed: {e}")
        return None


def _unique_path(directory: Path, fname: str) -> Path:
    dest = directory / fname
    counter = 1
    while dest.exists():
        dest = directory / f"{counter}_{fname}"
        counter += 1
    return dest


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


# ── main entry point ──────────────────────────────────────────────────────────

def fetch_matter(matter: str, doc_type: str) -> MatterResult:
    result = MatterResult(matter=matter, requested_type=doc_type)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()
        page.set_default_timeout(LONG)
        try:
            # ── 1. Load page and wait for search box ─────────────────────────
            print(f"[scraper] navigating to {config.UARB_URL}")
            page.goto(config.UARB_URL, wait_until="domcontentloaded")

            # TUNE: adjust selector if placeholder text differs
            page.wait_for_selector(
                "input[placeholder*='M'], input[placeholder*='Matter'], input[placeholder*='matter']",
                timeout=LONG,
            )

            # ── 2. Fill "Go Directly to Matter" box and search ───────────────
            # TUNE: placeholder is "eg M01234"
            box = page.get_by_placeholder(
                re.compile(r"M\d+|Matter|matter", re.IGNORECASE)
            ).first
            box.click()
            box.fill(matter)
            print(f"[scraper] filled matter number: {matter}")

            # TUNE: the Search button adjacent to the input
            search_btn = page.get_by_role(
                "button", name=re.compile(r"^Search$", re.IGNORECASE)
            ).first
            search_btn.click()

            # ── 3. Wait for the matter page to render ────────────────────────
            # Safest signal: the tab row with "Exhibits - " appears
            page.wait_for_selector("text=Exhibits -", timeout=LONG)
            print("[scraper] matter page loaded")

            # ── 4. Read tab counts and metadata ──────────────────────────────
            result.counts = _read_tab_counts(page)
            _read_metadata(page, result)
            print(f"[scraper] counts: {result.counts}")
            print(f"[scraper] title:  {result.title}")

            if result.requested_total == 0:
                print(f"[scraper] {doc_type}: 0 documents — skipping download")
                return result

            # ── 5. Click the requested document-type tab ─────────────────────
            # TUNE: tab text is exactly "Other Documents - 21"
            tab = page.get_by_text(
                re.compile(rf"{re.escape(doc_type)}\s*-\s*\d+")
            ).first
            tab.click()

            # Wait for at least one GO GET IT button to appear
            try:
                page.wait_for_selector("text=GO GET IT", timeout=LONG)
            except PWTimeout:
                print(f"[scraper] no GO GET IT buttons appeared for tab '{doc_type}'")
                return result

            # ── 6. Download up to MAX_DOCS ────────────────────────────────────
            n = min(config.MAX_DOCS, result.requested_total)
            print(f"[scraper] downloading {n} of {result.requested_total} {doc_type}")
            for i in range(n):
                dest = _try_download(page, ctx, i, f"{matter}_{i + 1}")
                if dest:
                    result.downloaded_files.append(dest)
                    print(f"[scraper] [{i + 1}/{n}] saved {dest.name}")
                else:
                    print(f"[scraper] [{i + 1}/{n}] failed (skipping)")

            # ── 7. Zip everything ─────────────────────────────────────────────
            _zip_files(result)

        except Exception as e:
            result.error = str(e)
            print(f"[scraper] fatal: {e}")
        finally:
            ctx.close()
            browser.close()
    return result
