"""End-to-end checks against a running stack (deploy/e2e_stack.sh or docker compose).

    python deploy/e2e_check.py http://127.0.0.1:8100 [http://127.0.0.1:8101]
Checks: build + progress, result served from a second replica via the bucket, immutable headers + 304,
slices/lite/columns, dedupe of two clients, cancellation of an abandoned build, per-IP 429.
"""
import json
import sys
import time
import urllib.error
import urllib.request

A = sys.argv[1]
B = sys.argv[2] if len(sys.argv) > 2 else A
BARNARD = {"ra": 269.4520769586, "dec": 4.6933649666, "pmra": -801.551, "pmdec": 10362.394, "ref_epoch": 2000.0}


def req(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def post(base, params, ip, client):
    return req("POST", base + "/api/sequences", params, {"X-Forwarded-For": ip, "X-Client-Id": client})


def poll(base, sid, client, until=("done",), timeout=300):
    t0, last = time.time(), None
    while time.time() - t0 < timeout:
        code, _, body = req("GET", f"{base}/api/sequences/{sid}", headers={"X-Client-Id": client})
        if code == 200:
            return "done", time.time() - t0, last
        job = json.loads(body)
        last = job
        if job["status"] in until or code in (410, 500):
            return job["status"], time.time() - t0, job
        time.sleep(1)
    return "timeout", time.time() - t0, last


ok = True


def check(name, cond, info=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info else ""))


# 1. build a fresh sequence (unique FOV so it is not cached), with progress
p1 = {**BARNARD, "fov_arcmin": 5.1}
code, _, body = post(A, p1, "10.0.0.1", "tabA")
sid = json.loads(body)["id"]
seen_progress = []
t0 = time.time()
while True:
    c, _, b = req("GET", f"{A}/api/sequences/{sid}", headers={"X-Client-Id": "tabA"})
    if c == 200:
        break
    j = json.loads(b)
    seen_progress.append((j["stage"], j["done"], j["total"]))
    if j["status"] in ("error", "cancelled") or time.time() - t0 > 300:
        break
    time.sleep(1)
check("fresh build finishes", c == 200, f"{time.time() - t0:.1f}s, progress samples {seen_progress[-3:]}")

# 2. second replica serves it from the bucket, immutable + 304
c, h, b = req("GET", f"{B}/api/sequences/{sid}", headers={"Accept-Encoding": "br"})
check("replica B serves meta from bucket", c == 200 and h.get("content-encoding") == "br", h.get("cache-control", ""))
check("immutable cache header", h.get("cache-control") == "public, max-age=31536000, immutable")
c, _, _ = req("GET", f"{B}/api/sequences/{sid}", headers={"If-None-Match": f'"{sid}"'})
check("conditional GET -> 304", c == 304)
c, h, b = req("GET", f"{B}/api/sequences/{sid}/coadds.bin?epoch=0&band=D1")
check("per-epoch/band slice", c == 200 and len(b) == 4 * int(h["x-array-shape"].split(",")[0]) ** 2, h.get("x-array-shape"))
c, _, b = req("GET", f"{B}/api/sequences/{sid}/lite")
lite = json.loads(b)
check("lite meta", c == 200 and "photometry" not in lite and lite["tables"]["photometry"]["rows"] > 0,
      f"{len(b)} B lite vs columns url {lite['tables']['photometry']['url']}")

# 3. dedupe: two clients, one job
p2 = {**BARNARD, "fov_arcmin": 4.3}
_, _, b1 = post(A, p2, "10.0.0.2", "tab1")
_, _, b2 = post(B, p2, "10.0.0.3", "tab2")
j1, j2 = json.loads(b1), json.loads(b2)
check("dedupe across replicas", j1["id"] == j2["id"] and j2["status"] in ("queued", "running"), j2["status"])
st, dt, _ = poll(A, j1["id"], "tab1")
check("deduped job completes", st == "done", f"{dt:.1f}s")

# 4. abandoned build is cancelled (submit, never poll)
p3 = {**BARNARD, "fov_arcmin": 7.7}
_, _, b = post(A, p3, "10.0.0.4", "tabX")
aid = json.loads(b)["id"]
time.sleep(int(sys.argv[3]) if len(sys.argv) > 3 else 45)
c, _, b = req("GET", f"{B}/api/sequences/{aid}", headers={"X-Client-Id": "observer"})
j = json.loads(b) if b else {}
check("abandoned build cancelled", c == 410 and j.get("status") == "cancelled", f"{c} {j.get('error')}")

# 5. explicit withdraw
p4 = {**BARNARD, "fov_arcmin": 8.3}
_, _, b = post(A, p4, "10.0.0.5", "tabW")
wid = json.loads(b)["id"]
time.sleep(2)
req("DELETE", f"{A}/api/sequences/{wid}", headers={"X-Client-Id": "tabW"})
st, dt, j = poll(B, wid, "observer2", until=("cancelled", "done"), timeout=20)
check("withdrawn build cancelled", st == "cancelled", f"{dt:.1f}s {j and j.get('error')}")

# 6. per-IP limit -> 429 with Retry-After
codes = []
for i in range(5):
    c, h, _ = post(A, {**BARNARD, "fov_arcmin": 9.0 + i * 0.1}, "10.0.0.9", f"tabL{i}")
    codes.append((c, h.get("retry-after")))
check("per-IP limit 429", any(c == 429 and ra for c, ra in codes), str(codes))
for i in range(5):
    req("DELETE", f"{A}/api/sequences/x", headers={"X-Client-Id": f"tabL{i}"})
print("ALL PASS" if ok else "SOME FAILED")
