# Smart-money consensus: top 10 active hedge funds, 30-JUN-2026

Ranked from SEC 13F filings: restricted to a curated list of well-known ACTIVE, concentrated stock-picking funds (excludes BlackRock/Vanguard/State Street-style passive giants and quant/multi-strategy shops with thousands of systematic positions -- see src/sec_13f_wisdom.py for why), then ranked by their own reported 13F portfolio value.

## The panel

| Fund | 13F value | Positions |
|---|---|---|
| Berkshire Hathaway Inc | $299.3B | 89 |
| COATUE MANAGEMENT LLC | $48.6B | 211 |
| VIKING GLOBAL INVESTORS LP | $35.1B | 90 |
| TIGER GLOBAL MANAGEMENT LLC | $24.0B | 46 |
| Elliott Investment Management L.P. | $22.7B | 29 |
| FARALLON CAPITAL MANAGEMENT, L.L.C. | $21.7B | 89 |
| PERSHING SQUARE INC. | $19.5B | 15 |
| LONE PINE CAPITAL LLC | $16.4B | 34 |
| ICAHN CARL C | $8.3B | 18 |
| SOROS FUND MANAGEMENT LLC | $8.1B | 266 |

## Consensus portfolio (dollar-value weighted across all 10 funds)

| Ticker | Company | Held by | Weight | Total $ across funds |
|---|---|---|---|---|
| AAPL | Apple Inc | 2/10 | 18.8% | $66.10B |
| AXP | American Express Co | 1/10 | 14.6% | $51.28B |
| KO | Coca Cola Co | 2/10 | 9.3% | $32.52B |
| GOOGL | Alphabet Inc | 5/10 | 9.2% | $32.26B |
| BAC | Bank Of Amer Corp | 1/10 | 7.8% | $27.54B |
| CVX | Chevron Corporation | 1/10 | 4.0% | $13.99B |
| OXY | Occidental Pete Corp | 1/10 | 3.7% | $12.87B |
| n/a | Chubb Limited | 3/10 | 3.4% | $11.81B |
| MCO | Moodys Corp | 1/10 | 3.2% | $11.17B |
| GOOG | Alphabet Inc | 2/10 | 2.8% | $9.93B |
| AMZN | Amazon Com Inc | 6/10 | 2.5% | $8.96B |
| TSM | Taiwan Semiconductor Manufac | 5/10 | 2.4% | $8.40B |
| KHC | Kraft Heinz Co | 1/10 | 2.2% | $7.69B |
| DVA | Davita Inc | 1/10 | 1.8% | $6.43B |
| META | Meta Platforms Inc | 5/10 | 1.7% | $5.88B |
| LRCX | Lam Research Corp | 2/10 | 1.6% | $5.46B |
| DAL | Delta Air Lines Inc | 1/10 | 1.5% | $5.37B |
| MSFT | Microsoft Corp | 6/10 | 1.5% | $5.29B |
| AMAT | Applied Matls Inc | 3/10 | 1.5% | $5.10B |
| IEP | Icahn Enterprises Lp | 1/10 | 1.3% | $4.46B |
| TFPM | Triple Flag Precious Metal | 1/10 | 1.1% | $3.99B |
| GEV | Ge Vernova Inc | 2/10 | 1.1% | $3.94B |
| SIRI | Siriusxm Holdings Inc | 1/10 | 1.0% | $3.69B |
| NVDA | Nvidia Corporation | 3/10 | 1.0% | $3.67B |
| MU | Micron Technology Inc | 2/10 | 1.0% | $3.65B |

## Biggest quarter-over-quarter net buying (across the panel)

| Company | Net fund flow (buys minus sells) | Events |
|---|---|---|
| Quantinuum Inc | +3 | 3 |
| Doordash Inc | +3 | 3 |
| Cerebras Systems Inc | +3 | 3 |
| Space Exploration Techn Corp | +3 | 3 |
| Applied Matls Inc | +3 | 3 |
| Advanced Micro Devices Inc | +3 | 3 |
| Intel Corp | +3 | 3 |
| Fervo Energy Co | +2 | 2 |
| Morgan Stanley | +2 | 2 |
| Brightspring Health Svcs Inc | +2 | 2 |

## Biggest quarter-over-quarter net selling (across the panel)

| Company | Net fund flow (buys minus sells) | Events |
|---|---|---|
| Zillow Group Inc | -4 | 4 |
| Capital One Finl Corp | -4 | 4 |
| Kkr & Co Inc | -3 | 3 |
| Servicenow Inc | -3 | 3 |
| Disney Walt Co | -2 | 3 |
| Jd.Com Inc | -2 | 2 |
| Apellis Pharmaceuticals Inc | -2 | 2 |
| Lucid Group Inc | -2 | 2 |
| Apollo Global Mgmt Inc | -2 | 2 |
| Mckesson Corp | -2 | 2 |

## All position changes: {'CLOSED': 267, 'NEW': 196, 'INCREASED': 185, 'DECREASED': 150, 'UNCHANGED': 117}

## Caveats

- 13F covers long US-listed equity + listed options only -- no shorts, no international holdings, no cash/bonds/private investments.
- Up to 45-day filing lag -- this is a snapshot of what was true as of the filing deadline, not real-time.
- 'Net buying/selling' counts fund-level position changes >10% as increased/decreased; it does not weight by dollar size of the change.
