"""Tests for watchlist ticker union using committed repository snapshots (no synthetic sources)."""

import json
import unittest
from datetime import date
from pathlib import Path

from watchlist.tickers_on_watchlist import _symbols_from_rundown_text
from watchlist.tickers_on_watchlist import _symbols_from_tradertv_text
from watchlist.tickers_on_watchlist import tickers_on_watchlist

watchlist_date = date(2026, 3, 26)

_NEW_FORMAT_TRADERTV_SNIPPET = """
Premarket Trading
TRADING HIGHER:
ANET +12% - AI-networking beat; BKNG +6.6% - travel demand; NVDA +1.9% - chip share
TRADING LOWER:
AMD -8.6% - SpaceX chip-share loss; INTC -1.4% - Nvidia preference
Stocks in Focus
SpaceX - SPCX
SPCX, TSLA, NVDA
Sandisk (SNDK):
Sandisk reports after the close.
Western Digital (WDC):
Western Digital reports fiscal Q4 after the close.
"""


class TestTickersOnWatchlist(unittest.TestCase):
    """Only the desk date is fixed: gameplan/rundown content comes from ``watchlist/repository``."""

    def test_symbols_from_new_html_derived_tradertv_format(self) -> None:
        symbols = _symbols_from_tradertv_text(_NEW_FORMAT_TRADERTV_SNIPPET)
        self.assertEqual(
            symbols,
            ['ANET', 'BKNG', 'NVDA', 'AMD', 'INTC', 'SPCX', 'TSLA', 'SNDK', 'WDC'],
        )

    def _p_repository_day(self, desk: date) -> Path:
        return (
            Path(__file__).resolve().parent.parent
            / 'repository'
            / str(desk.year)
            / f'{desk.month:02d}'
            / f'{desk.day:02d}'
        )

    def test_real_gameplan_payload_matches_tickers_on_watchlist(self) -> None:
        desk = watchlist_date
        p_day = self._p_repository_day(desk)
        paths = sorted(p_day.glob('gameplan_*.json'))
        if not paths:
            self.skipTest(f'no gameplan_*.json under {p_day}')

        wrap = json.loads(paths[0].read_text(encoding='utf-8'))
        payload = wrap.get('payload')
        if not isinstance(payload, dict):
            self.skipTest('gameplan wrapper missing payload object')
        stocks = payload.get('stocks')
        if not isinstance(stocks, list):
            self.skipTest('gameplan payload missing stocks list')

        payload_symbols = {str(s['ticker']).upper() for s in stocks if isinstance(s, dict) and s.get('ticker')}

        rows = tickers_on_watchlist(desk, repository_dir=p_day, save_json=False)
        from_gameplan = {r.symbol for r in rows if r.source_id == 'smb_gameplan'}

        self.assertEqual(from_gameplan, payload_symbols)
        for r in rows:
            self.assertEqual(r.trade_date, desk)
            self.assertIsNone(r.atr_14)
            self.assertIsNone(r.percent_of_avg_volume)

        print(
            f'gameplan | {desk.isoformat()} | ticker_union_from_payload = True\n'
            f'count = {len(from_gameplan)} | True'
        )

    def test_real_market_rundown_lines_match_tickers_on_watchlist(self) -> None:
        desk = watchlist_date
        p_day = self._p_repository_day(desk)
        paths = sorted(p_day.glob('market_rundown_*.txt'))
        if not paths:
            self.skipTest(f'no market_rundown_*.txt under {p_day}')

        body = paths[0].read_text(encoding='utf-8')
        line_symbols = _symbols_from_rundown_text(body)

        rows = tickers_on_watchlist(desk, repository_dir=p_day, save_json=False)
        from_rundown = {r.symbol for r in rows if r.source_id == 'market_rundown'}

        self.assertEqual(from_rundown, set(line_symbols))
        for r in rows:
            if r.source_id == 'market_rundown':
                self.assertEqual(r.trade_date, desk)
                self.assertIsNone(r.atr_14)
                self.assertIsNone(r.percent_of_avg_volume)

        print(
            f'market_rundown | {desk.isoformat()} | line_tickers_match = True\n'
            f'count = {len(from_rundown)} | True'
        )

    def test_real_tradertv_lines_match_tickers_on_watchlist(self) -> None:
        desk = watchlist_date
        p_day = self._p_repository_day(desk)
        paths = sorted(p_day.glob('trader_tv_*.txt'))
        if not paths:
            self.skipTest(f'no trader_tv_*.txt under {p_day}')

        body = paths[0].read_text(encoding='utf-8')
        line_symbols = _symbols_from_tradertv_text(body)

        rows = tickers_on_watchlist(desk, repository_dir=p_day, save_json=False)
        from_tradertv = {r.symbol for r in rows if r.source_id == 'tradertv_watchlist'}

        self.assertEqual(from_tradertv, set(line_symbols))
        for r in rows:
            if r.source_id == 'tradertv_watchlist':
                self.assertEqual(r.trade_date, desk)
                self.assertIsNone(r.atr_14)
                self.assertIsNone(r.percent_of_avg_volume)

        print(
            f'tradertv_watchlist | {desk.isoformat()} | line_tickers_match = True\n'
            f'count = {len(from_tradertv)} | True'
        )


if __name__ == '__main__':
    unittest.main()
