"""Live integration for ``watchlist.sources.smb_gameplan``.

No mocked HTTP or synthetic payloads. Hits the real SMB endpoint using
``trading.smb_api.get_session`` (same credentials as the screener).

Requires ``SMB_GAMEPLAN_LIVE=1`` and ``SMB_USERNAME`` / ``SMB_PASSWORD`` in
``.env``.

shell cmd
SMB_GAMEPLAN_LIVE=1 uv run --frozen python -m unittest watchlist.tests.test_smb_gameplan -v

Optional anchor for the fallback test (instead of machine ``date.today()``):

SMB_GAMEPLAN_LIVE=1 SMB_GAMEPLAN_START_DATE=2026-03-20 uv run --frozen python -m unittest \
  watchlist.tests.test_smb_gameplan.TestSmbGameplanLive.test_fetch_gameplan_today_with_fallback -v
"""

import json
import os
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from trading.smb_api import get_session
from watchlist.sources.smb_gameplan import fetch_gameplan
from watchlist.sources.smb_gameplan import fetch_gameplan_today
from watchlist.sources.smb_gameplan import save_gameplan_json


class TestSmbGameplanSave(unittest.TestCase):
    """Unit tests for on-disk behavior (no SMB API)."""

    def test_second_save_same_trade_date_returns_existing_path(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td) / '2026' / '03' / '20'
            first = save_gameplan_json({'v': 1}, date(2026, 3, 20), repository_dir=repo)
            second = save_gameplan_json({'v': 2}, date(2026, 3, 20), repository_dir=repo)
            self.assertEqual(first, second)
            self.assertEqual(len(list(repo.glob('gameplan_*.json'))), 1)
            loaded = json.loads(first.read_text(encoding='utf-8'))
            self.assertEqual(loaded['payload'], {'v': 1})


@unittest.skipUnless(
    os.getenv('SMB_GAMEPLAN_LIVE') == '1',
    'Set SMB_GAMEPLAN_LIVE=1 to hit the real SMB API',
)
class TestSmbGameplanLive(unittest.TestCase):
    """Run ``smb_gameplan`` against rt.smbtraining.com with a real session."""

    def test_fetch_gameplan_2026_03_20(self) -> None:
        session = get_session()
        outcome = fetch_gameplan(session, date(2026, 3, 20), save_json=False)
        self.assertIsInstance(outcome.data, (dict, list))
        self.assertIsNone(outcome.snapshot_path)
        self.assertEqual(outcome.trade_date, date(2026, 3, 20))

    def test_fetch_gameplan_2026_03_20_saves_snapshot(self) -> None:
        session = get_session()
        with TemporaryDirectory() as td:
            repo = Path(td) / '2026' / '03' / '20'
            outcome = fetch_gameplan(
                session,
                date(2026, 3, 20),
                save_json=True,
                repository_dir=repo,
            )
            self.assertIsNotNone(outcome.snapshot_path)
            files = sorted(repo.glob('gameplan_2026-03-20_*.json'))
            self.assertEqual(len(files), 1)
            self.assertEqual(outcome.snapshot_path, files[0])
            loaded = json.loads(files[0].read_text(encoding='utf-8'))
            self.assertEqual(loaded.get('source'), 'smb_gameplan')
            self.assertEqual(loaded.get('trade_date'), '2026-03-20')
            self.assertIn('payload', loaded)
            self.assertIsInstance(loaded['payload'], (dict, list))

    def test_fetch_gameplan_today_with_fallback(self) -> None:
        session = get_session()
        raw = os.getenv('SMB_GAMEPLAN_START_DATE')
        start = date.fromisoformat(raw) if raw else None
        with TemporaryDirectory() as td:
            out = Path(td) / 'repo'
            out.mkdir(parents=True, exist_ok=True)
            outcome = fetch_gameplan_today(
                session,
                start_date=start,
                save_json=True,
                repository_dir=out,
            )
            self.assertIsInstance(outcome.data, (dict, list))
            snapshots = sorted(out.glob('gameplan_*.json'))
            self.assertGreaterEqual(len(snapshots), 1)


if __name__ == '__main__':
    unittest.main()
