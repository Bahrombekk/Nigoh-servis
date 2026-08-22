"""Bitta kameraning holati nima uchun o'zgarayotganini jonli ko'rsatadi.

Muammo: "kamera ishlab turadi, keyin offline bo'lib qoladi" — sabab
uchta manbadan biri bo'lishi mumkin va ular bir-birini ko'rmaydi:

  * health  — kamera IP:portiga TCP tekshiruvi (har 60 s);
  * MediaMTX — oqim tayyormi va baytlar kelyaptimi (har 5 s);
  * events  — bazaga yozilgan o'tishlar.

Bu skript uchalasini bitta jadvalda, har 5 soniyada chiqaradi. Kamerani
ochib qo'ying va qaysi ustun birinchi bo'lib o'zgarishini kuzating:

    python scripts/kuzat.py 8
    python scripts/kuzat.py 8 --base http://SERVER:8010 --key KALIT
"""
import argparse, json, os, socket, sys, time, urllib.error, urllib.request

p = argparse.ArgumentParser()
p.add_argument("ref", help="kamera id yoki ext:...")
p.add_argument("--base", default=os.environ.get("NIGOH_BASE", "http://127.0.0.1:8010"))
p.add_argument("--key", default=os.environ.get("NIGOH_API_KEY", ""))
p.add_argument("--interval", type=float, default=5.0)
a = p.parse_args()
if not a.key:
    sys.exit("NIGOH_API_KEY bering: --key ... yoki muhit o'zgaruvchisi")


def api(path):
    req = urllib.request.Request(a.base.rstrip("/") + path)
    req.add_header("X-API-Key", a.key)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def tcp(ip, port, timeout):
    t0 = time.perf_counter()
    try:
        socket.create_connection((ip, port), timeout=timeout).close()
        return f"{(time.perf_counter()-t0)*1000:.0f}ms"
    except OSError as e:
        return f"XATO({type(e).__name__})"


cam = next(c for c in api(f"/api/v1/admin/cameras?q={a.ref}" if not a.ref.isdigit()
                          else "/api/v1/admin/cameras?limit=500")["cameras"]
           if str(c["id"]) == a.ref or c.get("external_id") == a.ref[4:])
ip, port, slug = cam["ip"], cam["port"], cam["slug"]
print(f"kamera {cam['id']}  {cam['name']}  {ip}:{port}  slug={slug}\n")
print(f"{'vaqt':8} {'holat':9} {'tcp(1.5s)':12} {'tcp(5s)':10} "
      f"{'mtx.ready':10} {'baytlar':>12} {'o\'quvchi':8} {'oxirgi hodisa'}")
print("-" * 100)

prev_bytes, prev_state, last_ev = None, None, ""
while True:
    try:
        st = next((c for c in api("/api/v1/cameras/status?ids=" + str(cam["id"]))["cameras"]), {})
        rt = api("/api/v1/admin/runtime")["paths"].get(slug, {})
        evs = api("/api/v1/admin/events?limit=3")["events"]
        ev = f"{evs[0]['ts'][11:19]} {evs[0]['kind']}" if evs else ""
        got = rt.get("bytes_received", 0)
        delta = "" if prev_bytes is None else f"{(got-prev_bytes)/1024:+.0f}KB"
        mark = ""
        if prev_state and st.get("state") != prev_state:
            mark = f"   <<< {prev_state} -> {st.get('state')}"
        print(f"{time.strftime('%H:%M:%S'):8} {st.get('state','?'):9} "
              f"{tcp(ip, port, 1.5):12} {tcp(ip, port, 5.0):10} "
              f"{str(rt.get('ready','-')):10} {got:>12,} {str(rt.get('readers','-')):8} "
              f"{ev}{mark}", flush=True)
        prev_bytes, prev_state = got, st.get("state")
    except Exception as exc:
        print(f"{time.strftime('%H:%M:%S')}  so'rov xatosi: {exc}", flush=True)
    time.sleep(a.interval)
