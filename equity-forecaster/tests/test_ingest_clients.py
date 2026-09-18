"""Parser tests for the keyless per-analyst sources.

The payload snippets below are trimmed copies of real responses captured on
2026-09-18. Testing against captured bytes rather than a live call keeps the
suite offline and, more importantly, makes a silent upstream schema change show
up as a failing test instead of as an empty panel.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.ingest.finviz import _money, _split_arrow, parse_quote_page
from src.ingest.nasdaq import parse_target_price
from src.ingest.stockanalysis import decode_devalue, parse_ratings

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)

# --- finviz ----------------------------------------------------------------

FINVIZ_PAGE = (
    'junk before'
    '{"dateTimestamp":1769749200,"eventType":"chartEvent/ratings","ratings":['
    '{"action":"Reiterated","analyst":"JP Morgan","rating":"Overweight",'
    '"targetPrice":"$315 \\u0026rarr; $325"},'
    '{"action":"Reiterated","analyst":"Monness Crespi \\u0026amp; Hardt",'
    '"rating":"Buy","targetPrice":"$300 \\u0026rarr; $315"}]}'
    '{"dateTimestamp":1770613200,"eventType":"chartEvent/ratings","ratings":['
    '{"action":"Downgrade","analyst":"Melius","rating":"Buy \\u0026rarr; Hold",'
    '"targetPrice":""}]}'
    '{"dateTimestamp":1770613200,"eventType":"chartEvent/dividends","ordinary":0.91}'
    'junk after'
)


def test_split_arrow_handles_every_live_shape():
    assert _split_arrow("$600 &rarr; $650") == ("$600", "$650")
    assert _split_arrow("Buy &rarr; Hold") == ("Buy", "Hold")
    assert _split_arrow("$392") == (None, "$392")
    assert _split_arrow("") == (None, None)
    assert _split_arrow(None) == (None, None)


def test_money_strips_currency_and_separators():
    assert _money("$1,250.50") == 1250.5
    assert _money("$392") == 392.0
    assert _money("$0") is None       # a zero target is missing data, not a forecast
    assert _money("") is None
    assert _money("n/a") is None


def test_finviz_parses_prior_and_new_values():
    recs = parse_quote_page(FINVIZ_PAGE, "AAPL", NOW)
    assert len(recs) == 3
    jpm = next(r for r in recs if r.analyst_firm == "JP Morgan")
    assert jpm.price_target_prev == 315.0 and jpm.price_target == 325.0
    assert jpm.rating == 4 and jpm.rating_prev is None   # single rating, no arrow
    assert jpm.action_date == date(2026, 1, 30)
    assert jpm.source == "finviz" and jpm.analyst_name is None


def test_finviz_parses_a_rating_change():
    recs = parse_quote_page(FINVIZ_PAGE, "AAPL", NOW)
    melius = next(r for r in recs if r.analyst_firm == "Melius")
    assert melius.rating_prev == 4 and melius.rating == 3   # Buy -> Hold
    assert melius.price_target is None                      # empty target string


def test_finviz_unescapes_firm_names():
    recs = parse_quote_page(FINVIZ_PAGE, "AAPL", NOW)
    assert any(r.analyst_firm == "Monness Crespi & Hardt" for r in recs)


def test_finviz_ignores_non_ratings_events():
    assert all(r.analyst_firm != "0.91" for r in parse_quote_page(FINVIZ_PAGE, "AAPL", NOW))


def test_finviz_timestamps_read_as_utc_give_weekday_dates():
    """Finviz stamps midnight US/Eastern. Reading in UTC recovers the calendar
    date; a fixed -5h read shifts action dates onto the previous Sunday."""
    recs = parse_quote_page(FINVIZ_PAGE, "AAPL", NOW)
    assert all(r.action_date.weekday() < 5 for r in recs)


def test_finviz_respects_date_bounds():
    recs = parse_quote_page(FINVIZ_PAGE, "AAPL", NOW)
    late = [r for r in recs if r.action_date >= date(2026, 2, 1)]
    assert len(late) == 1 and late[0].analyst_firm == "Melius"


# --- stockanalysis ---------------------------------------------------------

def _devalue_payload():
    """A devalue-encoded node: every integer is a reference, not a literal."""
    flat = [
        {"ratings": 1, "widget": 12},          # 0 root
        [2],                                   # 1 ratings list
        {"action_rt": 3, "pt_now": 4, "pt_old": 5, "firm": 6, "analyst": 7,
         "slug": 8, "date": 9, "rating_new": 10, "rating_old": 11,
         "time": 14, "curr": 15, "scores": 16},  # 2
        "Maintains", 296, 270, "UBS", "David Vogt", "david-vogt",
        "2026-09-18", "Hold", "",               # 3..11
        {"count": 13}, 44,                      # 12 widget, 13
        "07:25:15", "USD", {"stars": 17}, 4.4,  # 14..17
    ]
    return {"nodes": [None, {"type": "data", "data": [0]},
                      {"type": "data", "data": flat}]}


def test_decode_devalue_resolves_references_not_literals():
    flat = [{"a": 1, "b": 2}, "hello", [3], 42]
    assert decode_devalue(flat, 0) == {"a": "hello", "b": [42]}


def test_decode_devalue_treats_negative_indices_as_absent():
    assert decode_devalue([{"a": -1, "b": -2}], 0) == {"a": None, "b": None}


def test_stockanalysis_parses_the_full_schema():
    recs = parse_ratings(_devalue_payload(), "AAPL", NOW)
    assert len(recs) == 1
    r = recs[0]
    assert r.analyst_firm == "UBS" and r.analyst_name == "David Vogt"
    assert r.price_target == 296.0 and r.price_target_prev == 270.0
    assert r.rating == 3 and r.rating_prev is None      # rating_old was ""
    assert r.action_date == date(2026, 9, 18)
    assert r.source == "stockanalysis"


def test_stockanalysis_keeps_the_intraday_timestamp_out_of_action_date():
    r = parse_ratings(_devalue_payload(), "AAPL", NOW)[0]
    assert r.vendor_published_at is not None
    assert r.vendor_published_at.hour == 7
    # The event date is the vendor's date field, never derived from the clock.
    assert r.action_date == date(2026, 9, 18)


def test_stockanalysis_quarantines_the_vendor_skill_scores():
    """Those scores are computed over the analyst's whole history, including
    everything after this record's date. They must be impossible to pick up as
    a point-in-time skill weight by accident."""
    r = parse_ratings(_devalue_payload(), "AAPL", NOW)[0]
    assert "lookahead_contaminated_scores" in r.extra
    assert "scores" not in r.extra
    assert r.extra["lookahead_contaminated_scores"] == {"stars": 4.4}


def test_stockanalysis_returns_nothing_when_the_node_is_missing():
    assert parse_ratings({"nodes": [None]}, "AAPL", NOW) == []


# --- nasdaq ----------------------------------------------------------------

NASDAQ_PAYLOAD = {
    "data": {
        "consensusOverview": {"lowPriceTarget": 245.0, "highPriceTarget": 400.0,
                              "priceTarget": 336.26, "buy": 16, "sell": 4, "hold": 10},
        "historicalConsensus": [
            {"x": 1756684800, "y": 232.14,
             "z": {"buy": 13, "hold": 13, "sell": 2, "date": "09/01/2025"}},
            {"x": 1759276800, "y": 255.45,
             "z": {"buy": 17, "hold": 12, "sell": 3, "date": "10/01/2025"}},
        ],
    }
}


def test_nasdaq_parses_current_and_dated_history():
    current, history = parse_target_price(NASDAQ_PAYLOAD, "AAPL", NOW)
    assert current.target_mean == 336.26 and current.n_analysts == 30
    assert current.target_high == 400.0 and current.target_low == 245.0
    assert [h.as_of for h in history] == [date(2025, 9, 1), date(2025, 10, 1)]
    assert history[0].target_mean == 232.14 and history[0].n_analysts == 28


def test_nasdaq_does_not_backfill_todays_band_onto_old_observations():
    """Stamping the current high/low onto a 2025 point would be exactly the
    restatement contamination the store exists to prevent."""
    _, history = parse_target_price(NASDAQ_PAYLOAD, "AAPL", NOW)
    assert all(h.target_high is None and h.target_low is None for h in history)


def test_nasdaq_handles_an_empty_payload():
    current, history = parse_target_price({}, "AAPL", NOW)
    assert current.target_mean is None and history == []


def test_consensus_clients_refuse_to_emit_panel_rows():
    """Both aggregated sources must be unusable as a panel input by construction."""
    from src.ingest.finnhub import FinnhubConsensusClient
    from src.ingest.nasdaq import NasdaqConsensusClient

    for client in (NasdaqConsensusClient(), FinnhubConsensusClient()):
        with pytest.raises(NotImplementedError, match="consensus"):
            client.fetch("AAPL")
