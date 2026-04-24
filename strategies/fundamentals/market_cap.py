"""IB helpers to resolve equity market cap and shares outstanding."""

from xml.etree import ElementTree

from ib_async import IB
from ib_async import Stock

from trading.config import ACCOUNT_CURRENCY
from trading.market_data import get_market_price

MARKET_CAP_FIELD = 'MKTCAP'
SHARES_OUTSTANDING_FIELD = 'SHARESOUT'
_FUNDAMENTALS_ALLOWED_BY_SESSION: dict[int, bool] = {}


def _as_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.strip().replace(',', '')
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _snapshot_numeric_fields(xml_text: str) -> dict[str, float]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return {}

    out: dict[str, float] = {}
    for ratio in root.findall('.//Ratio'):
        field_name = str(ratio.attrib.get('FieldName', '')).strip().upper()
        if not field_name:
            continue
        parsed_value = _as_float(ratio.attrib.get('Value'))
        if parsed_value is None:
            parsed_value = _as_float(ratio.text)
        if parsed_value is None:
            continue
        out[field_name] = parsed_value
    return out


def _parse_report_snapshot(xml_text: str) -> tuple[float | None, float | None]:
    if not xml_text.strip():
        return None, None

    ratio_fields = _snapshot_numeric_fields(xml_text)
    market_cap = ratio_fields.get(MARKET_CAP_FIELD)
    shares_outstanding = ratio_fields.get(SHARES_OUTSTANDING_FIELD)
    return market_cap, shares_outstanding


def get_report_snapshot_field_names(ib: IB | None, symbol: str) -> list[str]:
    """Return sorted ReportSnapshot field names for one symbol."""
    if ib is None or not ib.isConnected():
        return []
    contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
    try:
        ib.qualifyContracts(contract)
        response = ib.reqFundamentalData(contract, 'ReportSnapshot')
    except Exception:
        return []
    if not isinstance(response, str) or not response.strip():
        return []
    return sorted(_snapshot_numeric_fields(response).keys())


def get_market_cap(
    ib: IB | None,
    symbol: str,
) -> tuple[float | None, float | None]:
    """Return market cap and shares outstanding for ``symbol`` from IB.

    Uses ``reqFundamentalData(..., 'ReportSnapshot')`` first. If market cap is absent
    but shares outstanding is available, computes cap via ``shares * best_price``.
    """
    if ib is None or not ib.isConnected():
        return None, None
    session_id = id(ib)
    if _FUNDAMENTALS_ALLOWED_BY_SESSION.get(session_id) is False:
        return None, None

    contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
    try:
        ib.qualifyContracts(contract)
    except Exception:
        return None, None

    xml_text = ''
    try:
        response = ib.reqFundamentalData(contract, 'ReportSnapshot')
        if isinstance(response, str):
            xml_text = response
    except Exception:
        _FUNDAMENTALS_ALLOWED_BY_SESSION[session_id] = False
        return None, None
    if not xml_text.strip():
        _FUNDAMENTALS_ALLOWED_BY_SESSION[session_id] = False
        return None, None

    _FUNDAMENTALS_ALLOWED_BY_SESSION[session_id] = True

    market_cap, shares_outstanding = _parse_report_snapshot(xml_text)
    if market_cap is not None:
        return round(market_cap, 2), shares_outstanding
    if shares_outstanding is None:
        return None, None

    best_price = get_market_price(ib, symbol)
    if best_price is None:
        return None, shares_outstanding
    computed_market_cap = shares_outstanding * best_price
    return round(computed_market_cap, 2), shares_outstanding
