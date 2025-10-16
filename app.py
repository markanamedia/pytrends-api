import os
import time
from flask import Flask, request, jsonify
from pytrends.request import TrendReq
from collections import OrderedDict

app = Flask(__name__)

# ---- Simple in-memory TTL cache (per (q, geo)) ----
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", 60 * 60))   # 1 hour default
CACHE_MAX = int(os.getenv("CACHE_MAX_ITEMS", 300))

class TTLCache(OrderedDict):
    def __init__(self, *args, **kwargs):
        self.ttl = kwargs.pop('ttl', CACHE_TTL)
        self.max_items = kwargs.pop('max_items', CACHE_MAX)
        super().__init__(*args, **kwargs)

    def _evict(self):
        while len(self) > self.max_items:
            self.popitem(last=False)

    def get(self, key):
        item = super().get(key)
        if not item:
            return None
        value, ts = item
        if time.time() - ts > self.ttl:
            try:
                super().pop(key)
            except KeyError:
                pass
            return None
        return value

    def set(self, key, value):
        super().__setitem__(key, (value, time.time()))
        self._evict()

cache = TTLCache(ttl=CACHE_TTL, max_items=CACHE_MAX)

# Keep a short "cooldown" per keyword to avoid hammering
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", 5))
last_hit = {}

def get_pytrends():
    # Good UA helps reduce blocks
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/127.0.0.0 Safari/537.36")
    return TrendReq(
        hl="en-US",
        tz=360,
        retries=3,            # try a few times
        backoff_factor=2,     # 1s, 2s, 4s
        requests_args={"headers": {"User-Agent": ua}, "timeout": (5, 30)}
    )

@app.route("/trends/related")
def related():
    q = (request.args.get("q") or "").strip()
    geo = (request.args.get("geo") or "US").strip().upper()

    if not q:
        return jsonify({"error": "Missing q param"}), 400

    cache_key = (q.lower(), geo)
    cached = cache.get(cache_key)
    if cached is not None:
        return jsonify(cached)

    now = time.time()
    if cache_key in last_hit and (now - last_hit[cache_key]) < COOLDOWN_SECONDS:
        # Gentle local throttle to avoid 429s
        time.sleep(max(0, COOLDOWN_SECONDS - (now - last_hit[cache_key])))

    pytrends = get_pytrends()
    try:
        pytrends.build_payload([q], timeframe="today 12-m", geo=geo)
        related_queries = pytrends.related_queries()
        # Structure varies (keyed by the keyword)
        data = related_queries.get(q, {}) if related_queries else {}
        result = {
            "query": q,
            "geo": geo,
            "related_queries": {
                "top": data.get("top").to_dict("records") if data.get("top") is not None else [],
                "rising": data.get("rising").to_dict("records") if data.get("rising") is not None else [],
            },
        }
        cache.set(cache_key, result)
        last_hit[cache_key] = time.time()
        return jsonify(result)

    except Exception as e:
        # pytrends raises exceptions; if it's a 429, advertise retry
        msg = str(e)
        if "429" in msg or "Too Many Requests" in msg:
            return jsonify({"error": "Rate limited by Google (429). Please retry later."}), 429
        return jsonify({"error": msg}), 500

@app.route("/")
def health():
    return jsonify({"ok": True}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

