"""Dump the UARB page HTML so we can find the real input selectors."""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    print("Loading page...")
    page.goto("https://uarb.novascotia.ca/fmi/webd/UARB15", wait_until="domcontentloaded")

    # Give WebDirect JS time to render
    page.wait_for_timeout(5000)

    # Save the full HTML
    html = page.content()
    with open("page_dump.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved page_dump.html ({len(html)} bytes)")

    # Also print visible text
    text = page.locator("body").inner_text()
    print("\n--- VISIBLE TEXT ---")
    print(text[:3000])
    print("--- END ---")

    # List all input elements and their attributes
    inputs = page.locator("input").all()
    print(f"\nFound {len(inputs)} <input> elements:")
    for i, inp in enumerate(inputs):
        try:
            attrs = page.evaluate("el => Object.fromEntries([...el.attributes].map(a => [a.name, a.value]))", inp.element_handle())
            print(f"  [{i}] {attrs}")
        except Exception as e:
            print(f"  [{i}] error: {e}")

    input("\nPress Enter to close browser...")
    browser.close()
