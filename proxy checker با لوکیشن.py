#!/usr/bin/env python3
import os, re, sys, time, datetime, json
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
except ImportError:
    print("❌ pip install requests")
    sys.exit(1)

INPUT_FILE = "proxies.txt"
OK_FILE = "working.txt"
DEAD_FILE = "dead.txt"

THREADS = 120
TIMEOUT = 12
RETRIES = 1

URL = "https://callino.online/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

# برای گرفتن لوکیشن IP خروجی پروکسی
GEO_URL = "http://ip-api.com/json/?fields=status,country,regionName,city,query,isp,as"

def load_proxies(path=INPUT_FILE):
    if not os.path.exists(path):
        print(f"❌ فایل '{path}' نیست")
        sys.exit(1)
    out = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith(("#",";")):
                continue
            part = re.split(r"[\s,;]+", line)[0]
            if re.search(r":\d+$", part) or re.match(r"^[a-zA-Z0-9]+://", part):
                out.append(part)
    return out

def normalize(p: str) -> str:
    if re.match(r"^[a-zA-Z0-9]+://", p):
        return p
    return "http://" + p

def proxies_dict(proxy_url: str):
    return {"http": proxy_url, "https": proxy_url}

def classify_exception(e: Exception) -> str:
    s = str(e).lower()
    if "timed out" in s or "timeout" in s:
        return "timeout"
    if "name or service not known" in s or "nodename nor servname provided" in s:
        return "dns"
    if "connection refused" in s:
        return "refused"
    if "proxy error" in s:
        return "proxy_error"
    if "ssl" in s or "wrong version number" in s or "certificate" in s:
        return "ssl"
    if "403" in s:
        return "403"
    return "fail"

def fetch_geo(proxy_url: str, headers: dict):
    """
    تلاش می‌کند IP خروجی و لوکیشن را از داخل پروکسی بگیرد.
    خروجی: dict یا None
    """
    try:
        r = requests.get(
            GEO_URL,
            proxies=proxies_dict(proxy_url),
            timeout=min(10, TIMEOUT),
            headers=headers,
            allow_redirects=True
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                return {
                    "ip": data.get("query"),
                    "country": data.get("country"),
                    "region": data.get("regionName"),
                    "city": data.get("city"),
                    "isp": data.get("isp"),
                    "as": data.get("as"),
                }
    except Exception:
        return None
    return None

def test_target(proxy_url: str, headers: dict):
    last_reason = "fail"
    for _ in range(RETRIES + 1):
        start = time.time()
        try:
            r = requests.head(URL, proxies=proxies_dict(proxy_url), timeout=TIMEOUT, headers=headers, allow_redirects=True)
            latency = round(time.time() - start, 2)
            if 200 <= r.status_code < 500:
                return True, latency, f"status={r.status_code}"
            return False, None, f"status={r.status_code}"
        except requests.exceptions.RequestException as e:
            last_reason = classify_exception(e)
            try:
                start = time.time()
                r = requests.get(URL, proxies=proxies_dict(proxy_url), timeout=TIMEOUT, headers=headers, allow_redirects=True)
                latency = round(time.time() - start, 2)
                if 200 <= r.status_code < 500:
                    return True, latency, f"status={r.status_code}"
                return False, None, f"status={r.status_code}"
            except requests.exceptions.RequestException as e2:
                last_reason = classify_exception(e2)
                continue
    return False, None, last_reason

def worker(proxy_raw: str):
    proxy_url = normalize(proxy_raw)
    headers = {"User-Agent": UA, "Accept": "*/*", "Connection": "close"}

    ok, lat, info = test_target(proxy_url, headers)
    if not ok:
        print(f"❌ {proxy_raw} ({info})")
        return ("dead", proxy_raw, None, info, None)

    # فقط اگر سالم بود، لوکیشن بگیر (تا درخواست‌ها زیاد نشه)
    geo = fetch_geo(proxy_url, headers)

    if geo:
        loc = f'{geo["country"]}/{geo["city"]} ({geo["ip"]})'
    else:
        loc = "loc=unknown"

    print(f"✅ {proxy_raw} ({lat}s) {info} | {loc}")
    return ("ok", proxy_raw, lat, info, geo)

def main():
    px = load_proxies()
    if not px:
        print("⚠️ خالیه")
        return

    print(f"🔍 Testing {len(px)} proxies | threads={THREADS} | timeout={TIMEOUT}s | target={URL}")

    oks = []
    deads = []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        futs = [ex.submit(worker, p) for p in px]
        for fut in as_completed(futs):
            kind, p, lat, info, geo = fut.result()
            if kind == "ok":
                oks.append((p, lat, info, geo))
            else:
                deads.append((p, info))

    oks.sort(key=lambda x: x[1] if x[1] is not None else 999999)
    ts = datetime.datetime.utcnow().isoformat() + "Z"
    elapsed = round(time.time() - t0, 2)

    with open(OK_FILE, "w", encoding="utf-8") as f:
        f.write(f"# Generated: {ts}\n# Target: {URL}\n")
        for p, lat, info, geo in oks:
            if geo:
                f.write(
                    f"{p}  # {lat}s {info} | {geo.get('country')}/{geo.get('region')}/{geo.get('city')} "
                    f"| ip={geo.get('ip')} | isp={geo.get('isp')} | as={geo.get('as')}\n"
                )
            else:
                f.write(f"{p}  # {lat}s {info} | loc=unknown\n")

    with open(DEAD_FILE, "w", encoding="utf-8") as f:
        f.write(f"# Generated: {ts}\n# Target: {URL}\n")
        for p, info in deads:
            f.write(f"{p}  # {info}\n")

    print(f"\n✅ working: {len(oks)} -> {OK_FILE}")
    print(f"❌ dead: {len(deads)} -> {DEAD_FILE}")
    print(f"⏱ elapsed: {elapsed}s")

if __name__ == "__main__":
    main()
