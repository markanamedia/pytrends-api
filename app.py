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
    """
    IMPORTANT FIX:
    - Set timeout via the top-level TrendReq(timeout=...) parameter
    - Do NOT pass 'timeout' again inside requests_args
      (pytrends forwards both, which leads to 'multiple values for timeout')
    """
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/127.0.0.0 Safari/537.36")
    return TrendReq(
        hl="en-US",
        tz=360,
        retries=2,
        backoff_factor=1,
        timeout=(5, 15),  # <-- set timeout here
        # Do NOT include 'timeout' in requests_args
        requests_args={"headers": {"User-Agent": ua}}
    )


@app.route("/trends/related")
def related():
    q = (request.args.get("q") or "").strip()
    geo = (request.args.get("geo") or "US").strip().upper()

    if not q:
        return jsonify({"error": "Missing q param"}), 400

    # Optional: quick cooldown to reduce chances of 429 on repeat queries
    key = (q.lower(), geo)
    now = time.time()
    if key in last_hit and (now - last_hit[key]) < COOLDOWN_SECONDS:
        time.sleep(max(0, COOLDOWN_SECONDS - (now - last_hit[key])))
    last_hit[key] = time.time()

    # Optional: small cache to reduce repeated calls
    cached = cache.get(key)
    if cached is not None:
        return jsonify(cached)

    try:
        pytrends = get_pytrends()
        pytrends.build_payload([q], timeframe="today 12-m", geo=geo)
        related_queries = pytrends.related_queries()
        if not related_queries or q not in related_queries:
            return jsonify({"error": f"No related data for '{q}'"}), 404

        data = related_queries[q]
        top = data.get("top")
        rising = data.get("rising")

        result = {
            "query": q,
            "geo": geo,
            "related_queries": {
                "top": top.to_dict("records") if top is not None else [],
                "rising": rising.to_dict("records") if rising is not None else [],
            },
        }

        # save to cache
        cache.set(key, result)
        return jsonify(result)

    except Exception as e:
        msg = str(e)
        if "429" in msg or "Too Many Requests" in msg:
            return jsonify({"error": "Rate limited by Google (429). Please retry later."}), 429
        if "Failed to connect" in msg or "Connection aborted" in msg:
            return jsonify({"error": "Google connection failed. Try again soon."}), 503
        return jsonify({"error": f"Internal error: {msg}"}), 500


@app.route("/")
def health():
    return jsonify({"ok": True}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

