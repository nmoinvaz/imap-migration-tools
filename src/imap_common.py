"""
IMAP Common Utilities

Shared functionality for IMAP migration, counting, and comparison scripts.
"""

import imaplib
import os
import re
import sys
from email.header import decode_header
from email.parser import BytesParser


def verify_env_vars(vars_list):
    """
    Checks if all environment variables in the list are set.
    Returns True if all are present, False otherwise.
    Prints missing variables to stderr.
    """
    missing = [v for v in vars_list if not os.getenv(v)]
    if missing:
        print(f"Error: Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return False
    return True


def detect_oauth2_provider(host):
    """
    Detects the OAuth2 provider from the IMAP host.
    Returns "microsoft", "google", or None if unrecognized.
    """
    host_lower = host.lower()
    if "outlook" in host_lower or "office365" in host_lower or "microsoft" in host_lower:
        return "microsoft"
    if "gmail" in host_lower or "google" in host_lower:
        return "google"
    return None


def discover_microsoft_tenant(email):
    """
    Auto-discovers the Microsoft tenant ID from an email address domain.
    Uses the OpenID Connect discovery endpoint (no authentication required).
    Returns the tenant ID string or None if discovery fails.
    """
    import json
    import urllib.error
    import urllib.request

    domain = email.split("@")[-1]
    url = f"https://login.microsoftonline.com/{domain}/.well-known/openid-configuration"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"Error: Could not discover Microsoft tenant for domain '{domain}': {e}")
        return None

    issuer = data.get("issuer", "")
    match = re.search(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", issuer)
    if match:
        return match.group(1)

    print(f"Error: Could not extract tenant ID from issuer: {issuer}")
    return None


def acquire_microsoft_oauth2_token(client_id, email):
    """
    Acquires a Microsoft OAuth2 access token using the MSAL device code flow.
    Auto-discovers tenant ID from the email domain.
    Requires the 'msal' package: pip install msal
    """
    try:
        import msal
    except ImportError:
        print("Error: 'msal' package is required for Microsoft OAuth2. Install it with: pip install msal")
        sys.exit(1)

    tenant_id = discover_microsoft_tenant(email)
    if not tenant_id:
        return None

    print(f"Discovered Microsoft tenant: {tenant_id}")

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    scopes = ["https://outlook.office365.com/IMAP.AccessAsUser.All"]

    app = msal.PublicClientApplication(client_id, authority=authority)

    # Try cached token first
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])
        if result and "access_token" in result:
            return result["access_token"]

    # Fall back to device code flow
    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        print(f"Error: Could not initiate device flow: {flow.get('error_description', 'Unknown error')}")
        return None

    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)

    if "access_token" in result:
        return result["access_token"]

    print(f"Error: Could not acquire token: {result.get('error_description', 'Unknown error')}")
    return None


def acquire_google_oauth2_token(client_id, client_secret):
    """
    Acquires a Google OAuth2 access token using the installed app flow.
    Opens a browser for user consent and runs a local HTTP server for the redirect.
    Requires the 'google-auth-oauthlib' package: pip install google-auth-oauthlib
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Error: 'google-auth-oauthlib' package is required for Google OAuth2.")
        print("Install it with: pip install google-auth-oauthlib")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=["https://mail.google.com/"])

    print("Opening browser for Google authentication...")
    print("If the browser does not open, check the terminal for a URL to visit.")

    credentials = flow.run_local_server(port=0)

    if credentials and credentials.token:
        return credentials.token

    print("Error: Could not acquire Google OAuth2 token.")
    return None


def acquire_oauth2_token_for_provider(provider, client_id, email, client_secret=None):
    """
    Acquires an OAuth2 token for the specified provider.

    Args:
        provider: "microsoft" or "google"
        client_id: OAuth2 client ID
        email: User's email address (used for Microsoft tenant discovery)
        client_secret: Required for Google, not needed for Microsoft
    """
    if provider == "microsoft":
        return acquire_microsoft_oauth2_token(client_id, email)
    elif provider == "google":
        if not client_secret:
            print("Error: --client-secret is required for Google OAuth2.")
            return None
        return acquire_google_oauth2_token(client_id, client_secret)
    else:
        print(f"Error: Unknown OAuth2 provider: {provider}")
        return None


def get_imap_connection(host, user, password=None, oauth2_token=None):
    """
    Establishes an SSL connection to the IMAP server and logs in.
    Supports both basic auth (password) and OAuth 2.0 (XOAUTH2).
    Returns the connection object or None if failed.
    """
    if not host or not user:
        print(f"Error: Invalid credentials for {host}")
        return None

    if not password and not oauth2_token:
        print(f"Error: Either password or oauth2_token is required for {host}")
        return None

    try:
        conn = imaplib.IMAP4_SSL(host)
        if oauth2_token:
            auth_string = f"user={user}\x01auth=Bearer {oauth2_token}\x01\x01"
            conn.authenticate("XOAUTH2", lambda _: auth_string.encode())
        else:
            conn.login(user, password)
        return conn
    except Exception as e:
        print(f"Connection error to {host}: {e}")
        return None


def ensure_connection(conn, host, user, password=None, oauth2_token=None):
    """
    Verifies an IMAP connection is still alive, reconnecting if necessary.
    Returns the existing connection if healthy, or a new connection if it was broken.
    Returns None if reconnection fails.
    """
    try:
        if conn:
            conn.noop()
            return conn
    except Exception:
        pass
    return get_imap_connection(host, user, password, oauth2_token)


def normalize_folder_name(folder_info_str):
    """
    Parses the IMAP list response to extract the clean folder name.
    Handles quoted names and flags.
    """
    if isinstance(folder_info_str, bytes):
        folder_info_str = folder_info_str.decode("utf-8", errors="ignore")

    # Regex to extract folder name: (flags) "delimiter" name
    # Matches: (\HasNoChildren) "/" "INBOX"  OR  (\HasNoChildren) "/" Drafts
    list_pattern = re.compile(r'\((?P<flags>.*?)\) "(?P<delimiter>.*)" "?(?P<name>.*)"?')
    match = list_pattern.search(folder_info_str)
    if match:
        name = match.group("name")
        # If the regex grabbed a trailing quote, strip it (though the regex tries to handle it)
        return name.rstrip('"').strip()

    # Fallback: take the last part
    return folder_info_str.split()[-1].strip('"')


def list_selectable_folders(imap_conn):
    """
    Lists all selectable folders (excluding \\Noselect) on the IMAP connection.
    Returns a list of normalized folder name strings, or an empty list on failure.
    """
    try:
        status, folders = imap_conn.list()
        if status != "OK" or not folders:
            return []
    except Exception:
        return []

    result = []
    for f in folders:
        f_str = f.decode("utf-8", errors="ignore") if isinstance(f, bytes) else str(f)
        if "\\Noselect" in f_str:
            continue
        result.append(normalize_folder_name(f))
    return result


def decode_mime_header(header_value):
    """
    Decodes MIME encoded headers (Subject, etc.) to a unicode (str) string.
    """
    if not header_value:
        return "(No Subject)"
    try:
        decoded_list = decode_header(header_value)
        default_charset = "utf-8"
        text_parts = []
        for bytes_data, encoding in decoded_list:
            if isinstance(bytes_data, bytes):
                if encoding:
                    try:
                        text_parts.append(bytes_data.decode(encoding, errors="ignore"))
                    except LookupError:
                        text_parts.append(bytes_data.decode(default_charset, errors="ignore"))
                else:
                    text_parts.append(bytes_data.decode(default_charset, errors="ignore"))
            else:
                text_parts.append(str(bytes_data))
        return "".join(text_parts)
    except Exception:
        return str(header_value)


def get_msg_details(imap_conn, uid):
    """
    Fetches simplified message details (Message-ID, Size, Subject) for a given UID.
    Returns (msg_id, size, subject) tuple.
    """
    try:
        resp, data = imap_conn.uid("fetch", uid, "(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT)])")
    except Exception:
        return None, None, None

    if resp != "OK":
        return None, None, None

    msg_id = None
    subject = "(No Subject)"
    size = 0

    for item in data:
        if isinstance(item, tuple):
            content = item[0].decode("utf-8", errors="ignore")

            # Parse Size
            size_match = re.search(r"RFC822\.SIZE\s+(\d+)", content)
            if size_match:
                size = int(size_match.group(1))

            # Parse Headers
            msg_bytes = item[1]
            parser = BytesParser()
            email_obj = parser.parsebytes(msg_bytes)
            msg_id = email_obj.get("Message-ID")
            raw_subject = email_obj.get("Subject")
            if raw_subject:
                subject = decode_mime_header(raw_subject)

    return msg_id, size, subject


def message_exists_in_folder(dest_conn, msg_id, src_size):
    """
    Checks if a message with the given Message-ID and RFC822.SIZE exists in the CURRENTLY SELECTED folder of dest_conn.
    Returns True if found, False otherwise.
    """
    if not msg_id:
        return False

    clean_id = msg_id.replace('"', '\\"')
    try:
        typ, data = dest_conn.search(None, f'(HEADER Message-ID "{clean_id}")')
        if typ != "OK":
            return False

        dest_ids = data[0].split()
        if not dest_ids:
            return False

        for did in dest_ids:
            resp, items = dest_conn.fetch(did, "(RFC822.SIZE)")
            if resp == "OK":
                for item in items:
                    if isinstance(item, bytes):
                        content = item.decode("utf-8", errors="ignore")
                    else:
                        content = item[0].decode("utf-8", errors="ignore")
                    size_match = re.search(r"RFC822\.SIZE\s+(\d+)", content)
                    if size_match and int(size_match.group(1)) == src_size:
                        return True
    except Exception:
        return False
    return False


def sanitize_filename(filename):
    """
    Sanitizes a string to be safe for use as a filename.
    Removes/replaces characters that are illegal in file systems.
    Truncates to 250 chars.
    """
    if not filename:
        return "untitled"
    # Replace invalid characters with underscore
    # Invalid: < > : " / \ | ? * and control chars
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)
    # Strip leading/trailing whitespaces/dots
    s = s.strip().strip(".")
    # Ensure not empty and not too long
    return s[:250] if s else "untitled"


def detect_trash_folder(imap_conn):
    """
    Attempts to identify the Trash folder in the account.
    Returns the folder name (str) or None if not found.
    Checks for common names and SPECIAL-USE attributes.
    """
    try:
        status, folders = imap_conn.list()
        if status != "OK":
            return None
    except Exception:
        return None

    trash_candidates = ["[Gmail]/Trash", "Trash", "Deleted Items", "Bin", "[Gmail]/Bin"]
    detected_by_flag = None
    all_folder_names = []

    for f in folders:
        if isinstance(f, bytes):
            f_str = f.decode("utf-8", errors="ignore")
        else:
            f_str = str(f)

        name = normalize_folder_name(f_str)
        all_folder_names.append(name)

        # Check for SPECIAL-USE flag \Trash
        # The flag is usually inside parentheses like (\HasNoChildren \Trash)
        if "\\Trash" in f_str or "\\Bin" in f_str:
            detected_by_flag = name

    if detected_by_flag:
        return detected_by_flag

    # Check candidates
    for candidate in trash_candidates:
        if candidate in all_folder_names:
            return candidate

    return None
