"""Tests for ``watchlist.run_sources`` orchestration."""

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

from watchlist.run_sources import _run_market_rundown
from watchlist.sources.market_rundown import MarketRundownOutcome

THURSDAY = date(2026, 4, 2)


class TestRunSourcesMarketRundown(unittest.TestCase):
    """Thursday ingest retries when the doc header date != desk date."""

    def test_thursday_retries_when_strict_match_fails(self) -> None:
        session = MagicMock()
        strict_flags: list[bool] = []

        def fake_fetch(
            _session: object,
            trade_date: date,
            *,
            save_text: bool = True,
            require_matching_trade_date: bool = True,
            **_kw: object,
        ) -> MarketRundownOutcome:
            strict_flags.append(require_matching_trade_date)
            if require_matching_trade_date:
                raise ValueError('Market rundown date does not match requested trade date')
            snap = Path('/tmp/market_rundown_snapshot.txt')
            return MarketRundownOutcome('body', snap, trade_date, date(2026, 4, 1))

        with patch('watchlist.run_sources.fetch_market_rundown', side_effect=fake_fetch):
            status, msg = _run_market_rundown(session, THURSDAY, force=False)

        self.assertEqual(status, 'ok')
        self.assertEqual(strict_flags, [True, False])
        self.assertIn('path=', msg)
        self.assertIn('doc header date differed', msg)


if __name__ == '__main__':
    unittest.main()
