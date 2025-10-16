import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from pytrends.request import TrendReq
import pandas as pd

app = Flask(__name__)
CORS(app)

def new_pytrends():
    # You can tweak defaults via env vars later if you want
    hl = os.getenv("PYTRENDS_HL", "en-US")
    tz = int(os.getenv("PYTRENDS_TZ", "0"))
    return TrendReq(hl=hl, tz=tz, timeout=(10, 25))

@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.get("/trends/related")
def related():
    """
    GET /trends/related?q=hvac,mini split&geo=US&timeframe=today 12-m
    Returns Google Trends related queries (top & rising) for each keyword.
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Missing q param"}), 400

    geo = request.args.get("geo", "US")
    timeframe = request.args.get("timeframe", "today 12-m")
    keywords = [k.strip() for k in q.split(",") if k.strip()]

    py = new_pytrends()
    out = {}

    for kw in keywords:
        try:
            py.build_payload([kw], timeframe=timeframe, geo=geo)
            data = py.related_queries() or {}
            out[kw] = data.get(kw, {})
        except Exception as e:
            out[kw] = {"error": str(e)}

    return jsonify(out)

@app.get("/trends/interest")
def interest():
    """
    GET /trends/interest?q=hvac,mini split&geo=US&timeframe=now 7-d
    Returns interest over time for each keyword (daily/weekly points).
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Missing q param"}), 400

    geo = request.args.get("geo", "US")
    timeframe = request.args.get("timeframe", "today 12-m")
    keywords = [k.strip() for k in q.split(",") if k.strip()]

    py = new_pytrends()
    try:
        py.build_payload(keywords, timeframe=timeframe, geo=geo)
        df = py.interest_over_time()
        if df is None or df.empty:
            return jsonify({"records": []})

        if "isPartial" in df.columns:
            df = df.drop(columns=["isPartial"])

        df = df.reset_index().rename(columns={"date": "date"})
        df["date"] = df["date"].dt.date.astype(str)

        # convert to list-of-dicts rows: [{date, kw1, kw2, ...}]
        records = df.to_dict(orient="records")
        return jsonify({"records": records})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # For local testing only; Render runs via gunicorn (see Procfile)
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
