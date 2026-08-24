"""Kamera RTSP sessiyasini qancha vaqt ushlab turadi — bog'liqliksiz o'lchov.

Faqat Python standart kutubxonasi. ffmpeg, MediaMTX yoki boshqa hech
narsa kerak emas — istalgan mashinada ishlaydi (Linux, Windows, Proxmox
xosti). Maqsad: bir xil kamerani turli mashinalardan sinab, uzilish
mijozga bog'liqmi yoki tarmoqdagi o'ringa bog'liqmi degan savolga javob
berish.

    python rtsp-sessiya-sinovi.py "rtsp://login:parol@10.30.11.65:554/cam/realmonitor?channel=1&subtype=0" [sekund]

Nima qiladi: OPTIONS -> DESCRIBE -> SETUP -> PLAY qiladi (Digest yoki
Basic auth), keyin oqimni o'qib turadi va KEEPALIVE YUBORMAYDI. Shunda
kamera sessiyani o'z muddati bo'yicha yopadi va biz aynan shu muddatni
o'lchaymiz.

Chiqishda:
    Session ... timeout=N   — kamera qancha muddat e'lon qilgani
    uzildi: N s             — amalda qancha ushlab turgani
"""
import base64
import hashlib
import re
import socket
import sys
import time
import urllib.parse

URL = sys.argv[1] if len(sys.argv) > 1 else ""
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 300
if not URL.startswith("rtsp://"):
    print(__doc__)
    sys.exit(2)

u = urllib.parse.urlsplit(URL)
host, port = u.hostname, u.port or 554
user = urllib.parse.unquote(u.username or "")
pwd = urllib.parse.unquote(u.password or "")
# Manzilda login/parol qolmasin — kamera ba'zan buni yoqtirmaydi.
target = urllib.parse.urlunsplit(
    ("rtsp", f"{host}:{port}", u.path, u.query, ""))

seq = 0
auth_hdr = ""
sock = socket.create_connection((host, port), timeout=10)
sock.settimeout(10)
buf = b""


def digest(method: str, uri: str, ch: str) -> str:
    """Digest javobi (RTSP kameralarda eng ko'p uchraydigan usul)."""
    def g(k):
        m = re.search(k + r'="([^"]*)"', ch)
        return m.group(1) if m else ""
    realm, nonce = g("realm"), g("nonce")
    ha1 = hashlib.md5(f"{user}:{realm}:{pwd}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    return (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
            f'uri="{uri}", response="{resp}"')


def so_rov(method: str, uri: str, extra: str = "") -> tuple[int, str]:
    """Bitta RTSP so'rovi; 401 kelsa auth bilan bir marta qayta yuboradi."""
    global seq, auth_hdr, buf
    for urinish in (1, 2):
        seq += 1
        req = f"{method} {uri} RTSP/1.0\r\nCSeq: {seq}\r\n"
        if auth_hdr:
            req += f"Authorization: {auth_hdr}\r\n"
        req += extra + "\r\n"
        sock.sendall(req.encode())
        # javobni sarlavhalar tugaguncha o'qiymiz
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                return 0, ""
            buf += chunk
        head, _, buf = buf.partition(b"\r\n\r\n")
        head = head.decode("utf-8", "replace")
        code = int(re.search(r"RTSP/1.0 (\d+)", head).group(1))
        # tanasi bo'lsa (DESCRIBE) uni ham o'qib tashlaymiz
        m = re.search(r"Content-Length: (\d+)", head, re.I)
        if m:
            need = int(m.group(1))
            while len(buf) < need:
                buf += sock.recv(65536)
            buf = buf[need:]
        if code == 401 and urinish == 1:
            ch = re.search(r"WWW-Authenticate: (.*)", head, re.I)
            ch = ch.group(1) if ch else ""
            if "Digest" in ch:
                auth_hdr = digest(method, uri, ch)
            else:
                tok = base64.b64encode(f"{user}:{pwd}".encode()).decode()
                auth_hdr = "Basic " + tok
            continue
        return code, head
    return code, head


print(f"kamera: {host}:{port}{u.path}")
t0 = time.time()
c, h = so_rov("OPTIONS", target)
print(f"  OPTIONS  -> {c}")
c, h = so_rov("DESCRIBE", target, "Accept: application/sdp\r\n")
print(f"  DESCRIBE -> {c}")
if c != 200:
    print("  DESCRIBE muvaffaqiyatsiz — login/parol yoki yo'lni tekshiring")
    sys.exit(1)

# Interleaved TCP: video shu ulanish orqali keladi.
c, h = so_rov("SETUP", target.rstrip("/") + "/trackID=0",
              "Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n")
if c != 200:                      # ba'zi kameralar trackID'ni boshqacha ataydi
    c, h = so_rov("SETUP", target, "Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n")
print(f"  SETUP    -> {c}")
m = re.search(r"Session: *([^;\r\n]+)(;timeout=(\d+))?", h, re.I)
sid = m.group(1).strip() if m else ""
tmo = m.group(3) if m and m.group(3) else "e'lon qilinmagan"
print(f"  Session  -> {sid}   timeout={tmo}")
c, h = so_rov("PLAY", target, f"Session: {sid}\r\nRange: npt=0.000-\r\n")
print(f"  PLAY     -> {c}\n")

print(f"oqim o'qilmoqda, keepalive YUBORILMAYDI ({LIMIT} s chegara)...")
boshlandi = time.time()
baytlar = 0
try:
    while time.time() - boshlandi < LIMIT:
        sock.settimeout(5)
        try:
            d = sock.recv(65536)
        except socket.timeout:
            continue
        if not d:
            print(f"\n  ulanish YOPILDI (EOF): {time.time()-boshlandi:.0f} s")
            break
        baytlar += len(d)
    else:
        print(f"\n  {LIMIT} s to'ldi — UZILMADI ({baytlar/1024/1024:.1f} MB)")
except ConnectionResetError:
    print(f"\n  ulanish UZILDI (RST): {time.time()-boshlandi:.0f} s "
          f"({baytlar/1024/1024:.1f} MB)")
except OSError as e:
    print(f"\n  ulanish xatosi {time.time()-boshlandi:.0f} s: {e}")
finally:
    sock.close()
