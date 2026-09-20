import { useState } from "react";
import PredictionMarketsPage from "./pages/PredictionMarketsPage";
import SmartMoneyPage from "./pages/SmartMoneyPage";
import StockLookupPage from "./pages/StockLookupPage";
import ThreeCrowdsPage from "./pages/ThreeCrowdsPage";

type Tab = "markets" | "smart-money" | "lookup" | "three-crowds";

const TABS: { id: Tab; label: string }[] = [
  { id: "markets", label: "Prediction Markets" },
  { id: "smart-money", label: "Smart Money" },
  { id: "lookup", label: "Stock Lookup" },
  { id: "three-crowds", label: "Three Crowds" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("markets");

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-left">
          <h1>Wisdom of the Markets</h1>
          <span className="subtitle">Every crowd this project could get free data for, in one place</span>
        </div>
        <nav className="tab-nav">
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "tab-btn active" : "tab-btn"} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      {tab === "markets" && <PredictionMarketsPage />}
      {tab === "smart-money" && <SmartMoneyPage />}
      {tab === "lookup" && <StockLookupPage />}
      {tab === "three-crowds" && <ThreeCrowdsPage />}
    </div>
  );
}
