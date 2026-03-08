"""Integration tests for SMB API module.

Requires SMB_USERNAME and SMB_PASSWORD in .env. Tests hit the real SMB API.
Run with: python -m tests.test_smb_api
Or pytest: python -m pytest tests/test_smb_api.py -v -s
"""

import json
import unittest

import requests

from trading.smb_api import (
    create_authenticated_session,
    fetch_positions,
    get_session,
    is_session_valid,
    load_cookies,
    save_cookies,
)


def _run_tests_and_print() -> None:
    """Run tests and print values for manual verification."""
    print('=== SMB API test & print ===\n')

    session = get_session()
    print('get_session(): OK')

    valid = is_session_valid(session)
    print(f'is_session_valid(session): {valid}\n')

    positions = fetch_positions(session)
    print(f'fetch_positions(): {len(positions)} raw records')
    if positions:
        print('Sample record (first):')
        print(json.dumps(positions[0], indent=2, default=str))
        print('\nAll positions summary:')
        for i, rec in enumerate(positions[:20], 1):
            acct = rec.get('account_name', '?')
            sym = rec.get('symbol', '?')
            side = rec.get('side', '?')
            mag = rec.get('magnitude', 0)
            print(f'  {i:2}. {acct} | {sym} | {side} | {mag}')
        if len(positions) > 20:
            print(f'  ... and {len(positions) - 20} more')
    print()


class TestSmbApiIntegration(unittest.TestCase):
    """Integration tests against real SMB API."""

    def test_get_session(self) -> None:
        """get_session returns an authenticated session."""
        session = get_session()
        self.assertIsNotNone(session)
        valid = is_session_valid(session)
        self.assertTrue(valid, 'Session should be valid after get_session')
        print('get_session: OK')

    def test_fetch_positions(self) -> None:
        """fetch_positions returns a list (possibly empty)."""
        session = get_session()
        positions = fetch_positions(session)
        self.assertIsInstance(positions, list)
        print(f'fetch_positions: {len(positions)} records')

    def test_create_authenticated_session(self) -> None:
        """create_authenticated_session returns a valid session."""
        session = create_authenticated_session()
        self.assertIsNotNone(session)
        self.assertTrue(is_session_valid(session))
        print('create_authenticated_session: OK')

    def test_save_load_cookies(self) -> None:
        """save_cookies and load_cookies round-trip."""
        session = get_session()
        save_cookies(session)
        new_session = requests.Session()
        loaded = load_cookies(new_session)
        self.assertTrue(loaded)
        self.assertTrue(is_session_valid(new_session))
        print('save_cookies/load_cookies: OK')


if __name__ == '__main__':
    _run_tests_and_print()
    print('--- unittest ---')
    unittest.main(buffer=False)
