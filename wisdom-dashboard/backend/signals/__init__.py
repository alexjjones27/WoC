"""Crowd signals that are NOT price distributions: futures forward curves,
perpetual funding, the EIA's expert forecast, CFTC positioning and retail
sentiment. They answer "where does this crowd think the price is going" or
"how is this crowd positioned", but none of them states a probability for
every price, so they never go through aggregation.py's mixture maths. The
Crowds page (/api/crowds/{asset}) shows them next to the prediction-market
and options distributions instead.

Every public function here returns plain JSON-safe dicts and never raises:
a dead source comes back as {"error": "..."} so one outage can't blank the
whole page.
"""
