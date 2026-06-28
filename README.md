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

## Why Playwright? Why coordinate-based clicking?

The UARB site runs **FileMaker WebDirect** (`/fmi/webd/UARB15`), a thick-client
VAADIN framework app. Key constraints discovered through live DOM analysis:

- Zero `<input>` elements — fields are `div.fm-textarea-prompt` VAADIN widgets
- VAADIN re-renders the entire DOM after each keystroke; CSS-class locators go
  stale immediately — **coordinate-based clicking** is the only stable approach
- The "Preview" button triggers a browser navigation to a streaming PDF URL
  (`/Streaming/Additional_1/<HASH>.pdf`) but does NOT fire a Playwright
  `download` event and doesn't open a new tab
- **Download strategy**: `page.route("**", handler)` intercepts the PDF
  navigation, aborts it (keeping the browser on WebDirect), then downloads the
  streaming URL directly via `page.request.get()` with session cookies
- VAADIN replays the aborted streaming request on the next tab click; the
  scraper uses a `seen_hashes` set to skip stale replays and pick the correct
  new-document URL

## Setup

### 1. Clone and install

```bash
git clone <your-repo-url>
cd senpilot-agent
python -m venv .venv
.venv\Scripts\activate   # Windows; or: source .venv/bin/activate
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
# edit .env — add HEADLESS=true for production
```

### 4. Authorise Gmail (one-time)

```bash
.venv\Scripts\python run.py --once
```

A browser window opens for OAuth consent. Click through; `token.json` is
written. Subsequent runs reuse it automatically.

### 5. Run

```bash
.venv\Scripts\python run.py           # poll loop — runs forever
.venv\Scripts\python run.py --once    # process current unread inbox, then exit
```

Send the agent an email like:
> Can you pull Other Documents from M12205?

The agent replies in-thread with a summary + ZIP attachment.

## File structure

| File | Purpose |
|------|---------|
| `run.py` | Entrypoint — poll loop |
| `agent.py` | Orchestration — parse → scrape → summarise → reply |
| `request_parser.py` | Extract matter number + doc type from email text |
| `scraper.py` | Playwright scraper for the UARB WebDirect site |
| `summary.py` | Build the reply email body from scrape results |
| `gmail_client.py` | Gmail API — poll, reply, mark-read |
| `config.py` | All config pulled from `.env` |

## What I'd add with more time

- Scroll/paginate WebDirect's document list (currently downloads up to the first
  page-full of Preview buttons; 9–10 visible rows)
- Retry with exponential back-off for WebDirect session timeouts
- Dedup incoming emails (hash `message-id`) so re-sends aren't processed twice
- Extract descriptive file names (exhibit number, title) instead of hash names
- Persist scrape results to SQLite for a searchable cache layer
