import math
import numpy as np
import yfinance as yf
from flask import Flask, render_template, request

app = Flask(__name__)

# Your 5 risk tiers
RISK_TIERS = {
    "low": 0.10,
    "low_moderate": 0.15,
    "moderate": 0.20,
    "moderate_high": 0.25,
    "high": 0.30,
}

RISK_LABELS = {
    "low": "Low",
    "low_moderate": "Low-Moderate",
    "moderate": "Moderate",
    "moderate_high": "Moderate-High",
    "high": "High",
}

# -----------------------------
#   MARKET DATA FUNCTIONS
# -----------------------------

def get_current_price(ticker: str) -> float:
    t = yf.Ticker(ticker)
    data = t.history(period="1d")
    if data.empty:
        raise ValueError("No price data found for ticker.")
    return float(data["Close"].iloc[-1])


def get_market_aware_call(ticker: str, expiration: str, target_delta: float):
    t = yf.Ticker(ticker)
    chain = t.option_chain(expiration)
    calls = chain.calls.copy()

    if calls.empty or "delta" not in calls.columns:
        raise ValueError("No delta data available for this expiration.")

    # Find the strike whose delta is closest to the target
    calls["delta_diff"] = np.abs(calls["delta"] - target_delta)
    best_row = calls.loc[calls["delta_diff"].idxmin()]

    return {
        "strike": float(best_row["strike"]),
        "bid": float(best_row["bid"]),
        "ask": float(best_row["ask"]),
        "mid": float((best_row["bid"] + best_row["ask"]) / 2),
        "delta": float(best_row["delta"]),
    }


# -----------------------------
#   COVERED CALL MATH
# -----------------------------

def compute_covered_call_metrics(shares, cost_basis, strike, premium):
    total_cost_basis = shares * cost_basis
    total_premium = premium * (shares / 100)  # 1 contract per 100 shares

    breakeven = cost_basis - (premium / 100 * 100)
    max_profit_per_share = (strike - cost_basis) + (premium / 100 * 100)
    max_profit = max_profit_per_share * shares

    max_profit_pct = (max_profit / total_cost_basis) * 100 if total_cost_basis > 0 else 0

    return {
        "total_cost_basis": total_cost_basis,
        "total_premium": total_premium,
        "breakeven": breakeven,
        "max_profit": max_profit,
        "max_profit_pct": max_profit_pct,
    }


# -----------------------------
#   FLASK ROUTE
# -----------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    # Default values
    ticker = "MCD"
    shares = 100
    cost_basis = 280.00
    expiration = "2025-01-17"

    result = None
    error = None

    if request.method == "POST":
        try:
            ticker = request.form.get("ticker", ticker).upper().strip()
            shares = int(request.form.get("shares", shares))
            cost_basis = float(request.form.get("cost_basis", cost_basis))
            expiration = request.form.get("expiration", expiration).strip()
            risk_key = request.form.get("risk", "moderate")

            if risk_key not in RISK_TIERS:
                raise ValueError("Invalid risk tier selected.")

            target_delta = RISK_TIERS[risk_key]
            risk_label = RISK_LABELS[risk_key]

            # Pull live price
            current_price = get_current_price(ticker)

            # Pull option chain + find closest delta strike
            call_data = get_market_aware_call(ticker, expiration, target_delta)

            strike = call_data["strike"]
            premium = call_data["mid"]
            actual_delta = call_data["delta"]

            # Compute covered call metrics
            metrics = compute_covered_call_metrics(shares, cost_basis, strike, premium)

            # Bundle result for template
            result = {
                "ticker": ticker,
                "current_price": current_price,
                "risk_label": risk_label,
                "target_delta": target_delta,
                "expiration": expiration,
                "strike": strike,
                "premium": premium,
                "actual_delta": actual_delta,
                "shares": shares,
                "cost_basis": cost_basis,
                "total_cost_basis": metrics["total_cost_basis"],
                "total_premium": metrics["total_premium"],
                "breakeven": metrics["breakeven"],
                "max_profit": metrics["max_profit"],
                "max_profit_pct": metrics["max_profit_pct"],
            }

        except Exception as e:
            error = str(e)

    return render_template(
        "index.html",
        ticker=ticker,
        shares=shares,
        cost_basis=cost_basis,
        expiration=expiration,
        result=result,
        error=error,
    )


if __name__ == "__main__":
    app.run(debug=True)
