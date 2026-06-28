# Senpilot Regulatory Agent

Email an agent a matter number + document type → it fetches up to 10 documents
from the Nova Scotia UARB public database, ZIPs them, and replies with a summary.

```
user emails "Other Documents from M12205?"
         │
         ▼
  ┌────────────┐   ┌──────────────┐   ┌──────────────────┐   ┌────────────┐
  │ Gmail API  │──▶│request_parser│──▶│ scraper.py       │──▶│ summary.py │
  │ (poll/send)│   │ matter+type  │   │ Playwright on    │   │ + ZIP      │
  └────────────┘   └──────────────┘   │ FileMaker WebDir.│   └────────────┘
                                       └──────────────────┘
```

## Why Playwright?

The UARB site runs FileMaker WebDirect (`/fmi/webd/` URL). It's a thick-client
JS app with dynamic element IDs — plain `requests` / `BeautifulSoup` won't
touch it. Scripted Playwright with text-based locators is the reliable choice.

## Setup

### 1. Clone and install

```bash
git clone <your-repo-url>
cd senpilot-agent
pip install -r requirements.txt
playwright install chromium
```

### 2. Create a Gmail account for the agent

1. Make a **new throwaway Gmail** (e.g. `your-agent@gmail.com`). This becomes
   the email address you hand to the graders.
2. In [Google Cloud Console](https://console.cloud.google.com/):
   - Create a project → **Enable the Gmail API**
   - **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Download the JSON → save as `credentials.json` in this folder
3. Under **OAuth consent screen → Test users**, add your own Gmail address
   (the one you'll use to test-email the agent).

### 3. Configure

```bash
cp .env.example .env
# edit .env — set HEADLESS=false while developing
```

### 4. Authorise Gmail (one-time)

```bash
python run.py --once
```

A browser window opens for OAuth consent. Click through, then `token.json`
is written. Subsequent runs are headless.

### 5. Tune the Playwright selectors (the real work)

```bash
# Watch the browser and record exact selectors
HEADLESS=false python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=False)
    page = b.new_context().new_page()
    page.goto('https://uarb.novascotia.ca/fmi/webd/UARB15')
    input('Navigate through the site, then press Enter to close...')
    b.close()
"
```

Or use codegen:
```bash
playwright codegen https://uarb.novascotia.ca/fmi/webd/UARB15
```

Copy any generated selectors into `scraper.py` at the lines marked `# TUNE:`.

### 6. Run

```bash
python run.py           # poll loop — runs forever
python run.py --once    # process current unread inbox, then exit
```

## File structure

| File | Purpose |
|------|---------|
| `run.py` | Entrypoint — poll loop |
| `agent.py` | Orchestration — tie all components together |
| `request_parser.py` | Extract matter number + doc type from email text |
| `scraper.py` | Playwright scraper for the UARB WebDirect site |
| `summary.py` | Build the reply email body from scrape results |
| `gmail_client.py` | Gmail API — poll, reply, mark-read |
| `config.py` | All config pulled from `.env` |

## What I'd add with more time

- Retry with exponential back-off for WebDirect timeouts
- Dedup incoming emails (hash `message-id`) so re-sends aren't processed twice
- Preview-tab fallback: some WebDirect installs open files in a viewer; the
  current fallback handles this but could be made more robust
- Persist scrape results to SQLite for the searchable database layer
