"""
Tests for imap_common.py

Tests cover:
- Environment variable verification
- IMAP connection handling
- Folder name normalization
- MIME header decoding
- Message details extraction
- Duplicate detection
- Filename sanitization
- Trash folder detection
"""

import json
import os
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import imap_common


class TestVerifyEnvVars:
    """Tests for verify_env_vars function."""

    def test_all_vars_present(self, monkeypatch):
        """Test returns True when all variables are set."""
        monkeypatch.setenv("VAR1", "value1")
        monkeypatch.setenv("VAR2", "value2")

        result = imap_common.verify_env_vars(["VAR1", "VAR2"])
        assert result is True

    def test_missing_vars(self, monkeypatch, capsys):
        """Test returns False and prints error when variables are missing."""
        monkeypatch.delenv("MISSING_VAR", raising=False)

        result = imap_common.verify_env_vars(["MISSING_VAR"])
        assert result is False

        captured = capsys.readouterr()
        assert "MISSING_VAR" in captured.err

    def test_partial_vars_present(self, monkeypatch, capsys):
        """Test with some variables present and some missing."""
        monkeypatch.setenv("PRESENT", "value")
        monkeypatch.delenv("MISSING", raising=False)

        result = imap_common.verify_env_vars(["PRESENT", "MISSING"])
        assert result is False


class TestGetImapConnection:
    """Tests for get_imap_connection function."""

    def test_invalid_credentials_empty(self, capsys):
        """Test returns None when credentials are empty."""
        result = imap_common.get_imap_connection("", "user", "pass")
        assert result is None

        result = imap_common.get_imap_connection("host", "", "pass")
        assert result is None

        result = imap_common.get_imap_connection("host", "user", "")
        assert result is None

    def test_connection_error(self, capsys):
        """Test returns None on connection error."""
        # Try to connect to an invalid host
        result = imap_common.get_imap_connection("invalid.nonexistent.host", "u", "p")
        assert result is None

        captured = capsys.readouterr()
        assert "Connection error" in captured.out or "Error" in captured.out


class TestNormalizeFolderName:
    """Tests for normalize_folder_name function."""

    def test_standard_format(self):
        """Test parsing standard IMAP list response."""
        folder_info = b'(\\HasNoChildren) "/" "INBOX"'
        result = imap_common.normalize_folder_name(folder_info)
        assert result == "INBOX"

    def test_unquoted_name(self):
        """Test parsing unquoted folder name."""
        folder_info = b'(\\HasNoChildren) "/" Drafts'
        result = imap_common.normalize_folder_name(folder_info)
        assert result == "Drafts"

    def test_with_special_flags(self):
        """Test parsing folder with special-use flags."""
        folder_info = b'(\\HasNoChildren \\Trash) "/" "Trash"'
        result = imap_common.normalize_folder_name(folder_info)
        assert result == "Trash"

    def test_gmail_folder(self):
        """Test parsing Gmail-style folder."""
        folder_info = b'(\\HasNoChildren) "/" "[Gmail]/All Mail"'
        result = imap_common.normalize_folder_name(folder_info)
        assert result == "[Gmail]/All Mail"

    def test_string_input(self):
        """Test with string input instead of bytes."""
        folder_info = '(\\HasNoChildren) "/" "Archive"'
        result = imap_common.normalize_folder_name(folder_info)
        assert result == "Archive"

    def test_fallback_parsing(self):
        """Test fallback when regex doesn't match."""
        folder_info = "simple_folder"
        result = imap_common.normalize_folder_name(folder_info)
        assert result == "simple_folder"


class TestDecodeMimeHeader:
    """Tests for decode_mime_header function."""

    def test_plain_ascii(self):
        """Test decoding plain ASCII header."""
        result = imap_common.decode_mime_header("Hello World")
        assert result == "Hello World"

    def test_none_input(self):
        """Test with None input."""
        result = imap_common.decode_mime_header(None)
        assert result == "(No Subject)"

    def test_empty_string(self):
        """Test with empty string."""
        result = imap_common.decode_mime_header("")
        assert result == "(No Subject)"

    def test_utf8_encoded(self):
        """Test decoding UTF-8 MIME encoded header."""
        # =?UTF-8?B?SGVsbG8gV29ybGQ=?= is "Hello World" in base64
        result = imap_common.decode_mime_header("=?UTF-8?B?SGVsbG8gV29ybGQ=?=")
        assert "Hello" in result

    def test_quoted_printable(self):
        """Test decoding quoted-printable header."""
        # =?UTF-8?Q?Hello_World?= is "Hello World" in quoted-printable
        result = imap_common.decode_mime_header("=?UTF-8?Q?Hello_World?=")
        assert "Hello" in result


class TestSanitizeFilename:
    """Tests for sanitize_filename function."""

    def test_valid_filename(self):
        """Test that valid filename passes through."""
        result = imap_common.sanitize_filename("valid_filename")
        assert result == "valid_filename"

    def test_invalid_characters(self):
        """Test that invalid characters are replaced."""
        result = imap_common.sanitize_filename('file<>:"/\\|?*name')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert '"' not in result
        assert "/" not in result
        assert "\\" not in result
        assert "|" not in result
        assert "?" not in result
        assert "*" not in result

    def test_empty_input(self):
        """Test that empty input returns 'untitled'."""
        result = imap_common.sanitize_filename("")
        assert result == "untitled"

    def test_none_input(self):
        """Test that None input returns 'untitled'."""
        result = imap_common.sanitize_filename(None)
        assert result == "untitled"

    def test_long_filename_truncation(self):
        """Test that long filenames are truncated."""
        long_name = "a" * 300
        result = imap_common.sanitize_filename(long_name)
        assert len(result) <= 250

    def test_strip_leading_trailing(self):
        """Test that leading/trailing whitespace and dots are stripped."""
        result = imap_common.sanitize_filename("  ..filename..  ")
        assert not result.startswith(" ")
        assert not result.startswith(".")
        assert not result.endswith(" ")
        assert not result.endswith(".")


class TestDetectTrashFolder:
    """Tests for detect_trash_folder function."""

    def test_detect_by_special_use_flag(self):
        """Test detection via \\Trash flag."""
        mock_conn = Mock()
        mock_conn.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren \\Trash) "/" "Deleted Items"',
            ],
        )

        result = imap_common.detect_trash_folder(mock_conn)
        assert result == "Deleted Items"

    def test_detect_gmail_trash(self):
        """Test detection of Gmail Trash folder."""
        mock_conn = Mock()
        mock_conn.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"',
            ],
        )

        result = imap_common.detect_trash_folder(mock_conn)
        assert result == "[Gmail]/Trash"

    def test_detect_by_name_fallback(self):
        """Test detection by common name when no flag present."""
        mock_conn = Mock()
        mock_conn.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "Trash"',
            ],
        )

        result = imap_common.detect_trash_folder(mock_conn)
        assert result == "Trash"

    def test_no_trash_folder(self):
        """Test returns None when no trash folder found."""
        mock_conn = Mock()
        mock_conn.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "Sent"',
            ],
        )

        result = imap_common.detect_trash_folder(mock_conn)
        assert result is None

    def test_list_error(self):
        """Test returns None on list error."""
        mock_conn = Mock()
        mock_conn.list.return_value = ("NO", [])

        result = imap_common.detect_trash_folder(mock_conn)
        assert result is None

    def test_exception_handling(self):
        """Test returns None on exception."""
        mock_conn = Mock()
        mock_conn.list.side_effect = Exception("Connection error")

        result = imap_common.detect_trash_folder(mock_conn)
        assert result is None


class TestMessageExistsInFolder:
    """Tests for message_exists_in_folder function."""

    def test_no_message_id(self):
        """Test returns False when message_id is None."""
        mock_conn = Mock()
        result = imap_common.message_exists_in_folder(mock_conn, None, 100)
        assert result is False

    def test_search_fails(self):
        """Test returns False when search fails."""
        mock_conn = Mock()
        mock_conn.search.return_value = ("NO", [])

        result = imap_common.message_exists_in_folder(mock_conn, "<msg-id>", 100)
        assert result is False

    def test_no_matches(self):
        """Test returns False when no matches found."""
        mock_conn = Mock()
        mock_conn.search.return_value = ("OK", [b""])

        result = imap_common.message_exists_in_folder(mock_conn, "<msg-id>", 100)
        assert result is False

    def test_match_found_same_size(self):
        """Test returns True when message with same ID and size found."""
        mock_conn = Mock()
        mock_conn.search.return_value = ("OK", [b"1"])
        mock_conn.fetch.return_value = ("OK", [b"1 (RFC822.SIZE 100)"])

        result = imap_common.message_exists_in_folder(mock_conn, "<msg-id>", 100)
        assert result is True

    def test_match_found_different_size(self):
        """Test returns False when message with same ID but different size found."""
        mock_conn = Mock()
        mock_conn.search.return_value = ("OK", [b"1"])
        mock_conn.fetch.return_value = ("OK", [b"1 (RFC822.SIZE 200)"])

        result = imap_common.message_exists_in_folder(mock_conn, "<msg-id>", 100)
        assert result is False


class TestGetMsgDetails:
    """Tests for get_msg_details function."""

    def test_fetch_error(self):
        """Test returns None tuple on fetch error."""
        mock_conn = Mock()
        mock_conn.uid.side_effect = Exception("Fetch error")

        msg_id, size, subject = imap_common.get_msg_details(mock_conn, b"1")
        assert msg_id is None
        assert size is None
        assert subject is None

    def test_not_ok_response(self):
        """Test returns None tuple on non-OK response."""
        mock_conn = Mock()
        mock_conn.uid.return_value = ("NO", None)

        msg_id, size, subject = imap_common.get_msg_details(mock_conn, b"1")
        assert msg_id is None
        assert size is None
        assert subject is None


class TestDetectOauth2Provider:
    """Tests for detect_oauth2_provider function."""

    def test_microsoft_outlook(self):
        """Test detects Microsoft from outlook host."""
        assert imap_common.detect_oauth2_provider("outlook.office365.com") == "microsoft"

    def test_microsoft_office365(self):
        """Test detects Microsoft from office365 host."""
        assert imap_common.detect_oauth2_provider("imap.office365.com") == "microsoft"

    def test_microsoft_mixed_case(self):
        """Test detects Microsoft case-insensitively."""
        assert imap_common.detect_oauth2_provider("Outlook.Office365.COM") == "microsoft"

    def test_google_gmail(self):
        """Test detects Google from gmail host."""
        assert imap_common.detect_oauth2_provider("imap.gmail.com") == "google"

    def test_google_googlemail(self):
        """Test detects Google from google host."""
        assert imap_common.detect_oauth2_provider("imap.google.com") == "google"

    def test_unknown_provider(self):
        """Test returns None for unrecognized host."""
        assert imap_common.detect_oauth2_provider("imap.example.com") is None

    def test_unknown_yahoo(self):
        """Test returns None for Yahoo host."""
        assert imap_common.detect_oauth2_provider("imap.mail.yahoo.com") is None


class TestDiscoverMicrosoftTenant:
    """Tests for discover_microsoft_tenant function."""

    def test_successful_discovery(self):
        """Test successful tenant ID extraction from OpenID config."""
        tenant_id = "12345678-abcd-ef01-2345-67890abcdef0"
        openid_response = json.dumps({
            "issuer": f"https://sts.windows.net/{tenant_id}/",
            "authorization_endpoint": f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize",
        }).encode("utf-8")

        mock_response = MagicMock()
        mock_response.read.return_value = openid_response
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = imap_common.discover_microsoft_tenant("user@contoso.com")

        assert result == tenant_id

    def test_domain_extraction(self):
        """Test that domain is correctly extracted from email."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "issuer": "https://sts.windows.net/abcdef01-2345-6789-abcd-ef0123456789/"
        }).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            imap_common.discover_microsoft_tenant("user@example.org")
            call_url = mock_urlopen.call_args[0][0]
            assert "example.org" in call_url

    def test_network_error(self, capsys):
        """Test returns None on network error."""
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            result = imap_common.discover_microsoft_tenant("user@invalid.example")

        assert result is None
        captured = capsys.readouterr()
        assert "Could not discover" in captured.out

    def test_invalid_json(self, capsys):
        """Test returns None on invalid JSON response."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"not json"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = imap_common.discover_microsoft_tenant("user@test.com")

        assert result is None

    def test_no_tenant_in_issuer(self, capsys):
        """Test returns None when issuer has no tenant GUID."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "issuer": "https://sts.windows.net/not-a-guid/"
        }).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = imap_common.discover_microsoft_tenant("user@test.com")

        assert result is None
        captured = capsys.readouterr()
        assert "Could not extract tenant ID" in captured.out


@pytest.fixture(autouse=True)
def clear_oauth2_caches():
    """Clear module-level OAuth2 caches between tests."""
    imap_common._msal_app_cache.clear()
    imap_common._google_creds_cache.clear()
    yield
    imap_common._msal_app_cache.clear()
    imap_common._google_creds_cache.clear()


class TestAcquireMicrosoftOauth2Token:
    """Tests for acquire_microsoft_oauth2_token function."""

    def test_successful_token(self):
        """Test successful token acquisition with auto-discovery."""
        with patch.object(imap_common, "discover_microsoft_tenant", return_value="tenant-123"):
            mock_msal = MagicMock()
            mock_app = MagicMock()
            mock_app.get_accounts.return_value = []
            mock_app.initiate_device_flow.return_value = {"user_code": "ABC123", "message": "Go to..."}
            mock_app.acquire_token_by_device_flow.return_value = {"access_token": "test_token"}
            mock_msal.PublicClientApplication.return_value = mock_app

            with patch.dict("sys.modules", {"msal": mock_msal}):
                result = imap_common.acquire_microsoft_oauth2_token("client-id", "user@test.com")

            assert result == "test_token"

    def test_tenant_discovery_failure(self, capsys):
        """Test returns None when tenant discovery fails."""
        with patch.object(imap_common, "discover_microsoft_tenant", return_value=None):
            result = imap_common.acquire_microsoft_oauth2_token("client-id", "user@test.com")

        assert result is None

    def test_cached_token(self):
        """Test returns cached token when available."""
        with patch.object(imap_common, "discover_microsoft_tenant", return_value="tenant-123"):
            mock_msal = MagicMock()
            mock_app = MagicMock()
            mock_account = {"username": "user@test.com"}
            mock_app.get_accounts.return_value = [mock_account]
            mock_app.acquire_token_silent.return_value = {"access_token": "cached_token"}
            mock_msal.PublicClientApplication.return_value = mock_app

            with patch.dict("sys.modules", {"msal": mock_msal}):
                result = imap_common.acquire_microsoft_oauth2_token("client-id", "user@test.com")

            assert result == "cached_token"


class TestAcquireGoogleOauth2Token:
    """Tests for acquire_google_oauth2_token function."""

    def test_successful_token(self):
        """Test successful Google token acquisition."""
        mock_credentials = MagicMock()
        mock_credentials.token = "google_test_token"

        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_credentials

        mock_installed_app_flow = MagicMock()
        mock_installed_app_flow.from_client_config.return_value = mock_flow

        mock_module = MagicMock()
        mock_module.InstalledAppFlow = mock_installed_app_flow

        with patch.dict("sys.modules", {"google_auth_oauthlib": MagicMock(), "google_auth_oauthlib.flow": mock_module}):
            result = imap_common.acquire_google_oauth2_token("client-id", "client-secret")

        assert result == "google_test_token"

    def test_missing_library(self):
        """Test exits when google-auth-oauthlib is not installed."""
        with patch.dict("sys.modules", {"google_auth_oauthlib": None, "google_auth_oauthlib.flow": None}):
            with pytest.raises(SystemExit):
                imap_common.acquire_google_oauth2_token("client-id", "client-secret")

    def test_no_token_returned(self):
        """Test returns None when credentials have no token."""
        mock_credentials = MagicMock()
        mock_credentials.token = None

        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_credentials

        mock_installed_app_flow = MagicMock()
        mock_installed_app_flow.from_client_config.return_value = mock_flow

        mock_module = MagicMock()
        mock_module.InstalledAppFlow = mock_installed_app_flow

        with patch.dict("sys.modules", {"google_auth_oauthlib": MagicMock(), "google_auth_oauthlib.flow": mock_module}):
            result = imap_common.acquire_google_oauth2_token("client-id", "client-secret")

        assert result is None


class TestAcquireOauth2TokenForProvider:
    """Tests for acquire_oauth2_token_for_provider dispatch function."""

    def test_dispatch_to_microsoft(self):
        """Test dispatches to Microsoft when provider is 'microsoft'."""
        with patch.object(imap_common, "acquire_microsoft_oauth2_token", return_value="ms_token") as mock_ms:
            result = imap_common.acquire_oauth2_token_for_provider("microsoft", "cid", "user@test.com")

        assert result == "ms_token"
        mock_ms.assert_called_once_with("cid", "user@test.com")

    def test_dispatch_to_google(self):
        """Test dispatches to Google when provider is 'google'."""
        with patch.object(imap_common, "acquire_google_oauth2_token", return_value="g_token") as mock_g:
            result = imap_common.acquire_oauth2_token_for_provider("google", "cid", "user@gmail.com", "secret")

        assert result == "g_token"
        mock_g.assert_called_once_with("cid", "secret")

    def test_google_requires_client_secret(self, capsys):
        """Test returns None when Google is selected without client_secret."""
        result = imap_common.acquire_oauth2_token_for_provider("google", "cid", "user@gmail.com")

        assert result is None
        captured = capsys.readouterr()
        assert "--client-secret" in captured.out

    def test_unknown_provider(self, capsys):
        """Test returns None for unknown provider."""
        result = imap_common.acquire_oauth2_token_for_provider("yahoo", "cid", "user@yahoo.com")

        assert result is None
        captured = capsys.readouterr()
        assert "Unknown OAuth2 provider" in captured.out


class TestMicrosoftTokenRefresh:
    """Tests for Microsoft OAuth2 token caching and refresh."""

    def test_msal_app_cached_on_first_call(self):
        """Test MSAL app is cached after first call."""
        with patch.object(imap_common, "discover_microsoft_tenant", return_value="tenant-123"):
            mock_msal = MagicMock()
            mock_app = MagicMock()
            mock_app.get_accounts.return_value = []
            mock_app.initiate_device_flow.return_value = {"user_code": "ABC", "message": "Go to..."}
            mock_app.acquire_token_by_device_flow.return_value = {"access_token": "token1"}
            mock_msal.PublicClientApplication.return_value = mock_app

            with patch.dict("sys.modules", {"msal": mock_msal}):
                imap_common.acquire_microsoft_oauth2_token("client-id", "user@test.com")

            assert ("client-id", "tenant-123") in imap_common._msal_app_cache

    def test_cached_app_reused_on_second_call(self):
        """Test second call reuses cached MSAL app instead of creating new one."""
        with patch.object(imap_common, "discover_microsoft_tenant", return_value="tenant-123"):
            mock_msal = MagicMock()
            mock_app = MagicMock()
            mock_app.get_accounts.return_value = []
            mock_app.initiate_device_flow.return_value = {"user_code": "ABC", "message": "Go to..."}
            mock_app.acquire_token_by_device_flow.return_value = {"access_token": "token1"}
            mock_msal.PublicClientApplication.return_value = mock_app

            with patch.dict("sys.modules", {"msal": mock_msal}):
                imap_common.acquire_microsoft_oauth2_token("client-id", "user@test.com")

                # Second call — simulate cached token available (refresh token worked)
                mock_account = {"username": "user@test.com"}
                mock_app.get_accounts.return_value = [mock_account]
                mock_app.acquire_token_silent.return_value = {"access_token": "refreshed_token"}

                result = imap_common.acquire_microsoft_oauth2_token("client-id", "user@test.com")

            assert result == "refreshed_token"
            # PublicClientApplication should only have been called once (first call)
            assert mock_msal.PublicClientApplication.call_count == 1


class TestGoogleTokenRefresh:
    """Tests for Google OAuth2 token caching and refresh."""

    def test_credentials_cached_on_first_call(self):
        """Test Google credentials are cached after first call."""
        mock_credentials = MagicMock()
        mock_credentials.token = "google_token"

        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_credentials

        mock_installed_app_flow = MagicMock()
        mock_installed_app_flow.from_client_config.return_value = mock_flow

        mock_module = MagicMock()
        mock_module.InstalledAppFlow = mock_installed_app_flow

        with patch.dict("sys.modules", {"google_auth_oauthlib": MagicMock(), "google_auth_oauthlib.flow": mock_module}):
            imap_common.acquire_google_oauth2_token("client-id", "client-secret")

        assert ("client-id", "client-secret") in imap_common._google_creds_cache

    def test_cached_credentials_refreshed_on_second_call(self):
        """Test second call refreshes cached credentials without opening browser."""
        # Pre-populate cache with credentials that have a refresh token
        mock_creds = MagicMock()
        mock_creds.refresh_token = "refresh_tok"
        mock_creds.token = "refreshed_google_token"
        imap_common._google_creds_cache[("client-id", "client-secret")] = mock_creds

        mock_request_module = MagicMock()
        with patch.dict("sys.modules", {
            "google": MagicMock(),
            "google.auth": MagicMock(),
            "google.auth.transport": MagicMock(),
            "google.auth.transport.requests": mock_request_module,
        }):
            result = imap_common.acquire_google_oauth2_token("client-id", "client-secret")

        assert result == "refreshed_google_token"
        # Verify refresh was called
        mock_creds.refresh.assert_called_once()

    def test_falls_back_to_browser_if_refresh_fails(self):
        """Test falls back to full auth flow if cached token refresh fails."""
        # Pre-populate cache with credentials whose refresh fails
        mock_creds = MagicMock()
        mock_creds.refresh_token = "refresh_tok"
        mock_creds.refresh.side_effect = Exception("Refresh failed")
        imap_common._google_creds_cache[("client-id", "client-secret")] = mock_creds

        # Set up the full auth flow
        mock_credentials = MagicMock()
        mock_credentials.token = "new_browser_token"

        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_credentials

        mock_installed_app_flow = MagicMock()
        mock_installed_app_flow.from_client_config.return_value = mock_flow

        mock_module = MagicMock()
        mock_module.InstalledAppFlow = mock_installed_app_flow

        with patch.dict("sys.modules", {"google_auth_oauthlib": MagicMock(), "google_auth_oauthlib.flow": mock_module}):
            result = imap_common.acquire_google_oauth2_token("client-id", "client-secret")

        assert result == "new_browser_token"


class TestRefreshOauth2Token:
    """Tests for thread-safe refresh_oauth2_token function."""

    def test_refreshes_token_and_updates_conf(self):
        """Test that a new token is acquired and conf[3] is updated."""
        conf = ["host", "user", "pass", "old_token"]

        with patch.object(imap_common, "acquire_oauth2_token_for_provider", return_value="new_token") as mock_acquire:
            result = imap_common.refresh_oauth2_token(
                "microsoft", "client-id", "user@test.com", None, conf, "old_token"
            )

        assert result == "new_token"
        assert conf[3] == "new_token"
        mock_acquire.assert_called_once_with("microsoft", "client-id", "user@test.com", None)

    def test_skips_refresh_when_token_already_updated(self):
        """Test that refresh is skipped if another thread already updated the token."""
        conf = ["host", "user", "pass", "already_refreshed_token"]

        with patch.object(imap_common, "acquire_oauth2_token_for_provider") as mock_acquire:
            result = imap_common.refresh_oauth2_token(
                "microsoft", "client-id", "user@test.com", None, conf, "old_token"
            )

        assert result == "already_refreshed_token"
        assert conf[3] == "already_refreshed_token"
        mock_acquire.assert_not_called()

    def test_returns_none_on_refresh_failure(self):
        """Test returns None and leaves conf unchanged when refresh fails."""
        conf = ["host", "user", "pass", "old_token"]

        with patch.object(imap_common, "acquire_oauth2_token_for_provider", return_value=None):
            result = imap_common.refresh_oauth2_token(
                "google", "client-id", "user@gmail.com", "secret", conf, "old_token"
            )

        assert result is None
        assert conf[3] == "old_token"

    def test_concurrent_threads_only_one_refreshes(self):
        """Test that only one thread performs the refresh when multiple threads compete."""
        import threading
        import time

        conf = ["host", "user", "pass", "expired_token"]
        call_count = {"value": 0}
        barrier = threading.Barrier(3)  # 3 threads

        original_acquire = imap_common.acquire_oauth2_token_for_provider

        def slow_acquire(provider, client_id, email, client_secret):
            call_count["value"] += 1
            time.sleep(0.05)  # Simulate network delay
            return "fresh_token"

        def thread_func():
            barrier.wait()  # Ensure all threads start at the same time
            imap_common.refresh_oauth2_token(
                "microsoft", "client-id", "user@test.com", None, conf, "expired_token"
            )

        with patch.object(imap_common, "acquire_oauth2_token_for_provider", side_effect=slow_acquire):
            threads = [threading.Thread(target=thread_func) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # Only one thread should have called acquire (the first to get the lock).
        # The other two should see conf[3] changed and skip.
        assert call_count["value"] == 1
        assert conf[3] == "fresh_token"
