"""Unit tests for watchlist session range JSON helpers (no market data objects)."""

import unittest
from datetime import date

from strategies.indicators.session_range import DeskSessionRanges
from strategies.indicators.session_range import SessionOhlcAdr
from watchlist.session_range_export import desk_session_ranges_to_dict
from watchlist.session_range_export import session_range_json_filename


class TestSessionRangeExport(unittest.TestCase):
    """Serialization and naming for session range export."""

    def test_desk_session_ranges_to_dict_roundtrip_keys(self) -> None:
        sample = SessionOhlcAdr(
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            change=1.5,
            adr_change_percent=0.1,
        )
        empty = SessionOhlcAdr(
            open=None,
            high=None,
            low=None,
            close=None,
            change=None,
            adr_change_percent=None,
        )
        ranges = DeskSessionRanges(
            prior_day_ah_session=sample,
            pm_session=empty,
            opening_range_session=sample,
            morning_session=empty,
            afternoon_session=sample,
            closing_session=empty,
        )
        d = desk_session_ranges_to_dict(ranges)
        self.assertEqual(
            set(d.keys()),
            {
                'prior_day_ah_session',
                'pm_session',
                'opening_range_session',
                'morning_session',
                'afternoon_session',
                'closing_session',
            },
        )
        self.assertEqual(d['prior_day_ah_session']['open'], 1.0)
        self.assertEqual(d['prior_day_ah_session']['change'], 1.5)
        self.assertEqual(d['prior_day_ah_session']['adr_change_percent'], 0.1)
        self.assertIsNone(d['pm_session']['close'])

    def test_session_range_json_filename(self) -> None:
        desk = date(2026, 4, 23)
        name = session_range_json_filename(desk)
        self.assertEqual(name, 'session_range_2026_04_23.json')


if __name__ == '__main__':
    unittest.main()
