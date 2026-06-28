import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

UARB_URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"
MAX_DOCS = 10

DOC_TYPES = [
    "Exhibits",
    "Key Documents",
    "Other Documents",
    "Transcripts",
    "Recordings",
]

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "20"))
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or None

GMAIL_CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
GMAIL_TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "token.json")

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
