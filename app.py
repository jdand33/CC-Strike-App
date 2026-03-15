import math
import numpy as np
import yfinance as yf
from flask import Flask, render_template_string, request

app = Flask(__name__)

RISK_TIERS = {
    "low": 0.10,
    "low_moderate": 0.15,
    "moderate": 0.20,
    "moderate_high": 0.25,
    "high": 0.30,
}

HTML_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Covered Call Calculator</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .container { max-width: 800px; margin: auto; }
        .risk-buttons button { margin: 4px; padding: 8px 12px; }
        .result { margin-top: 20px; padding: 15px; border: 1px solid #ccc; border-radius: 6px; }
        .label { font-weight: bold; }
    </style>
</head>
<body>
<div class="container">
    <h1>Covered Call Calculator (Market-Aware)</h1>
    <form method="post">
        <label>Ticker:</label>
        <input type="text" name="ticker" value="{{ ticker }}" required>
        <br><br>

        <label>Shares Owned:</label>
        <input type="number" name="shares" value="{{ shares }}" required>
        <br><br>

        <label>Cost Basis per Share:</label>
        <input type="number" step="0.01" name="cost_basis" value="{{ cost_basis }}" required>
        <br><br>

        <label>Expiration Date (YYYY-MM-DD):</label>
        <input type="text" name="expiration" value="{{ expiration }}" required>
        <br><br>

        <label>Risk Tier (Target Delta):</label>
        <div class="risk-buttons">
            <button name="risk" value="low" type="submit">Low (0.10)</button>
            <button name="risk" value="low_moderate" type="submit">Low-Moderate (0.15)</button>
            <button name="risk" value="moderate" type="submit">Moderate (0.20)</button>
            <button name="risk" value="moderate_high" type="submit">Moderate-High (0.25)</button>
            <button name="risk" value="high" type="submit">High (0.30)</button>
        </div>
    </form>

    {% if result %}
    <div class="result">
        <div><span class="label">Ticker:</span> {{ result.ticker }}</div>
        <div><span class="label">Current Price:</span> ${{ "%.2f"|format(result.current_price) }}</div>
        <div><span class="label">Risk Tier:</span> {{ result.risk_label }} (Target Δ {{ "%.2f"|format(result.target_delta) }})</div>
        <hr>
        <div><span class="label">Selected Expiration:</span> {{ result.expiration }}</div>
        <div><span class="label">Market-Aware Strike:</span> {{ "%.2f"|format(result.strike) }}</div>
        <div><span class="label">Option Premium (mid):</span> ${{ "%.2f"|format(result.premium) }}</div>
        <div><span class="label">Actual Delta:</span> {{ "%.2f"|format(result.actual_delta) }}</div>
        <hr>
        <div><span class="label">Shares:</span> {{ result.shares }}</div>
        <div><span class="label">Cost Basis per Share:</span> ${{ "%.2f"|format(result.cost_basis) }}</div>
        <div><span class="label">Total Cost Basis:</span> ${{ "%.2f"|format(result.total_cost_basis) }}</div>
        <div><span class="label">Total Premium Collected:</span> ${{ "%.2f"|format(result.total_premium) }}</div>
        <div><span class="label">Breakeven Price:</span> ${{ "%.2f"|format(result.breakeven) }}</div>
        <div><span class="label">Max Profit if Called Away:</span> ${{ "%.2f"|format(result.max_profit) }}</div>
        <div><span class="label">Max Profit % on Cost Basis:</span> {{ "%.2f"|format(result.max_profit_pct) }}%</div>
    </div>
    {% endif %}

    {% if error %}
    <div class="result" style="border-color: #c00; color: #c00;">
        <span class="label">Error:</span> {{ error }}
    </div>
    {% endif %}
</div>
</body>
</html>
"""

def get_current_price(ticker: str) -> float:
    t = yf.Ticker(ticker)
    data = t.history(period="1d")
    if data.empty:
        raise ValueError("No price data for ticker.")
    return float(data["Close"].iloc[-1])

def get_market_aware_call(ticker: str, expiration: str, target_delta: float):
    t = yf.Ticker(ticker)
    chain = t.option_chain(expiration)
    calls = chain.calls.copy()

    if "delta" not in calls.columns or calls.empty:
        raise ValueError("No delta data available for calls on this expiration.")

    calls["delta_diff"] = np.abs(calls["delta"] - target_delta)
    best_row = calls.loc[calls["delta_diff"].idxmin()]

    return {
        "strike": float(best_row["strike"]),
        "bid": float(best_row["bid"]),
        "ask": float(best_row["ask"]),
        "mid": float((best_row["bid"] + best_row["ask"]) / 2),
        "delta": float(best_row["delta"]),
    }

def compute_covered_call_metrics(shares, cost_basis, strike, premium):
    total_cost_basis = shares * cost_basis
    total_premium = premium * (shares / 100)  # 1 contract per 100 shares
    breakeven = cost_basis - (premium / 100 * 100)  # effectively cost_basis - premium_per_share
    max_profit_per_share = (strike - cost_basis) + (premium / 100 * 100)
    max_profit = max_profit_per_share * shares
    max_profit_pct = (max_profit / total_cost_basis) * 100 if total_cost_basis > 0 else 0.0

    return {
        "total_cost_basis": total_cost_basis,
        "total_premium": total_premium,
        "breakeven": breakeven,
        "max_profit": max_profit,
        "max_profit_pct": max_profit_pct,
    }

@app.route("/", methods=["GET", "POST"])
def index():
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
            risk_label_map = {
                "low": "Low",
                "low_moderate": "Low-Moderate",
                "moderate": "Moderate",
                "moderate_high": "Moderate-High",
                "high": "High",
            }
            risk_label = risk_label_map[risk_key]

            current_price = get_current_price(ticker)
            call_data = get_market_aware_call(ticker, expiration, target_delta)

            strike = call_data["strike"]
            premium = call_data["mid"]
            actual_delta = call_data["delta"]

            metrics = compute_covered_call_metrics(shares, cost_basis, strike, premium)

            class R:  # simple object-like container for template
                pass

            r = R()
            r.ticker = ticker
            r.current_price = current_price
            r.risk_label = risk_label
            r.target_delta = target_delta
            r.expiration = expiration
            r.strike = strike
            r.premium = premium
            r.actual_delta = actual_delta
            r.shares = shares
            r.cost_basis = cost_basis
            r.total_cost_basis = metrics["total_cost_basis"]
            r.total_premium = metrics["total_premium"]
            r.breakeven = metrics["breakeven"]
            r.max_profit = metrics["max_profit"]
            r.max_profit_pct = metrics["max_profit_pct"]

            result = r

        except Exception as e:
            error = str(e)

    return render_template_string(
        HTML_TEMPLATE,
        ticker=ticker,
        shares=shares,
        cost_basis=cost_basis,
        expiration=expiration,
        result=result,
        error=error,
    )

if __name__ == "__main__":
    app.run(debug=True)
