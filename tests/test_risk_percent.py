"""Unit tests for derived `risk_%` (risk fraction) calculation."""

import unittest

from trading.config import DAILY_STOP
from trading.config import SCREENER_DAILY_STOP_FRACTION
from trading.trade_mgmt import compute_risk_percent_and_trade_stop_amount
from trading.trade_mgmt import round_risk_fraction

# =========================================================
# Constants (mirroring the style of test_trade_mgmt.py)
# =========================================================
TRADER = 'Justin Spero'
MAGNITUDE_0_100 = 48.0
ENTRY_PRICE = 25.3
STOP_PRICE = 21.21


class TestRiskPercent(unittest.TestCase):
    def test_risk_percent_returns_fraction_and_risk_dollars(self) -> None:
        daily_stop = DAILY_STOP

        out = compute_risk_percent_and_trade_stop_amount(
            trader=TRADER,
            magnitude_0_100=MAGNITUDE_0_100,
            entry_price=ENTRY_PRICE,
            stop_price=STOP_PRICE,
        )
        assert out is not None
        risk_fraction, risk_dollars = out
        print(f'risk_% (fraction) = {risk_fraction:.2f}  risk_dollars = {risk_dollars:.2f}')

        raw_fraction = SCREENER_DAILY_STOP_FRACTION * (abs(MAGNITUDE_0_100) / 100.0)
        expected_risk_fraction = round_risk_fraction(raw_fraction)
        expected_risk_dollars = expected_risk_fraction * daily_stop

        self.assertAlmostEqual(risk_dollars, expected_risk_dollars, places=6)
        self.assertAlmostEqual(risk_fraction, expected_risk_fraction, places=6)


if __name__ == '__main__':
    unittest.main(buffer=False)
