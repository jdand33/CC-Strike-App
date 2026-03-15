import os
import math
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

def get_atm_iv(ticker_obj, spot):
    expirations = ticker_obj.options
    if not expirations:
        return None, None

    nearest_exp = expirations[0]
    chain = ticker_obj.option_chain(nearest_exp)
    calls = chain.calls

    calls["diff"] = (calls["strike"] - spot).abs()
    atm = calls.sort_values("diff").iloc[0]
    iv = float(atm["impliedVolatility"])
    return iv, nearest_exp

# ---------- API endpoints for frontend autofill ----------

@app.route("/price")
def price():
    symbol = request.args.get("ticker", "MCD").upper()
    p = get_live_price(symbol)
    return jsonify({"price": round(p, 2)})

@app.route("/iv")
def iv():
    symbol = request.args.get("ticker", "MCD").upper()
    spot = get_live_price(symbol)
    t = yf.Ticker(symbol)
    iv_val, _ = get_atm_iv(t, spot)
    if iv_val is None:
        return jsonify({"iv": None})
    return jsonify({"iv": round(iv_val, 4)})

# ---------- Main covered call calculator ----------

@app.route("/", methods=["GET", "POST"])
def index():
    result = None

    if request.method == "POST":
        ticker = request.form["ticker"].upper()
        days = int(request.form["days"])
        risk = request.form["risk"]

        # Live price
        price = get_live_price(ticker)

        # Live IV + nearest expiration
        t = yf.Ticker(ticker)
        iv, nearest_exp = get_atm_iv(t, price)
        if iv is None or nearest_exp is None:
            return render_template("index.html", result=None, error="No options data available for this ticker.")

        T = days / 365
        r = 0.02

        # Target delta by risk
        target = {"low": 0.10, "moderate": 0.20, "high": 0.30}[risk]

        # Theoretical strike from delta
        theoretical_strike = find_strike(price, T, r, iv, target)

        # Real strikes from chain
        chain = t.option_chain(nearest_exp)
        calls = chain.calls
        strike_list = sorted(list(calls["strike"]))
        real_strike = round_to_real_strike(theoretical_strike, strike_list, direction="up")

        # Find row for chosen strike
        row = calls[calls["strike"] == real_strike]
        if row.empty:
            # fallback: nearest by absolute difference
            idx = (calls["strike"] - real_strike).abs().idxmin()
            row = calls.loc[[idx]]

        row = row.iloc[0]

        bid = float(row["bid"])
        ask = float(row["ask"])
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else max(bid, ask)

        # 1 contract = 100 shares
        premium = mid * 100

        breakeven = price - mid
        yield_pct = premium / (price * 100) if price > 0 else 0
        annualized = yield_pct * (365 / days) if days > 0 else 0

        # Assignment probability via delta of final strike
        delta = call_delta(price, real_strike, T, r, iv)
     
        result = {
            "ticker": ticker,
            "strike": round(real_strike, 2),
            "premium": round(premium, 2),
            "mid_price": round(mid, 2),
            "yield": round(yield_pct * 100, 2),        # %
            "annualized": round(annualized * 100, 2),  # %
            "breakeven": round(breakeven, 2),
            "assignment_prob": round(delta, 3),
            "iv": round(iv, 3),
            "risk": risk.capitalize(),
            "days": days,
            "price": round(price, 2),
            "expiration": nearest_exp
        }

    return render_template("index.html", result=result)

# ---------- Render entrypoint ----------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
