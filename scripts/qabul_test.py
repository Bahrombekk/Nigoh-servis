"""Nigoh mikroservisining qabul testi — YANGI (bo'sh) test konteyneriga qarshi.

Ishga tushirish:
    docker run -d --name nigoh-sinov -p 8021:8010 \n      -e ADMIN_PAROL=sinov-admin-987 -e NIGOH_API_KEY=test-kalit-abc123 \n      -e ENABLE_UI=1 nigoh:latest
    python scripts/qabul_test.py

Muhit orqali moslash: NIGOH_BASE, NIGOH_KEY, NIGOH_ADMIN_PAROL.
DIQQAT: faqat sinov konteyneriga qarshi yuriting — test kamera va
foydalanuvchi yaratib o'chiradi, demo bazani (6 kamera) kutadi.
"""
import http.cookiejar
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("NIGOH_BASE", "http://127.0.0.1:8021")
KEY = {"X-API-Key": os.environ.get("NIGOH_KEY", "test-kalit-abc123")}
BAD_KEY = {"X-API-Key": "xato-kalit"}

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  << {str(detail)[:160]}")


def req(path, method="GET", body=None, headers=None, opener=None, timeout=30):
    r = urllib.request.Request(BASE + path,
                               data=json.dumps(body).encode() if body is not None else None,
                               method=method)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    op = opener or urllib.request.build_opener()
    try:
        with op.open(r, timeout=timeout) as res:
            raw = res.read()
            try:
                return res.status, json.loads(raw) if raw else None
            except ValueError:
                return res.status, raw[:200]
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw[:200]


# ---------- 1. Umumiy sog'liq ----------
s, _ = req("/docs")
check("1.1 /docs ochiladi", s == 200, s)
s, d = req("/openapi.json")
check("1.2 OpenAPI faqat v1 yo'llar", s == 200 and all(p.startswith("/api/v1") for p in d["paths"]), s)
s, _ = req("/")
check("1.3 Test UI (/) beriladi", s == 200, s)

# ---------- 2. Anonim cheklovlar (X-API-Key yagona kirish) ----------
s, _ = req("/api/v1/cameras")
check("2.1 anonim ro'yxat 401", s == 401, s)
s, _ = req("/api/v1/cameras/1/stream")
check("2.2 anonim oqim 401", s == 401, s)
s, _ = req("/api/v1/admin/status")
check("2.3 anonim admin 401", s == 401, s)
s, _ = req("/api/v1/admin/status", headers=BAD_KEY)
check("2.4 noto'g'ri kalit 401", s == 401, s)

# ---------- 3. API kalit — server-to-server ----------
s, d = req("/api/v1/cameras", headers=KEY)
check("3.1 kalit bilan ro'yxat (demo 6 ta)", s == 200 and d["total"] == 6, d)
s, d = req("/api/v1/admin/status", headers=KEY)
check("3.2 MediaMTX tirik", s == 200 and d["mediamtx"] is True, d)
check("3.3 tugun online", d["nodes"][0]["status"] == "online", d["nodes"])
s, d = req("/api/v1/vendors", headers=KEY)
check("3.4 vendors ro'yxati", s == 200 and any(v["id"] == "hikvision" for v in d), s)

# ---------- 4. Kamera CRUD (kalit bilan) ----------
new_cam = {
    "name": "Sinov kamera", "region": "Sinovobod", "lat": 41.0, "lng": 69.0,
    "ip": "127.0.0.1", "port": 9, "username": "admin", "password": "p123",
    "vendor": "hikvision", "rtsp_path": "/Streaming/Channels/101",
}
s, cam = req("/api/v1/admin/cameras", "POST", new_cam, headers=KEY)
check("4.1 kamera yaratildi", s == 201 and cam.get("id"), (s, cam))
cam_id = cam["id"]
check("4.2 javobda parol yo'q, has_password bor",
      "password" not in cam and cam.get("has_password") is True, list(cam))
check("4.3 state maydoni bor", cam.get("state") in
      ("online", "offline", "unknown", "stalled", "disabled"), cam.get("state"))
s, d = req("/api/v1/admin/cameras?q=Sinov", headers=KEY)
check("4.4 qidiruv topadi", s == 200 and d["total"] == 1, d["total"])
upd = dict(new_cam, name="Sinov kamera 2", password=None)
s, d = req(f"/api/v1/admin/cameras/{cam_id}", "PUT", upd, headers=KEY)
check("4.5 tahrirlash (parol saqlanadi)", s == 200 and d["name"] == "Sinov kamera 2"
      and d["has_password"] is True, (s, d.get("name")))

# ---------- 5. Oqim manzili va chipta ----------
s, d = req(f"/api/v1/cameras/{cam_id}/stream", headers=KEY)
check("5.1 oqim manzili chipta bilan", s == 200 and "token=" in d["stream_url"], d)
token = d["stream_url"].split("token=")[1]
slug = d["stream_url"].split("8888/")[1].split("/index")[0]
# MediaMTX nomidan chipta tekshiruvi (haqiqiy oqim ochilganda shu yo'l ishlaydi)
s, d = req("/api/v1/auth/stream", "POST",
           {"ip": "10.0.0.5", "action": "read", "path": slug, "query": f"token={token}"})
check("5.2 to'g'ri chipta qabul qilinadi", s == 200, (s, d))
s, d = req("/api/v1/auth/stream", "POST",
           {"ip": "10.0.0.6", "action": "read", "path": slug, "query": "token=soxta"})
check("5.3 soxta chipta rad etiladi", s == 401, s)
s, d = req("/api/v1/auth/stream", "POST",
           {"ip": "10.0.0.5", "action": "publish", "path": slug, "query": f"token={token}"})
check("5.4 tashqaridan publish rad etiladi", s == 401, s)

# ---------- 6. Probe (kamera yo'q — sabab aniq aytilsin) ----------
s, d = req("/api/v1/admin/probe", "POST",
           {"ip": "127.0.0.1", "port": 9, "rtsp_path": "/x", "username": "", "password": ""},
           headers=KEY)
check("6.1 probe: tarmoq bosqichida to'xtaydi", s == 200 and d["ok"] is False
      and d["stage"] == "tarmoq", d)

# ---------- 7. NVR import (dry_run) ----------
s, d = req("/api/v1/admin/nvr/import", "POST", {
    "ip": "127.0.0.1", "port": 9, "username": "a", "password": "b",
    "vendor": "hikvision", "channels": "1-4", "region": "Sinovobod",
    "lat": 41.0, "lng": 69.0, "dry_run": True, "probe": True,
}, headers=KEY)
check("7.1 NVR dry_run: 4 kanal, 0 javob, 0 yozildi", s == 200
      and len(d["planned"]) == 4 and d["reachable"] == 0 and d["created"] == 0, d)

# ---------- 8. Admin cookie oqimi (parol bilan) ----------
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
s, d = req("/api/v1/auth/login", "POST",
           {"username": "admin", "password": os.environ.get("NIGOH_ADMIN_PAROL", "sinov-admin-987")}, opener=op)
check("8.1 admin login", s == 200 and d["role"] == "admin", (s, d))
s, d = req("/api/v1/auth/me", opener=op)
check("8.2 /me admin", s == 200 and d["authenticated"] and d["role"] == "admin", d)

# ---------- 9. Debug UI foydalanuvchilari (rollar asosiy tizimda) ----------
s, d = req("/api/v1/admin/users", "POST",
           {"username": "op1", "password": "op1parol"}, headers=KEY)
check("9.1 foydalanuvchi yaratildi (hamma admin)",
      s == 201 and d["role"] == "admin" and d["regions"] == [], (s, d))
op_id = d["id"]
cj2 = http.cookiejar.CookieJar()
op2 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj2))
s, d = req("/api/v1/auth/login", "POST",
           {"username": "op1", "password": "op1parol"}, opener=op2)
check("9.2 login ishlaydi", s == 200 and d["role"] == "admin", (s, d))
s, d = req("/api/v1/cameras", opener=op2)
check("9.3 hudud filtri yo'q — hammasini ko'radi", s == 200 and d["total"] >= 7, d)
s, d = req("/api/v1/admin/users/" + str(op_id), "DELETE", headers=KEY)
check("9.4 foydalanuvchi o'chirildi", s == 204, s)

# ---------- 10. MediaMTX sinxron va hodisalar ----------
s, d = req("/api/v1/admin/mediamtx/sync", "POST", headers=KEY)
check("10.1 sync ishlaydi (tugun bo'yicha)", s == 200 and d["live"]["ok"]
      and d["live"]["nodes"][0]["node"] == "Asosiy", d.get("live"))
s, d = req("/api/v1/admin/events", headers=KEY)
check("10.2 hodisalar jurnali (restart yozilgan)", s == 200
      and any(e["kind"] == "mediamtx" for e in d["events"]), len(d.get("events", [])))
s, d = req("/api/v1/admin/nodes", headers=KEY)
check("10.3 tugun runtime ko'rsatkichlari", s == 200
      and d["nodes"][0]["runtime"] is not None
      and "readers" in d["nodes"][0]["runtime"], d["nodes"][0].get("runtime"))

# ---------- 11. Eski (/api) alias ----------
s, d = req("/api/cameras", headers=KEY)
check("11.1 eski /api/cameras ishlaydi", s == 200 and d["total"] == 7, d.get("total"))

# ---------- 12. Tozalash ----------
s, _ = req(f"/api/v1/admin/cameras/{cam_id}", "DELETE", headers=KEY)
check("12.1 sinov kamerasi o'chirildi", s == 204, s)
s, d = req("/api/v1/cameras", headers=KEY)
check("12.2 baza asl holiga qaytdi (6 demo)", d["total"] == 6, d["total"])

print(f"\nJAMI: {passed} PASS, {failed} FAIL")
raise SystemExit(1 if failed else 0)
