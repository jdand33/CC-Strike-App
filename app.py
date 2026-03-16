from flask import Flask, render_template, request
import yfinance as yf

app = Flask(__name__)

# Risk categories mapped to approximate deltas
RISK_TO_DELTA = {
    "very_safe": 0.10,
    "safe": 0.15,
    "moderate": 0.20,
    "aggressive": 0.25,
    "very_aggressive": 0.30
}

def validate_ticker(ticker: str) -> bool:
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        return bool(info and info.last_price and t.options)
    except:
        return False

def get_expirations(ticker: str):
    try:
        return yf.Ticker(ticker).options
    except:
        return []

def get_closest_delta_strike(ticker: str, expiration: str, target_delta: float):
    try:
        t = yf.Ticker(ticker)
        chain = t.option_chain(expiration)
        calls = chain.calls.dropna(subset=["delta"])
        calls["abs_diff"] = (calls["delta"] - target_delta).abs()
        best = calls.loc[calls["abs_diff"].idxmin()]
        return {
            "symbol": best["contractSymbol"],
            "strike": float(best["strike"]),
            "delta": float(best["delta"]),
            "bid": float(best["bid"]),
            "ask": float(best["ask"]),
            "last": float(best["lastPrice"])
        }
    except:
        return None

@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    error = None
    expirations = []

    if request.method == "POST":
        ticker = request.form.get("ticker", "").upper().strip()
        expiration = request.form.get("expiration", "").strip()
        risk_key = request.form.get("risk", "").strip()

        # Validate ticker first
        if not validate_ticker(ticker):
            return render_template("index.html",
                                   error=f"'{ticker}' is not a valid ticker with options.",
                                   expirations=[])

        # Pull expiration list immediately
        expirations = get_expirations(ticker)

        if not expirations:
            return render_template("index.html",
                                   error="No expirations available.",
                                   expirations=[])

        # If expiration is empty, stop here and show the list
        if expiration == "":
            return render_template("index.html",
                                   expirations=expirations)

        # Validate expiration
        if expiration not in expirations:
            return render_template("index.html",
                                   error=f"{expiration} is not a valid expiration.",
                                   expirations=expirations)

        # Validate risk
        if risk_key not in RISK_TO_DELTA:
            return render_template("index.html",
                                   error="Invalid risk level.",
                                   expirations=expirations)

        target_delta = RISK_TO_DELTA[risk_key]

        # Pull closest delta strike
        result = get_closest_delta_strike(ticker, expiration, target_delta)
        if result is None:
            return render_template("index.html",
                                   error="Unable to pull option data.",
                                   expirations=expirations)

    return render_template("index.html",
                           result=result,
                           error=error,
                           expirations=expirations)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
