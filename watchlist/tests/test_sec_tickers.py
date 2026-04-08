"""Tests for SEC all-tickers models (no network)."""

import unittest
from pathlib import Path

from trading.models import SecTickers
from trading.models import load_all_tickers

P_SAMPLE = (
    Path(__file__).resolve().parent.parent.parent
    / 'trading'
    / 'data'
    / 'symbols'
    / 'all_tickers.sample.json'
)


class TestSecTickers(unittest.TestCase):
    """Parse all_tickers sample file."""

    def test_load_sample_json(self) -> None:
        bundle = load_all_tickers(P_SAMPLE)
        self.assertEqual(len(bundle.rows), 5)
        self.assertEqual(bundle.fields, ('cik', 'name', 'ticker', 'exchange'))
        upper = bundle.ticker_set_upper()
        self.assertIn('AAPL', upper)
        self.assertIn('SPY', upper)
        self.assertIn('BRK-B', upper)

    def test_from_json_dict_roundtrip(self) -> None:
        bundle = load_all_tickers(P_SAMPLE)
        raw = {
            'fields': list(bundle.fields),
            'data': [
                [row.cik, row.name, row.ticker, row.exchange]
                for row in bundle.rows
            ],
        }
        again = SecTickers.from_json_dict(raw)
        self.assertEqual(again, bundle)


if __name__ == '__main__':
    unittest.main()
