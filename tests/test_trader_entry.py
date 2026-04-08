"""Integration tests for trader-specific entry pricing.

shell cmd (module — one scenario report by default)
uv run --frozen python -m tests.test_trader_entry

"""

import copy
import os
import time
import unittest
import warnings
from datetime import date
from datetime import datetime
from typing import TYPE_CHECKING

from strategies.bar_patterns.breakout import BREAK_OUT_LOOKBACK_BARS
from strategies.bar_patterns.breakout import break_out_bar_stats
from strategies.bar_patterns.breakout import breakout_limit_entry_price
from strategies.indicators.ema import ema9
from trading import config
from trading.bar_loader import load_bars
from trading.entry_mode import get_entry_mode
from trading.market_data import connect
from trading.market_data import disconnect
from trading.market_data import get_ticker_quote

if TYPE_CHECKING:
    from trading.config import NewOrderEntryPolicy
    from trading.models import TickerQuote

SYMBOL = 'NFLX'
TRADER = 'Jeff Holden'
NET_SIDE = 'long'  # 'long' | 'short' — prints quote factor for this side only

# Stdout report: '' = off | 'all' = every test | substring of method id (e.g. 'add_uses_quote').
TRADER_ENTRY_REPORT = ''


class TestTraderEntryPolicy(unittest.TestCase):
    """Validate trader-specific NEW-order pricing with IB-loaded bars."""

    ib = None
    bundle = None
    _orig_policy: dict[str, 'NewOrderEntryPolicy']

    @classmethod
    def setUpClass(cls) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=DeprecationWarning)
            cls.ib = connect(readonly=True)
        assert cls.ib is not None
        assert cls.ib.isConnected()
        cls.bundle = load_bars(cls.ib, SYMBOL)

    @classmethod
    def tearDownClass(cls) -> None:
        disconnect(cls.ib)

    def setUp(self) -> None:
        self._orig_policy = copy.deepcopy(config.NEW_ORDER_ENTRY_POLICY_BY_TRADER)
        assert self.ib is not None
        assert self.ib.isConnected()
        assert self.bundle is not None
        assert self.bundle.bars_2min

    def tearDown(self) -> None:
        config.NEW_ORDER_ENTRY_POLICY_BY_TRADER.clear()
        config.NEW_ORDER_ENTRY_POLICY_BY_TRADER.update(self._orig_policy)

    def _session_date(self) -> date:
        bundle = self.bundle
        if bundle is None or not bundle.bars_2min:
            return date.today()
        bar_dt = bundle.bars_2min[-1].date
        if isinstance(bar_dt, datetime):
            return bar_dt.date()
        return date.today()

    def _is_long(self, side: str) -> bool:
        return side == 'long'

    def _emit_trader_entry_report(self) -> bool:
        raw = os.environ.get('TRADER_ENTRY_PRINT')
        if raw is not None:
            token = raw.strip().lower()
        else:
            token = TRADER_ENTRY_REPORT.strip().lower()
        if not token or token == 'off':
            return False
        if token == 'all':
            return True
        return token in self.id().lower()

    def _print_trader_entry_block(
        self,
        *,
        passed: bool,
        trader: str,
        net_side: str,
        side: str,
        quote: 'TickerQuote',
        entry_price: float,
        anchor: float | None = None,
        ema9_price: float | None = None,
        expected: float | None = None,
        breakout_mid: float | None = None,
    ) -> None:
        """Structured stdout for the current test only when TRADER_ENTRY_REPORT matches."""
        if not self._emit_trader_entry_report():
            return
        # unittest writes ``.`` without a trailing newline; start block on a fresh line.
        print()
        print('test_trader_entry')
        print(f'[result] = {passed}')
        print(f'[symbol] = {SYMBOL}')
        print(f'[session_date] = {self._session_date()}')
        print(f'[trader] = {trader}')
        print(f'[net_side] = {net_side}')
        if breakout_mid is not None:
            print(f'[breakout_mid] = {float(breakout_mid):.2f}')
            print(f'use ema9 = {False}')
        elif ema9_price is not None and expected is not None:
            assert anchor is not None
            ema_f = float(ema9_price)
            anchor_lt_ema = anchor < ema_f
            if self._is_long(side):
                print(f'[ask < ema9] = {anchor:.2f} < {ema_f:.2f} = {anchor_lt_ema}')
            else:
                print(f'[bid < ema9] = {anchor:.2f} < {ema_f:.2f} = {anchor_lt_ema}')
            use_ema = round(ema_f, 2) == round(float(expected), 2)
            print(f'use ema9 = {use_ema}')
        else:
            print(f'use ema9 = {False}')
        if self._is_long(side):
            ask = quote.ask
            assert ask is not None and ask > 0, 'IB ask unavailable for print'
            print(f'[ask] = {float(ask):.2f}')
        else:
            bid = quote.bid
            assert bid is not None and bid > 0, 'IB bid unavailable for print'
            print(f'[bid] = {float(bid):.2f}')
        print(f'[entry_price] = {float(entry_price):.2f}')

    def _ask_for_long_or_skip(self, quote: 'TickerQuote', reason: str) -> float:
        ask = quote.ask
        assert ask is not None and ask > 0, reason
        return float(ask)

    def _bid_for_short_or_skip(self, quote: 'TickerQuote', reason: str) -> float:
        bid = quote.bid
        assert bid is not None and bid > 0, reason
        return float(bid)

    def _best_price(self, quote: 'TickerQuote') -> float:
        best_price = quote.best_price()
        assert best_price is not None and best_price > 0
        return float(best_price)

    def _quote(self) -> 'TickerQuote':
        ib = self.ib
        assert ib is not None
        assert ib.isConnected()
        # reqMktData can take a moment.
        quote = get_ticker_quote(ib, SYMBOL)
        if quote is not None:
            return quote
        time.sleep(0.1)
        quote2 = get_ticker_quote(ib, SYMBOL)
        assert quote2 is not None
        return quote2

    def test_new_uses_ema9_clamped_to_quote(self) -> None:
        """NEW + bundle: policy trader uses EMA9 clamped to ask (long) or bid (short)."""
        side = NET_SIDE
        bundle = self.bundle
        quote = self._quote()
        assert bundle is not None
        ema_price = ema9(bundle)
        assert ema_price is not None
        market_entry_price = self._best_price(quote)
        is_long = self._is_long(side)
        if is_long:
            anchor = self._ask_for_long_or_skip(quote, 'IB ask unavailable')
            expected = round(min(float(ema_price), anchor), 2)
        else:
            anchor = self._bid_for_short_or_skip(quote, 'IB bid unavailable')
            expected = round(max(float(ema_price), anchor), 2)

        mode = get_entry_mode(
            ib=None,
            trader=TRADER,
            change_type='NEW',
            symbol=SYMBOL,
            market_entry_price=market_entry_price,
            is_long=is_long,
            bundle=bundle,
            quote=quote,
        )
        passed = mode.entry_price == expected
        self._print_trader_entry_block(
            passed=passed,
            trader=TRADER,
            net_side=NET_SIDE,
            side=side,
            quote=quote,
            entry_price=mode.entry_price,
            anchor=anchor,
            ema9_price=float(ema_price),
            expected=expected,
        )
        self.assertEqual(mode.entry_price, expected)

    def test_add_uses_quote_side_price(self) -> None:
        """ADD ignores NEW policy; limit at quote side (ask long / bid short)."""
        side = NET_SIDE
        bundle = self.bundle
        quote = self._quote()
        assert bundle is not None
        is_long = self._is_long(side)
        if is_long:
            anchor = self._ask_for_long_or_skip(quote, 'IB ask unavailable')
        else:
            anchor = self._bid_for_short_or_skip(quote, 'IB bid unavailable')
        market_entry_price = self._best_price(quote)
        mode = get_entry_mode(
            ib=None,
            trader=TRADER,
            change_type='ADD',
            symbol=SYMBOL,
            market_entry_price=market_entry_price,
            is_long=is_long,
            bundle=bundle,
            quote=quote,
        )
        passed = mode.entry_price == anchor
        self._print_trader_entry_block(
            passed=passed,
            trader=TRADER,
            net_side=NET_SIDE,
            side=side,
            quote=quote,
            entry_price=mode.entry_price,
        )
        self.assertEqual(mode.entry_price, anchor)

    def test_new_without_bundle_uses_quote_side_price(self) -> None:
        """NEW without bundle: quote side only (no EMA from bars)."""
        side = NET_SIDE
        quote = self._quote()
        is_long = self._is_long(side)
        if is_long:
            anchor = self._ask_for_long_or_skip(quote, 'IB ask unavailable')
        else:
            anchor = self._bid_for_short_or_skip(quote, 'IB bid unavailable')
        market_entry_price = self._best_price(quote)
        mode = get_entry_mode(
            ib=None,
            trader=TRADER,
            change_type='NEW',
            symbol=SYMBOL,
            market_entry_price=market_entry_price,
            is_long=is_long,
            bundle=None,
            quote=quote,
        )
        passed = mode.entry_price == anchor
        self._print_trader_entry_block(
            passed=passed,
            trader=TRADER,
            net_side=NET_SIDE,
            side=side,
            quote=quote,
            entry_price=mode.entry_price,
        )
        self.assertEqual(mode.entry_price, anchor)

    def test_breakout_override_wins_else_fallback_applies(self) -> None:
        side = NET_SIDE
        is_long = self._is_long(side)
        ib = self.ib
        bundle = self.bundle
        quote = self._quote()
        assert ib is not None
        assert bundle is not None
        assert len(bundle.bars_2min_rth) >= BREAK_OUT_LOOKBACK_BARS

        stats = break_out_bar_stats(
            bundle,
            lookback_bars=BREAK_OUT_LOOKBACK_BARS,
            ib=ib,
            symbol=SYMBOL,
        )
        market_entry_price = self._best_price(quote)
        mode = get_entry_mode(
            ib=ib,
            trader=TRADER,
            change_type='NEW',
            symbol=SYMBOL,
            market_entry_price=market_entry_price,
            is_long=is_long,
            bundle=bundle,
            quote=quote,
        )
        if stats.breakout and stats.midpoint_of_breakout_bar is not None:
            expected = breakout_limit_entry_price(
                float(stats.midpoint_of_breakout_bar),
                is_long,
                quote,
            )
            passed = mode.entry_price == expected
            self._print_trader_entry_block(
                passed=passed,
                trader=TRADER,
                net_side=NET_SIDE,
                side=side,
                quote=quote,
                entry_price=mode.entry_price,
                breakout_mid=float(stats.midpoint_of_breakout_bar),
            )
            self.assertEqual(mode.entry_price, expected)
            return

        if is_long:
            anchor = self._ask_for_long_or_skip(
                quote, 'IB ask unavailable for non-breakout fallback validation'
            )
        else:
            anchor = self._bid_for_short_or_skip(
                quote, 'IB bid unavailable for non-breakout fallback validation'
            )
        ema_price = ema9(bundle)
        assert ema_price is not None
        if is_long:
            expected = round(min(float(ema_price), anchor), 2)
        else:
            expected = round(max(float(ema_price), anchor), 2)
        passed = mode.entry_price == expected
        self._print_trader_entry_block(
            passed=passed,
            trader=TRADER,
            net_side=NET_SIDE,
            side=side,
            quote=quote,
            entry_price=mode.entry_price,
            anchor=anchor,
            ema9_price=float(ema_price),
            expected=expected,
        )
        self.assertEqual(mode.entry_price, expected)


if __name__ == '__main__':
    # ``python -m tests.test_trader_entry`` — show one scenario unless user set env.
    if 'TRADER_ENTRY_PRINT' not in os.environ:
        os.environ['TRADER_ENTRY_PRINT'] = 'new_uses_ema9'
    unittest.main(module=__name__, verbosity=0)
