"""Tests for ``watchlist.sources.tradertv``."""

import base64
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from watchlist.sources.tradertv import _gmail_query_for_trade_date
from watchlist.sources.tradertv import _html_to_plain_text
from watchlist.sources.tradertv import _is_plain_text_stub
from watchlist.sources.tradertv import _strip_image_lines
from watchlist.sources.tradertv import fetch_tradertv_watchlist_email_or_none
from watchlist.sources.tradertv import save_tradertv_watchlist_text

FIXTURE_DESK_DAY = date(2026, 3, 30)


def _enc(data: str) -> str:
    return base64.urlsafe_b64encode(data.encode('utf-8')).decode('ascii').rstrip('=')


class TestTraderTvWatchlist(unittest.TestCase):
    def test_query_contains_sender_and_subject(self) -> None:
        query = _gmail_query_for_trade_date(FIXTURE_DESK_DAY)
        self.assertIn('from:(tradertv-live@mail.beehiiv.com)', query)
        self.assertIn('subject:("TraderTV Watchlist - March 30, 2026")', query)
        self.assertIn('subject:("Trader TV Watchlist - March 30, 2026")', query)
        self.assertIn(' OR ', query)

    def test_plain_text_stub_detection(self) -> None:
        stub = (
            '———\n\nYou are reading a plain text version of this post. '
            'For the best experience, copy and paste this link in your browser '
            'to view the post online:\n'
            'https://tradertv-live.beehiiv.com/p/tradertv-watchlist-august-5-2026'
        )
        self.assertTrue(_is_plain_text_stub(stub))
        self.assertFalse(_is_plain_text_stub('# **Premarket Trading:**\nAAPL: TSLA'))
        self.assertFalse(_is_plain_text_stub('AAPL'))

    def test_html_to_plain_text_keeps_premarket(self) -> None:
        html = (
            '<html><body><h1>Premarket Trading</h1>'
            '<p>TRADING HIGHER: ANET +12% - beat; NVDA +1.9% - chips</p>'
            '<p>Sandisk (SNDK): reports after close</p></body></html>'
        )
        text = _html_to_plain_text(html)
        self.assertIn('Premarket Trading', text)
        self.assertIn('ANET', text)
        self.assertIn('SNDK', text)

    def test_fetch_returns_none_when_no_messages(self) -> None:
        gmail_api = MagicMock()
        gmail_api.users.return_value.messages.return_value.list.return_value.execute.return_value = {}
        out = fetch_tradertv_watchlist_email_or_none(gmail_api, FIXTURE_DESK_DAY, save_text=False)
        self.assertIsNone(out)

    def test_strip_image_lines_removes_image_metadata(self) -> None:
        raw = (
            'View image: (https://x)\n'
            'Follow image link: (https://y)\n'
            'Caption:\n'
            'Actual content line\n'
        )
        cleaned = _strip_image_lines(raw)
        self.assertEqual(cleaned, 'Actual content line')

    def test_fetch_parses_and_saves_message_body(self) -> None:
        gmail_api = MagicMock()
        gmail_api.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            'messages': [{'id': 'abc123'}],
        }
        gmail_api.users.return_value.messages.return_value.get.return_value.execute.return_value = {
            'id': 'abc123',
            'snippet': 'fallback snippet',
            'payload': {
                'headers': [
                    {'name': 'Subject', 'value': 'Trader TV Watchlist - March 30, 2026'},
                    {'name': 'From', 'value': 'TraderTV Live Research <tradertv-live@mail.beehiiv.com>'},
                    {'name': 'Reply-To', 'value': 'TraderTV Live Research <marketing@tradertv.live>'},
                    {'name': 'Date', 'value': 'Mon, 30 Mar 2026 04:49:00 -0400'},
                ],
                'parts': [
                    {
                        'mimeType': 'text/plain',
                        'body': {'data': _enc('AAPL\nTSLA\nNVDA')},
                    },
                ],
            },
        }
        with TemporaryDirectory() as td:
            repo = (
                Path(td)
                / FIXTURE_DESK_DAY.strftime('%Y')
                / FIXTURE_DESK_DAY.strftime('%m')
                / FIXTURE_DESK_DAY.strftime('%d')
            )
            outcome = fetch_tradertv_watchlist_email_or_none(
                gmail_api,
                FIXTURE_DESK_DAY,
                save_text=True,
                repository_dir=repo,
            )
            self.assertIsNotNone(outcome)
            assert outcome is not None
            self.assertEqual(outcome.message_id, 'abc123')
            self.assertIn('Trader TV Watchlist', outcome.subject)
            self.assertIn('AAPL', outcome.body_text)
            self.assertIsNotNone(outcome.snapshot_path)
            assert outcome.snapshot_path is not None
            saved = outcome.snapshot_path.read_text(encoding='utf-8')
            self.assertIn('source=tradertv_watchlist', saved)
            self.assertIn('desk_date=2026-03-30', saved)
            self.assertIn('AAPL', saved)

    def test_save_reuses_existing_snapshot_for_same_day(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td) / 'r'
            first = save_tradertv_watchlist_text(
                'AAPL',
                FIXTURE_DESK_DAY,
                subject='Trader TV Watchlist - March 30, 2026',
                from_email='tradertv-live@mail.beehiiv.com',
                date_header='Mon, 30 Mar 2026 04:49:00 -0400',
                repository_dir=repo,
            )
            second = save_tradertv_watchlist_text(
                'TSLA',
                FIXTURE_DESK_DAY,
                subject='Trader TV Watchlist - March 30, 2026',
                from_email='tradertv-live@mail.beehiiv.com',
                date_header='Mon, 30 Mar 2026 04:49:00 -0400',
                repository_dir=repo,
            )
            self.assertEqual(first, second)
            self.assertEqual(len(list(repo.glob('trader_tv_*.txt'))), 1)
            self.assertIn('AAPL', first.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
