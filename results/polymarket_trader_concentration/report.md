# Polymarket trader concentration & herding check

Source: `src/polymarket_trader_concentration.py`. Motivated by a direct
question about the wisdom-dashboard's aggregation: does it implicitly
assume a market's volume represents many independent opinions, when it
might really be a handful of large traders reacting to each other rather
than assessing independently?

Method: pulled full trade history (wallet-attributed, via Polymarket's
public `data-api.polymarket.com/trades` feed, no auth) for 6 live BTC/OIL
markets spanning a wide volume range, and computed two measures per
market:

- **Trader concentration**: Herfindahl-Hirschman Index (HHI) of notional
  volume by wallet. `1/HHI` = "effective number of equal-sized
  participants" this concentration is equivalent to, regardless of the
  raw wallet count.
- **Herding/momentum signature**: lag-k autocorrelation of trade
  direction (+1 = pushes the "Yes" probability up, -1 = pushes it down),
  trades ordered by time. Near 0 = trades look direction-independent;
  positive = a directional trade tends to be followed by more
  same-direction trades more often than chance.

## Results

| event | bucket | volume | trades | wallets | HHI | effective traders | top-5 share | lag-1 autocorr | lag-5 autocorr |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC touch (yearly) | ↓ 45,000 | $6,847,572 | 5,000 | 1,462 | 0.146 | **6.8** | 55.8% | 0.207 | 0.119 |
| BTC touch (yearly) | ↓ 55,000 | $6,000,672 | 5,000 | 1,353 | 0.051 | **19.6** | 44.2% | 0.192 | 0.115 |
| BTC daily (point-in-time) | 68,000-70,000 | $4,428 | 31 | 4 | 0.996 | **1.0** | 100.0% | 0.024 | 0.285 |
| BTC daily (point-in-time) | 70,000-72,000 | $1,744 | 23 | 3 | 0.517 | **1.9** | 100.0% | 0.228 | 0.542 |
| OIL touch (monthly) | ↑ $105 | $359,625 | 1,295 | 350 | 0.052 | **19.1** | 43.9% | 0.329 | 0.097 |
| OIL touch (monthly) | ↑ $110 | $356,875 | 831 | 313 | 0.050 | **20.1** | 40.4% | 0.294 | 0.152 |

## Findings

1. **Real concentration at every volume level tested**, not just thin
   markets. Even the $6-7M "yearly touch" markets -- among the highest-volume
   BTC markets on Polymarket -- have an effective independent trader count
   of only ~7-20, with the top 5 wallets alone carrying 40-56% of all
   volume. A $6.8M-volume market is not "the crowd's" $6.8M view; it's
   closer to a dozen or so large positions.
2. **Near-term daily buckets are far worse**: 1.0-1.9 effective traders --
   these markets can be literally one or two wallets setting the whole
   price. This is a different, complementary signal to the dashboard's
   existing volume-based "low confidence" flag: a market can have *some*
   volume and still be almost entirely one trader's view.
3. **Herding/momentum is consistently positive across every market
   checked** (lag-1: 0.02-0.33; all six markets, both assets, both
   touch and point-in-time). Not a one-off -- a real, persistent pattern
   consistent with traders reacting to recent price movement (anchoring,
   momentum-following) rather than each contributing a fresh, independent
   read. Note lag-5 on the two thinnest markets (23 and 31 trades total)
   is noisy -- too few trades for that estimate to be trusted on its own.

## What this changed

`wisdom-dashboard/backend/concentration.py`: every Polymarket point-in-time
distribution's weight is now discounted by `effective_traders /
REFERENCE_EFFECTIVE_TRADERS` (reference = 20, roughly the most diffuse
trading observed above; clipped to [0.05, 1.0]) before it reaches the
cross-platform mixture -- computed from the event's single highest-volume
bucket (not every bucket, to bound cost) and cached 30 minutes. See that
module's docstring for the full design, including what it deliberately
does NOT do (the herding finding isn't used for any correction -- it's
real but doesn't have as direct a translation into "how much less should
this count" as concentration does).

## Caveats

- **6 markets, one snapshot in time.** Real, but not a large or repeated
  sample -- treat the specific effective-trader numbers as illustrative of
  the *pattern* (concentration is real and common), not as precise,
  stable constants for any given market.
- **Kalshi and Manifold aren't measurable this way.** Kalshi is a
  regulated DCM with no public wallet-level trade data; Manifold wasn't
  checked. This means Polymarket markets get discounted for a problem
  that likely also exists on the other platforms, just unmeasured there --
  a real asymmetry, not evidence Polymarket is uniquely concentrated.
- **HHI concentration and the herding autocorrelation are diagnostics,
  not proof of non-independence.** A few large, well-capitalized traders
  could each hold genuinely independent views; some autocorrelation could
  reflect legitimate sequential information arrival rather than pure
  herding. Both measures are suggestive, not dispositive.
