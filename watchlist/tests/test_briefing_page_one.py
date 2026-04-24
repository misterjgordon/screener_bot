"""Tests for Briefing Page One HTML extraction.

shell cmd
uv run --frozen python -m unittest watchlist.tests.test_briefing_page_one -v
"""

import unittest

from watchlist.sources.briefing_page_one import extract_briefing_page_one_plain_text

_MINIMAL_PAGE_ONE_HTML = """\
<div id="Content"><div class="row">
  <div class="col-xs-12">
    <div class="colTime">Last Updated: 09-Apr-26 09:08 ET</div>
    <div class="colTitle">Test headline for parser</div>
    <div class="colArticle"><p>First paragraph with enough characters to pass the minimum
    length check for the Briefing Page One ingestor. Second sentence continues the body.</p>
    <p>Second paragraph adds more macro context for the day trader report pipeline.</p></div>
  </div>
</div>
"""


class TestBriefingPageOneExtract(unittest.TestCase):
    """``extract_briefing_page_one_plain_text`` maps legacy HTML to plain text."""

    def test_extract_includes_time_title_and_article(self) -> None:
        text = extract_briefing_page_one_plain_text(_MINIMAL_PAGE_ONE_HTML)
        self.assertIn('Last Updated:', text)
        self.assertIn('Test headline for parser', text)
        self.assertIn('First paragraph', text)
        self.assertIn('Second paragraph', text)


if __name__ == '__main__':
    unittest.main()
