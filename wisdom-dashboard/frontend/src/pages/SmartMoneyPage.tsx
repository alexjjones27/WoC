import { useEffect, useState } from "react";
import type { SmartMoneyBacktestPayload, SmartMoneyPortfolioPayload } from "../types";
import { getSmartMoneyBacktest, getSmartMoneyPortfolio } from "../api";
import { formatPct } from "../format";

function pctClass(v: number | null | undefined): string {
  if (v === null || v === undefined) return "";
  return v >= 0 ? "value-gain" : "value-loss";
}

const BACKTEST_DISPLAY_NAMES: Record<string, string> = {
  consensus: "Consensus (all 10)", SPY: "SPY", BERKSHIRE: "Berkshire Hathaway", COATUE: "Coatue",
  VIKING: "Viking Global", TIGER_GLOBAL: "Tiger Global", ELLIOTT: "Elliott", FARALLON: "Farallon",
  PERSHING_SQUARE: "Pershing Square", LONE_PINE: "Lone Pine", ICAHN: "Icahn", SOROS: "Soros",
  N10: "Top 10 (objective)", N20: "Top 20 (objective)", N50: "Top 50 (objective)",
};

export default function SmartMoneyPage() {
  const [portfolio, setPortfolio] = useState<SmartMoneyPortfolioPayload | null>(null);
  const [backtest, setBacktest] = useState<SmartMoneyBacktestPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getSmartMoneyPortfolio(), getSmartMoneyBacktest()])
      .then(([p, b]) => {
        setPortfolio(p);
        setBacktest(b);
      })
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <div className="banner banner-error">{error}</div>;
  if (!portfolio || !backtest) return <div className="empty-state">Loading smart-money data…</div>;

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2 className="page-title">Smart money</h2>
          <span className="subtitle">SEC 13F consensus from active hedge funds, backtested 2013–2026, expanded to a 25-stock S&amp;P 500 portfolio</span>
        </div>
      </div>

      <section className="sm-section">
        <h3>The portfolio</h3>
        <p className="sm-note">
          Top {portfolio.portfolio.length} of {portfolio.coverage.universe_size} S&amp;P 500 stocks by combined score across four signals
          (smart money weight, analyst consensus, options-market confirmation, retail attention), sector-capped, weighted by score.
          Snapshot, not a backtest — no free source of historical options chains.
        </p>
        <div className="stat-strip">
          <div className="stat-tile"><div className="stat-label">Smart money coverage</div><div className="stat-value">{portfolio.coverage.smart_money}/{portfolio.coverage.universe_size}</div></div>
          <div className="stat-tile"><div className="stat-label">Analyst coverage</div><div className="stat-value">{portfolio.coverage.analyst}/{portfolio.coverage.universe_size}</div></div>
          <div className="stat-tile"><div className="stat-label">Options coverage</div><div className="stat-value">{portfolio.coverage.options}/{portfolio.coverage.universe_size}</div></div>
          <div className="stat-tile"><div className="stat-label">Retail attention coverage</div><div className="stat-value">{portfolio.coverage.retail}/{portfolio.coverage.universe_size}</div></div>
        </div>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Ticker</th><th>Sector</th><th className="num">Weight</th><th className="num">Smart money</th>
                <th className="num">Analyst impl. ret</th><th className="num">Options P(hit)</th><th className="num">Retail attn</th><th className="num">Combined</th>
              </tr>
            </thead>
            <tbody>
              {portfolio.portfolio.map((h) => (
                <tr key={h.ticker}>
                  <td><span className="ticker-pill">{h.ticker}</span></td>
                  <td>{h.sector}</td>
                  <td className="num">{formatPct(h.portfolio_weight, 1)}</td>
                  <td className="num">{h.smart_money_weight ? formatPct(h.smart_money_weight, 1) : "0%"}</td>
                  <td className={`num ${pctClass(h.analyst_implied_return)}`}>{h.analyst_implied_return !== null ? formatPct(h.analyst_implied_return, 1) : "n/a"}</td>
                  <td className="num">{h.options_p_exceed_target !== null ? `${Math.round(h.options_p_exceed_target * 100)}%` : "n/a"}</td>
                  <td className="num">{h.retail_attention_ratio !== null ? `${h.retail_attention_ratio.toFixed(2)}×` : "n/a"}</td>
                  <td className={`num ${pctClass(h.combined_score)}`} style={{ fontWeight: 600 }}>{h.combined_score >= 0 ? "+" : ""}{h.combined_score.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {backtest.fixed_panel && (
        <section className="sm-section">
          <h3>Fixed 10-fund panel backtest (2021–2026)</h3>
          <p className="sm-note">Berkshire Hathaway, Coatue, Viking Global, Tiger Global, Elliott, Farallon, Pershing Square, Lone Pine, Icahn, Soros — tracked by CIK across time, aggregated by dollar value each quarter.</p>
          <BacktestTable stats={backtest.fixed_panel.stats} order={["consensus", "SPY", "BERKSHIRE", "COATUE", "VIKING", "TIGER_GLOBAL", "ELLIOTT", "FARALLON", "PERSHING_SQUARE", "LONE_PINE", "ICAHN", "SOROS"]} />
        </section>
      )}

      {backtest.scaling_panels && (
        <section className="sm-section">
          <h3>Objective rolling panels, N=10/20/50 (2013–2026)</h3>
          <p className="sm-note">No hand-picked names — top-N by 13F value among concentrated, non-institutional-bank filers, re-selected fresh every quarter. 53 quarterly rebalances.</p>
          <BacktestTable stats={backtest.scaling_panels.stats} order={["N10", "N20", "N50", "SPY"]} significance={backtest.significance} />
          {backtest.significance && (
            <p className="sm-note" style={{ marginTop: 12 }}>
              None of the N10/20/50-vs-SPY differences are statistically significant (p ≈ 0.27–0.51) — the panels behave like SPY with extra
              leverage (beta 1.11–1.16), not like genuine stock-picking skill (alpha is statistically zero, p ≈ 0.81–0.99).
            </p>
          )}
        </section>
      )}
    </div>
  );
}

function BacktestTable({
  stats,
  order,
  significance,
}: {
  stats: Record<string, { total_return: number; cagr: number | null; annualized_vol: number | null; max_drawdown: number }>;
  order: string[];
  significance?: SmartMoneyBacktestPayload["significance"];
}) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Panel</th><th className="num">Total return</th><th className="num">CAGR</th><th className="num">Ann. vol</th><th className="num">Max drawdown</th>
            {significance && <th className="num">vs SPY p-value</th>}
          </tr>
        </thead>
        <tbody>
          {order.filter((k) => stats[k]).map((k) => {
            const s = stats[k];
            const sig = significance?.[k];
            return (
              <tr key={k} className={k === "SPY" ? "row-highlight" : ""}>
                <td>{BACKTEST_DISPLAY_NAMES[k] || k}</td>
                <td className={`num ${pctClass(s.total_return)}`}>{formatPct(s.total_return, 1)}</td>
                <td className={`num ${pctClass(s.cagr)}`}>{s.cagr !== null ? formatPct(s.cagr, 1) : "n/a"}</td>
                <td className="num">{s.annualized_vol !== null ? formatPct(s.annualized_vol, 1) : "n/a"}</td>
                <td className="num value-loss">{formatPct(s.max_drawdown, 1)}</td>
                {significance && <td className="num">{sig ? sig.significance_vs_spy.p_value.toFixed(2) : "—"}</td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
