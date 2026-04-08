"""Google APIs for this repo: user OAuth2 (Desktop client) + Gmail.

Jambot uses a service account in ``integrations/google.py``. For a personal
Gmail account you need the OAuth2 installed-app flow, a refresh token on disk,
and the Gmail API enabled in the same Cloud project as the OAuth client.
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuth2Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource
from googleapiclient.discovery import build

if TYPE_CHECKING:
    from google.auth.credentials import Credentials as GoogleCredentials

# Default scopes: tighten to what you need. ``gmail.readonly`` cannot send mail.
DEFAULT_GMAIL_SCOPES: tuple[str, ...] = (
    'https://www.googleapis.com/auth/gmail.readonly',
)

_ENV_CLIENT_SECRETS = 'TRADING_GOOGLE_CLIENT_SECRETS_JSON'
_ENV_TOKEN_PATH = 'TRADING_GOOGLE_OAUTH_TOKEN_JSON'


def _run_interactive_oauth_flow(
    *,
    p_client_secrets: Path,
    p_token: Path,
    scopes: tuple[str, ...],
) -> 'GoogleCredentials':
    """Run installed-app OAuth flow and persist token JSON."""
    if not p_client_secrets.is_file():
        msg = (
            f'Missing OAuth client secrets at {p_client_secrets}. '
            f'Download the Desktop client JSON from Google Cloud and save it there, '
            f'or set {_ENV_CLIENT_SECRETS}.'
        )
        raise FileNotFoundError(msg)

    creds = InstalledAppFlow.from_client_secrets_file(
        str(p_client_secrets),
        list(scopes),
    ).run_local_server(port=0)
    if creds is None:
        msg = 'OAuth flow did not return credentials.'
        raise RuntimeError(msg)

    p_token.parent.mkdir(parents=True, exist_ok=True)
    p_token.write_text(creds.to_json(), encoding='utf-8')
    return creds


def _p_config_dir() -> Path:
    return Path.home() / '.config' / 'trading'


def p_client_secrets_path() -> Path:
    """JSON from Google Cloud: APIs & Services → Credentials → Download OAuth client."""
    if raw := os.environ.get(_ENV_CLIENT_SECRETS):
        return Path(raw)
    return _p_config_dir() / 'google_client_secret.json'


def p_oauth_token_path() -> Path:
    """Writable path for stored user tokens (refresh + access). Create parent dirs if missing."""
    if raw := os.environ.get(_ENV_TOKEN_PATH):
        return Path(raw)
    return _p_config_dir() / 'google_oauth_token.json'


def get_creds(
    scopes: tuple[str, ...] | None = None,
    *,
    p_client_secrets: Path | None = None,
    p_token: Path | None = None,
    interactive: bool = True,
) -> 'GoogleCredentials':
    """Load or obtain Google user OAuth2 credentials (refresh token on disk).

    First run opens a browser (or console URL) to sign in; later runs refresh
    the access token automatically.

    Parameters
    ----------
    scopes
        OAuth scopes. Defaults to ``DEFAULT_GMAIL_SCOPES``.
    p_client_secrets
        OAuth client JSON from Google (Desktop client type). Defaults from env
        ``TRADING_GOOGLE_CLIENT_SECRETS_JSON`` or ``~/.config/trading/google_client_secret.json``.
    p_token
        File to read/write authorized user token. Defaults from env
        ``TRADING_GOOGLE_OAUTH_TOKEN_JSON`` or ``~/.config/trading/google_oauth_token.json``.
    interactive
        If True and no valid token exists, run the installed-app flow. If False,
        raises if the token file is missing or invalid.

    Returns
    -------
    GoogleCredentials
        ``google.auth.credentials.Credentials`` (typically OAuth2 user credentials).
    """
    scopes_use = scopes if scopes is not None else DEFAULT_GMAIL_SCOPES
    p_secret = p_client_secrets if p_client_secrets is not None else p_client_secrets_path()
    p_tok = p_token if p_token is not None else p_oauth_token_path()

    creds: GoogleCredentials | None = None
    if p_tok.is_file():
        creds = OAuth2Credentials.from_authorized_user_file(str(p_tok), list(scopes_use))

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            p_tok.parent.mkdir(parents=True, exist_ok=True)
            p_tok.write_text(creds.to_json(), encoding='utf-8')
            return creds
        except RefreshError as exc:
            if not interactive:
                msg = (
                    f'Google OAuth token refresh failed for {p_tok}: {exc}. '
                    'Token is likely revoked/expired; rerun with interactive=True to re-authorize.'
                )
                raise RuntimeError(msg) from exc

            if p_tok.is_file():
                p_tok.unlink()
            return _run_interactive_oauth_flow(
                p_client_secrets=p_secret,
                p_token=p_tok,
                scopes=scopes_use,
            )

    if not interactive:
        msg = (
            f'No valid Google OAuth token at {p_tok}. '
            'Run once with interactive=True or place a token file from a prior auth.'
        )
        raise RuntimeError(msg)

    return _run_interactive_oauth_flow(
        p_client_secrets=p_secret,
        p_token=p_tok,
        scopes=scopes_use,
    )


def get_gmail_api(
    scopes: tuple[str, ...] | None = None,
    **kw,
) -> Resource:
    """Build Gmail API v1 client with user credentials from :func:`get_creds`.

    Parameters
    ----------
    scopes
        Passed through to :func:`get_creds`.
    **kw
        Forwarded to :func:`get_creds` (e.g. ``p_client_secrets``, ``p_token``, ``interactive``).

    Returns
    -------
    Resource
        ``googleapiclient.discovery.Resource`` for ``gmail`` v1 (typed as Resource).
    """
    creds = get_creds(scopes=scopes, **kw)
    return build('gmail', 'v1', credentials=creds, cache_discovery=False)
