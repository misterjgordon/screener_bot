"""Tests for ``watchlist.sources.market_rundown``."""

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from watchlist.sources.market_rundown import MARKET_RUNDOWN_EXPORT_URL
from watchlist.sources.market_rundown import fetch_market_rundown
from watchlist.sources.market_rundown import report_date_from_snapshot_file
from watchlist.sources.market_rundown import save_market_rundown_text

# Shared desk/calendar day for assertions and paths (e.g. last Thursday when today is Sat 2026-03-28).
FIXTURE_DESK_DAY = date(2026, 3, 26)


class TestMarketRundown(unittest.TestCase):
    """Mocked HTTP; no live Google fetch."""

    def test_fetch_saves_under_repository_dir(self) -> None:
        session = MagicMock()
        response = MagicMock()
        response.text = 'Mar 26, 2026\n\nBody line.'
        response.raise_for_status = MagicMock()
        session.get.return_value = response

        expected_name_prefix = f'market_rundown_{FIXTURE_DESK_DAY.isoformat()}_'
        expected_desk_date_token = f'desk_date={FIXTURE_DESK_DAY.isoformat()}'
        with TemporaryDirectory() as td:
            repo = (
                Path(td)
                / FIXTURE_DESK_DAY.strftime('%Y')
                / FIXTURE_DESK_DAY.strftime('%m')
                / FIXTURE_DESK_DAY.strftime('%d')
            )
            outcome = fetch_market_rundown(
                session,
                FIXTURE_DESK_DAY,
                save_text=True,
                repository_dir=repo,
            )

            session.get.assert_called_once()
            call_args = session.get.call_args
            self.assertEqual(call_args[0][0], MARKET_RUNDOWN_EXPORT_URL)
            self.assertEqual(outcome.text.strip(), 'Mar 26, 2026\n\nBody line.')
            self.assertEqual(outcome.report_date, FIXTURE_DESK_DAY)
            self.assertIsNotNone(outcome.snapshot_path)
            saved = outcome.snapshot_path
            assert saved is not None
            self.assertTrue(saved.name.startswith(expected_name_prefix))
            self.assertTrue(saved.name.endswith('.txt'))
            raw = saved.read_text(encoding='utf-8')
            self.assertIn(expected_desk_date_token, raw)
            self.assertIn('Body line.', raw)

    def test_fetch_reuses_snapshot_when_one_exists_for_desk_day(self) -> None:
        """Second fetch does not add another file for the same desk day."""
        session = MagicMock()
        response = MagicMock()
        response.text = 'Mar 26, 2026\n\nNew body from API.'
        response.raise_for_status = MagicMock()
        session.get.return_value = response

        with TemporaryDirectory() as td:
            repo = (
                Path(td)
                / FIXTURE_DESK_DAY.strftime('%Y')
                / FIXTURE_DESK_DAY.strftime('%m')
                / FIXTURE_DESK_DAY.strftime('%d')
            )
            prior = save_market_rundown_text('prior body', FIXTURE_DESK_DAY, repository_dir=repo)
            outcome = fetch_market_rundown(
                session,
                FIXTURE_DESK_DAY,
                save_text=True,
                repository_dir=repo,
            )

            self.assertEqual(outcome.snapshot_path, prior)
            self.assertEqual(outcome.report_date, FIXTURE_DESK_DAY)
            self.assertEqual(len(list(repo.glob('market_rundown_*.txt'))), 1)
            self.assertIn('prior body', prior.read_text(encoding='utf-8'))
            self.assertIn('New body from API.', outcome.text)

    def test_fetch_saves_when_mismatch_but_strict_disabled(self) -> None:
        session = MagicMock()
        response = MagicMock()
        response.text = 'Mar 27, 2026\n\nBody line.'
        response.raise_for_status = MagicMock()
        session.get.return_value = response

        with TemporaryDirectory() as td:
            repo = (
                Path(td)
                / FIXTURE_DESK_DAY.strftime('%Y')
                / FIXTURE_DESK_DAY.strftime('%m')
                / FIXTURE_DESK_DAY.strftime('%d')
            )
            outcome = fetch_market_rundown(
                session,
                FIXTURE_DESK_DAY,
                save_text=True,
                require_matching_trade_date=False,
                repository_dir=repo,
            )

            self.assertEqual(outcome.report_date, date(2026, 3, 27))
            self.assertIsNotNone(outcome.snapshot_path)
            assert outcome.snapshot_path is not None
            self.assertEqual(len(list(repo.glob('market_rundown_*.txt'))), 1)

    def test_report_date_from_snapshot_file_skips_hash_header(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td) / 'r'
            path = save_market_rundown_text('ignored', FIXTURE_DESK_DAY, repository_dir=repo)
            raw = path.read_text(encoding='utf-8')
            path.write_text(
                raw.replace('ignored', 'Mar 27, 2026\n\nBody'),
                encoding='utf-8',
            )
            self.assertEqual(report_date_from_snapshot_file(path), date(2026, 3, 27))

    def test_fetch_raises_when_report_date_does_not_match_trade_date(self) -> None:
        session = MagicMock()
        response = MagicMock()
        response.text = 'Mar 27, 2026\n\nBody line.'
        response.raise_for_status = MagicMock()
        session.get.return_value = response

        with TemporaryDirectory() as td:
            repo = Path(td) / 'r'
            with self.assertRaises(ValueError):
                fetch_market_rundown(
                    session,
                    FIXTURE_DESK_DAY,
                    save_text=True,
                    repository_dir=repo,
                )

            self.assertEqual(len(list(repo.glob('market_rundown_*.txt'))), 0)

    def test_save_header_and_body(self) -> None:
        """Saved file is text with header; spot-check parse-friendly lines."""
        expected_desk_date_token = f'desk_date={FIXTURE_DESK_DAY.isoformat()}'
        with TemporaryDirectory() as td:
            repo = Path(td) / 'r'
            path = save_market_rundown_text('hello', FIXTURE_DESK_DAY, repository_dir=repo)
            content = path.read_text(encoding='utf-8')
        lines = content.splitlines()
        meta = lines[0].lstrip('# ').strip()
        self.assertTrue(meta.startswith(expected_desk_date_token))
        self.assertIn('fetched_at_utc=', meta)
        self.assertEqual(lines[-1], 'hello')


if __name__ == '__main__':
    unittest.main()
