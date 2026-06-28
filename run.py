"""Entrypoint.

  python run.py           # poll loop (runs forever)
  python run.py --once    # process current unread, then exit (good for demos)
"""
import sys
import time

import config
import gmail_client
from agent import handle_email


def main():
    once = "--once" in sys.argv
    service = gmail_client.get_service()
    print(f"[run] agent live — polling every {config.POLL_INTERVAL_SECONDS}s")
    while True:
        try:
            emails = gmail_client.list_unread(service)
            if emails:
                print(f"[run] {len(emails)} unread email(s)")
            for email in emails:
                try:
                    handle_email(service, email)
                finally:
                    gmail_client.mark_read(service, email.gmail_id)
        except Exception as e:
            print(f"[run] loop error: {e}")
        if once:
            break
        time.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
