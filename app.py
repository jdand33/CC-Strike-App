import os
import math
from datetime import datetime
from flask import Flask, render_template, request, jsonify
import yfinance as yf

app = Flask(__name__)

# ---------- Black-Scholes helpers ----------

def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def call_delta(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1)

def find_strike(S, T, r, sigma, target_delta):
    best_K = S
    best_diff = 1
    K = S
    while K <= S * 1.2:
        d = call_delta(S, K, T, r, sigma)
        diff = abs(d - target_delta)
        if diff < best_diff:
            best_diff = diff
            best_K = K
        K += 0.5
    return round(best_K, 2)

def round_to_real_strike(theoretical, strike_list, direction="up"):
    if not strike_list:
        return round(theoretical, 2)

    if direction == "up":
        for s in strike_list:
            if s >= theoretical:
                return float(s)
        return float(strike_list[-1])

    if direction == "down":
        for s in reversed(strike_list):
            if s <= theoretical:
                return float(s)
        return float(strike_list[0])

    return float(min(strike_list, key=lambda x: abs(x - theoretical)))

# ---------- Live data helpers ----------

def get_live_price(symbol):
    t = yf.Ticker(symbol)
    data = t.history(period="1d")
    return float(data["Close"].iloc[-1])

# ---------- Routes ----------

@app.route("/", methods=["GET", "POST"])
def index():
    ticker = "MCD"
    t = yf.Ticker(ticker)
    expirations = t.options  # list of available expiration dates

    result = None

    if request.method == "POST":
        ticker = request.form["ticker"].upper()
        expiration = request.form["expiration"]
        risk = request.form["risk"]

        t = yf.Ticker(ticker)
        price = get_live_price(ticker)

        # Compute days to expiration
        exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
        today = datetime.utcnow().date()
        days = (exp_date - today).days
        T = days / 365
        r = 0.02

        # Pull option chain for selected expiration
        chain = t.option_chain(expiration)
        calls = chain.calls

        # ATM IV for selected expiration
        calls["diff"] = (calls["strike"] - price).abs()
        atm = calls.sort_values("diff").iloc[0]
        iv = float(atm["impliedVolatility"])

        # Risk → target delta
        target = {"low": 0.10, "moderate": 0.20, "high": 0.30}[risk]

        # Theoretical strike
        theoretical_strike = find_strike(price, T, r, iv, target)

        # Real strike from chain
        strike_list = sorted(list(calls["strike"]))
        real_strike = round_to_real_strike(theoretical_strike, strike_list, direction="up")

        # Get bid/ask for chosen strike
        row = calls[calls["strike"] == real_strike]
        if row.empty:
            idx = (calls["strike"] - real_strike).abs().idxmin()
            row = calls.loc[[idx]]
        row = row.iloc[0]

        bid = float(row["bid"])
        ask = float(row["ask"])
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else max(bid, ask)

        # Premium (1 contract = 100 shares)
        premium = mid * 100

        # Covered call metrics
        breakeven = price - mid
        yield_pct = premium / (price * 100) if price > 0 else 0
        annualized = yield_pct * (365 / days) if days > 0 else 0

        # Assignment probability
        delta = call_delta(price, real_strike, T, r, iv)

        result = {
            "ticker": ticker,
            "strike": round(real_strike, 2),
            "premium": round(premium, 2),
            "mid_price": round(mid, 2),
            "yield": round(yield_pct * 100, 2),
            "annualized": round(annualized * 100, 2),
            "breakeven": round(breakeven, 2),
            "assignment_prob": round(delta, 3),
            "iv": round(iv, 3),
            "risk": risk.capitalize(),
            "days": days,
            "price": round(price, 2),
            "expiration": expiration
        }

    return render_template("index.html", expirations=expirations, result=result)

# ---------- Render entrypoint ----------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
