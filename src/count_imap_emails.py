"""
IMAP Email Counting Script

This script connects to an IMAP server, iterates through all available folders/mailboxes,
and counts the number of emails in each. It provides a progressive output of counts
per folder and a grand total at the end.

Configuration (Environment Variables):
  IMAP_HOST     : IMAP Host (e.g., imap.gmail.com)
  IMAP_USERNAME : Username/Email
  IMAP_PASSWORD : Password (or App Password)

Usage Example:
  export IMAP_HOST="imap.gmail.com"
  export IMAP_USERNAME="user@gmail.com"
  export IMAP_PASSWORD="secretpassword"

  python3 count_imap_emails.py
"""

import argparse
import imaplib
import os
import sys

import imap_common
import imap_oauth2


def count_emails(imap_server, username, password=None, oauth2_token=None):
    try:
        # Connect to the IMAP server (using SSL)
        print(f"Connecting to {imap_server}...")
        mail = imap_common.get_imap_connection(imap_server, username, password, oauth2_token)
        if not mail:
            return

        # List all mailboxes
        print("Listing mailboxes...")
        folders = imap_common.list_selectable_folders(mail)

        if not folders:
            print("Failed to list mailboxes.")
            return

        total_all_folders = 0
        print(f"{'Folder Name':<40} {'Count':>10}")
        print("-" * 52)

        for folder_name in folders:
            display_name = folder_name

            try:
                # Select the mailbox (read-only is sufficient for counting)
                # folder_name extracted from list usually handles quotes correctly for select
                rv, _ = mail.select(f'"{folder_name}"', readonly=True)
                if rv != "OK":
                    print(f"{display_name:<40} {'Skipped':>10}")
                    continue

                # Search for all emails
                status, data = mail.search(None, "ALL")

                if status == "OK":
                    # data[0] is space separated IDs
                    email_ids = data[0].split()
                    count = len(email_ids)
                    print(f"{display_name:<40} {count:>10}")
                    total_all_folders += count
                else:
                    print(f"{display_name:<40} {'Error':>10}")

            except imaplib.IMAP4.error:
                print(f"{display_name:<40} {'Error':>10}")

        print("-" * 52)
        print(f"{'TOTAL':<40} {total_all_folders:>10}")

        # Logout
        mail.logout()

    except imaplib.IMAP4.error as e:
        print(f"IMAP Error: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Count emails in IMAP account.")

    # Try to unify var names for defaults. Priority: IMAP_* > SRC_IMAP_* > None
    default_host = os.getenv("IMAP_HOST") or os.getenv("SRC_IMAP_HOST")
    default_user = os.getenv("IMAP_USERNAME") or os.getenv("SRC_IMAP_USERNAME")
    default_pass = os.getenv("IMAP_PASSWORD") or os.getenv("SRC_IMAP_PASSWORD")

    parser.add_argument("--host", default=default_host, help="IMAP Server")
    parser.add_argument("--user", default=default_user, help="Username")
    parser.add_argument("--pass", dest="password", default=default_pass, help="Password")

    # OAuth2
    parser.add_argument("--client-id", default=os.getenv("OAUTH2_CLIENT_ID") or os.getenv("SRC_OAUTH2_CLIENT_ID"), help="OAuth2 Client ID")
    parser.add_argument("--client-secret", default=os.getenv("OAUTH2_CLIENT_SECRET") or os.getenv("SRC_OAUTH2_CLIENT_SECRET"),
                        help="OAuth2 Client Secret (required for Google)")

    args = parser.parse_args()

    IMAP_SERVER = args.host
    USERNAME = args.user
    PASSWORD = args.password

    use_oauth2 = bool(args.client_id)
    if not IMAP_SERVER or not USERNAME or (not PASSWORD and not use_oauth2):
        print("Error: Missing credentials.")
        sys.exit(1)

    # Acquire OAuth2 token if configured
    oauth2_token = None
    oauth2_provider = None
    if use_oauth2:
        oauth2_provider = imap_oauth2.detect_oauth2_provider(IMAP_SERVER)
        if not oauth2_provider:
            print(f"Error: Could not detect OAuth2 provider from host '{IMAP_SERVER}'.")
            sys.exit(1)
        print(f"Acquiring OAuth2 token ({oauth2_provider})...")
        oauth2_token = imap_oauth2.acquire_oauth2_token_for_provider(
            oauth2_provider, args.client_id, USERNAME, args.client_secret
        )
        if not oauth2_token:
            print("Error: Failed to acquire OAuth2 token.")
            sys.exit(1)
        print("OAuth2 token acquired successfully.\n")

    print("\n--- Configuration Summary ---")
    print(f"Host            : {IMAP_SERVER}")
    print(f"User            : {USERNAME}")
    print(f"Auth Method     : {'OAuth2/' + oauth2_provider + ' (XOAUTH2)' if use_oauth2 else 'Basic (password)'}")
    print("-----------------------------\n")

    count_emails(IMAP_SERVER, USERNAME, PASSWORD, oauth2_token)
