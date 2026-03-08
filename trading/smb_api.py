"""SMB API: HTTP auth, session management, and positions.

Handles login to rt.smbtraining.com, cookie persistence, and fetching
external-positions. Used by smb_screener for orchestration.
"""

import os
import pickle
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

SMB_USERNAME = os.getenv('SMB_USERNAME')
SMB_PASSWORD = os.getenv('SMB_PASSWORD')

if not SMB_USERNAME or not SMB_PASSWORD:
    raise ValueError('Missing SMB_USERNAME or SMB_PASSWORD in .env')

# Paths relative to repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent
COOKIES_FILE = str(_REPO_ROOT / 'resources' / 'cookies' / 'smb_cookies.pkl')

CSRF_URL = 'https://rt.smbtraining.com/api/auth/csrf'
LOGIN_URL = 'https://rt.smbtraining.com/api/auth/callback/credentials'
CALLBACK_URL = 'https://rt.smbtraining.com/auth/signin?callbackUrl=https%3A%2F%2Frt.smbtraining.com%2Fcalendar'
SESSION_URL = 'https://rt.smbtraining.com/api/auth/session'
POSITIONS_URL = 'https://rt.smbtraining.com/api/external-positions'


def create_authenticated_session() -> requests.Session:
    """Create a logged-in requests.Session. Called when no valid session exists from get_session using smb_cookies.pkl."""
    session = requests.Session()

    csrf_resp = session.get(CSRF_URL)
    csrf_data = csrf_resp.json()
    csrf_token = csrf_data.get('csrfToken')
    if not csrf_token:
        raise RuntimeError('No csrfToken in CSRF response')

    payload = {
        'email': SMB_USERNAME,
        'password': SMB_PASSWORD,
        'redirect': 'false',
        'csrfToken': csrf_token,
        'callbackUrl': CALLBACK_URL,
        'json': 'true',
    }
    headers = {
        'Origin': 'https://rt.smbtraining.com',
        'Referer': CALLBACK_URL,
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }

    login_resp = session.post(LOGIN_URL, data=payload, headers=headers)
    login_resp.raise_for_status()

    session_resp = session.get(SESSION_URL)
    session_resp.raise_for_status()

    positions_resp = session.get(POSITIONS_URL)
    positions_resp.raise_for_status()
    return session


def is_session_valid(session: requests.Session) -> bool:
    """Check if the session is still authenticated."""
    resp = session.get(SESSION_URL)
    if not resp.ok:
        print('Session check status:', resp.status_code)
        return False
    data = resp.json()
    return bool(data)


def save_cookies(session: requests.Session, path: str = COOKIES_FILE) -> None:
    """Save the session cookies to a file using pickle."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('wb') as f:
        pickle.dump(session.cookies, f)


def load_cookies(session: requests.Session, path: str = COOKIES_FILE) -> bool:
    """Load cookies from disk into the given session, if the cookie file exists.

    Returns:
        True if cookies were loaded, False if file did not exist.
    """
    if not Path(path).exists():
        return False
    with Path(path).open('rb') as f:
        loaded_cookies = pickle.load(f)
    session.cookies.update(loaded_cookies)
    return True


def get_session() -> requests.Session:
    """Return a requests.Session that is authenticated if possible.

    Logic:
      1. Create a new session.
      2. Try to load cookies from disk.
      3. If cookies loaded, check if session is still valid; if valid, return it.
      4. If not valid (or no cookie file), perform fresh login and save cookies.
    """
    session = requests.Session()

    cookies_loaded = load_cookies(session)
    if cookies_loaded:
        if is_session_valid(session):
            return session
        print('Loaded cookies but session is invalid, performing fresh login.')
    else:
        print('No cookies loaded, performing fresh login.')

    fresh_session = create_authenticated_session()
    save_cookies(fresh_session)
    return fresh_session


def fetch_positions(session: requests.Session) -> dict:
    """Fetch raw positions JSON using an already authenticated session."""
    resp = session.get(POSITIONS_URL)
    resp.raise_for_status()
    return resp.json()
