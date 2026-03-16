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
    """Return True if ticker exists and has options."""
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        if not info or not getattr(info, "last_price", None):
            return False
        if not t.options:
            return False
        return True
    except Exception:
        return False


def get_expirations(ticker: str):
    """Return list of option expirations with retry for cold starts."""
    try:
        t = yf.Ticker(ticker)

        for _ in range(3):
            expirations = t.options
            if expirations:
                return expirations

        return []
    except Exception:
        return []


def get_closest_delta_strike(ticker: str, expiration: str, target_delta: float):
    """
    Return the call option closest to target_delta for given ticker/expiration.
    Includes retry and safety checks for missing/empty data.
    """
    try:
        t = yf.Ticker(ticker)

        calls = None
        for _ in range(2):
            chain = t.option_chain(expiration)
            calls = chain.calls
            if calls is not None and not calls.empty:
                break

        if calls is None or calls.empty:
            return None

        if "delta" not in calls.columns:
            return None

        calls = calls.dropna(subset=["delta"])
        if calls.empty:
            return None

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

    except Exception:
        return None


@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    error = None
    expirations = []

    if request.method == "POST":
        action = request.form.get("action")
        ticker = request.form.get("ticker", "").upper().strip()
        expiration = request.form.get("expiration", "").strip()
        risk_key = request.form.get("risk", "").strip()

        # Validate ticker first
        if not ticker:
            return render_template("index.html",
                                   error="Please enter a ticker.",
                                   expirations=expirations)

        if not validate_ticker(ticker):
            return render_template("index.html",
                                   error=f"'{ticker}' is not a valid ticker with options.",
                                   expirations=expirations)

        # Load expirations with retry
        expirations = get_expirations(ticker)
        if not expirations:
            return render_template("index.html",
                                   error="No expirations available for this ticker.",
                                   expirations=[])

        # If user clicked "Get Expirations", stop here and just show list
        if action == "load":
            return render_template("index.html",
                                   expirations=expirations)

        # From here on, user clicked "Calculate"
        if not expiration:
            return render_template("index.html",
                                   error="Please select an expiration.",
                                   expirations=expirations)

        if expiration not in expirations:
            return render_template("index.html",
                                   error=f"{expiration} is not a valid expiration.",
                                   expirations=expirations)

        if risk_key not in RISK_TO_DELTA:
            return render_template("index.html",
                                   error="Invalid risk level.",
                                   expirations=expirations)

        target_delta = RISK_TO_DELTA[risk_key]

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
