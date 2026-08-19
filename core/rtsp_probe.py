"""Nigoh — kamerani tekshirish: tarmoq, RTSP javobi va login/parol.

Tashqi kutubxonasiz, RTSP so'rovini to'g'ridan-to'g'ri TCP orqali yuboradi.
Kameralarning aksariyati Digest autentifikatsiyadan foydalanadi, shuning
uchun Basic ham, Digest ham qo'llab-quvvatlanadi.
"""
import base64
import hashlib
import re
import secrets
import socket
import urllib.parse

TIMEOUT = 6.0
USER_AGENT = "Nigoh/1.0"


def build_rtsp_url(ip: str, port: int, path: str,
                   username: str = "", password: str = "") -> str:
    """Kamera uchun to'liq RTSP manzil (login/parol bilan)."""
    path = "/" + (path or "").lstrip("/")
    host = f"[{ip}]" if ":" in ip and not ip.startswith("[") else ip
    if username:
        cred = urllib.parse.quote(username, safe="")
        if password:
            cred += ":" + urllib.parse.quote(password, safe="")
        return f"rtsp://{cred}@{host}:{port}{path}"
    return f"rtsp://{host}:{port}{path}"


def _digest_header(username: str, password: str, method: str, uri: str,
                   challenge: str, nc: int = 1,
                   cnonce: str | None = None) -> str:
    """Digest Authorization sarlavhasi.

    Kamera `qop` talab qilsa (Axis, ko'p ONVIF qurilma, ba'zi Dahua
    firmware) RFC 2617 formulasi ishlatiladi: MD5(HA1:nonce:nc:cnonce:
    qop:HA2). `qop`siz eski RFC 2069 formulasi qoladi. Bitta nonce bilan
    ikkinchi so'rov (SETUP) yuborilganda `nc` oshirilishi shart.
    """
    fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    realm = fields.get("realm", "")
    nonce = fields.get("nonce", "")
    # qop qo'shtirnoqli ham, qo'shtirnoqsiz ham keladi: qop="auth" / qop=auth
    qop_raw = fields.get("qop", "")
    if not qop_raw:
        match = re.search(r'qop=([^,\s"]+)', challenge)
        qop_raw = match.group(1) if match else ""
    qop = "auth" if "auth" in [q.strip() for q in qop_raw.split(",")] else ""

    md5 = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
    ha1 = md5(f"{username}:{realm}:{password}")
    ha2 = md5(f"{method}:{uri}")
    if qop:
        nc_value = f"{nc:08x}"
        cnonce = cnonce or secrets.token_hex(8)
        response = md5(f"{ha1}:{nonce}:{nc_value}:{cnonce}:{qop}:{ha2}")
    else:
        response = md5(f"{ha1}:{nonce}:{ha2}")
    header = (
        f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
        f'uri="{uri}", response="{response}"'
    )
    if qop:
        header += f', qop={qop}, nc={nc_value}, cnonce="{cnonce}"'
    if "opaque" in fields:
        header += f', opaque="{fields["opaque"]}"'
    return header


def _request(sock, method: str, uri: str, cseq: int, auth: str = "",
             extra: list[str] | None = None) -> str:
    lines = [
        f"{method} {uri} RTSP/1.0",
        f"CSeq: {cseq}",
        f"User-Agent: {USER_AGENT}",
    ]
    if method == "DESCRIBE":
        lines.append("Accept: application/sdp")
    if extra:
        lines.extend(extra)
    if auth:
        lines.append(f"Authorization: {auth}")
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())

    chunks = b""
    while b"\r\n\r\n" not in chunks:
        data = sock.recv(4096)
        if not data:
            break
        chunks += data
        if len(chunks) > 65536:
            break
    return chunks.decode("utf-8", "replace")


def _status(response: str) -> int:
    match = re.match(r"RTSP/\d\.\d (\d+)", response)
    return int(match.group(1)) if match else 0


def sdp_codec(describe: str) -> str:
    """DESCRIBE javobidagi SDP dan video kodekni ajratadi (H264 / H265 …)."""
    for codec in re.findall(r"a=rtpmap:\d+ ([A-Za-z0-9\-]+)/", describe):
        upper = codec.upper()
        if upper in ("H264", "H265", "HEVC", "MP4V-ES", "JPEG", "AV1", "VP8", "VP9"):
            return "H265" if upper == "HEVC" else upper
    return ""


def sdp_resolution(describe: str) -> str:
    """SDP'dan kadr o'lchamini ajratadi ("1920x1080" yoki bo'sh).

    Kameralar buni har xil beradi: Hikvision `a=x-dimensions:1920,1080`,
    boshqalar `a=framesize:96 1920-1080`; ba'zilari umuman bermaydi
    (o'lcham H.264 SPS ichida yashiringan bo'ladi) — u holda bo'sh.
    """
    match = re.search(r"a=x-dimensions:\s*(\d+)\s*,\s*(\d+)", describe)
    if not match:
        match = re.search(r"a=framesize:\d+\s+(\d+)-(\d+)", describe)
    return f"{match.group(1)}x{match.group(2)}" if match else ""


def sdp_fps(describe: str) -> float:
    """SDP'dan kadr tezligini ajratadi (bermagan kamerada 0)."""
    match = re.search(r"a=(?:x-)?framerate:\s*([\d.]+)", describe)
    try:
        return float(match.group(1)) if match else 0.0
    except ValueError:
        return 0.0


def sdp_has_audio(describe: str) -> bool:
    return "m=audio" in describe


def sdp_video_control(describe: str, request_uri: str) -> str:
    """SDP ichidan video trekning SETUP manzilini topadi.

    Kameralar buni uch xil beradi: to'liq URL, nisbiy yo'l ('trackID=1')
    yoki umuman bermaydi. Nisbiy bo'lsa Content-Base'ga qo'shiladi.
    """
    base = request_uri
    match = re.search(r"^Content-Base:\s*(\S+)", describe,
                      re.IGNORECASE | re.MULTILINE)
    if match:
        base = match.group(1)

    control, in_video = "", False
    for line in describe.splitlines():
        if line.startswith("m="):
            in_video = line.startswith("m=video")
        elif in_video and line.startswith("a=control:"):
            control = line.split(":", 1)[1].strip()
            break

    if not control or control == "*":
        return base
    if control.lower().startswith("rtsp://"):
        return control
    return base.rstrip("/") + "/" + control.lstrip("/")


def probe(ip: str, port: int, path: str, username: str = "",
          password: str = "") -> dict:
    """Kamerani bosqichma-bosqich tekshiradi.

    Qaytaradi: {ok, stage, message, codec, needs_transcode,
                resolution, fps, audio}
      stage — qaysi bosqichda to'xtagani: tarmoq / rtsp / parol / tayyor
      codec — kameradan kelayotgan video kodek
      needs_transcode — brauzer o'qishi uchun H.264 ga o'girish kerakmi
      resolution/fps/audio — SDP'dan; kamera bermasa bo'sh/0/False
    """
    def fail(stage: str, message: str) -> dict:
        return {"ok": False, "stage": stage, "message": message,
                "codec": "", "needs_transcode": False,
                "resolution": "", "fps": 0.0, "audio": False}

    if not ip:
        return fail("tarmoq", "IP manzil ko'rsatilmagan")

    # 1-bosqich: TCP ulanish
    try:
        sock = socket.create_connection((ip, port), timeout=TIMEOUT)
    except socket.gaierror:
        return fail("tarmoq", f"{ip} manzili topilmadi (DNS xatosi)")
    except socket.timeout:
        return fail("tarmoq", f"{ip}:{port} javob bermadi — kamera o'chiq yoki "
                              f"boshqa tarmoqda")
    except OSError as exc:
        return fail("tarmoq",
                    f"{ip}:{port} ga ulanib bo'lmadi ({exc.strerror or exc})")

    uri = build_rtsp_url(ip, port, path)
    try:
        sock.settimeout(TIMEOUT)

        # 2-bosqich: RTSP protokoli javob beryaptimi
        try:
            options = _request(sock, "OPTIONS", uri, 1)
        except (socket.timeout, OSError):
            return fail("rtsp", f"{ip}:{port} ochiq, lekin RTSP javobi kelmadi — "
                                f"port raqamini tekshiring")
        if not options.startswith("RTSP/"):
            return fail("rtsp", f"{ip}:{port} RTSP xizmati emas")

        # 3-bosqich: DESCRIBE — bu yerda login/parol tekshiriladi
        describe = _request(sock, "DESCRIBE", uri, 2)
        code = _status(describe)

        challenge = ""
        if code == 401:
            if not username:
                return fail("parol", "Kamera login/parol so'rayapti — ularni kiriting")
            for line in describe.split("\r\n"):
                if line.lower().startswith("www-authenticate:"):
                    challenge = line.split(":", 1)[1].strip()
                    break

            if challenge.lower().startswith("digest"):
                auth = _digest_header(username, password, "DESCRIBE", uri, challenge)
            else:
                token = base64.b64encode(f"{username}:{password}".encode()).decode()
                auth = f"Basic {token}"

            describe = _request(sock, "DESCRIBE", uri, 3, auth)
            code = _status(describe)

            if code == 401:
                return fail("parol", "Login yoki parol noto'g'ri")

        if code == 404:
            return fail("rtsp", f"RTSP yo'li topilmadi: {path} — ishlab chiqaruvchi "
                                f"shablonini tekshiring")
        if code and code >= 400:
            return fail("rtsp", f"Kamera {code} kodi bilan rad etdi")
        if code == 0:
            return fail("rtsp", "Kameradan tushunarsiz javob")

        codec = sdp_codec(describe)
        needs_transcode = codec in ("H265", "MP4V-ES", "JPEG")
        resolution = sdp_resolution(describe)
        fps = sdp_fps(describe)
        audio = sdp_has_audio(describe)

        # Ba'zi qurilmalar istalgan (hatto mavjud bo'lmagan) yo'lga ham 200
        # qaytaradi, lekin videosiz bo'sh SDP beradi — bu ishlaydigan oqim
        # emas, xato deb qaytaramiz, aks holda skaner soxta kanallar topadi.
        if "m=video" not in describe:
            return fail("oqim", "Kamera javob berdi, lekin bu yo'lda video "
                                "oqim yo'q — RTSP yo'lini tekshiring")

        # 4-bosqich: SETUP — kamera oqimni haqiqatan beradimi. DESCRIBE'ga
        # javob berib, SETUP'da rad etadigan kameralar uchraydi (masalan,
        # o'chirilgan qo'shimcha oqim yoki band kanal) — bularni shu yerda
        # ushlaymiz, aks holda "ok" deb saqlanadi-yu, video ochilmaydi.
        if "m=video" in describe:
            setup_uri = sdp_video_control(describe, uri)
            transport = ["Transport: RTP/AVP/TCP;unicast;interleaved=0-1"]
            setup_auth = ""
            if challenge and challenge.lower().startswith("digest"):
                # Bitta nonce ichida ikkinchi so'rov — nc oshiriladi.
                setup_auth = _digest_header(username, password, "SETUP",
                                            setup_uri, challenge, nc=2)
            elif challenge:
                token = base64.b64encode(f"{username}:{password}".encode()).decode()
                setup_auth = f"Basic {token}"
            try:
                setup = _request(sock, "SETUP", setup_uri, 4, setup_auth, transport)
            except (socket.timeout, OSError):
                return fail("oqim", "Kamera SETUP so'roviga javob bermadi")
            setup_code = _status(setup)
            if setup_code == 461:
                # TCP transportni bilmaydi — UDP bilan qayta urinamiz.
                udp = ["Transport: RTP/AVP;unicast;client_port=45678-45679"]
                try:
                    setup = _request(sock, "SETUP", setup_uri, 5, setup_auth, udp)
                    setup_code = _status(setup)
                except (socket.timeout, OSError):
                    setup_code = 0
            if setup_code and setup_code >= 400:
                return fail("oqim",
                            f"Kamera javob beradi, lekin oqimni bermayapti "
                            f"(SETUP {setup_code}) — bu oqim/kanal o'chiq yoki "
                            f"band bo'lishi mumkin, boshqa yo'lni sinang")

        if needs_transcode:
            message = (f"Ulanish muvaffaqiyatli · kodek {codec} — brauzer buni "
                       f"o'qiy olmaydi, H.264 ga o'girib beriladi")
        elif codec:
            message = f"Ulanish muvaffaqiyatli · kodek {codec}"
        else:
            message = "Ulanish muvaffaqiyatli"
        if resolution:
            message += f" · {resolution}"
        if fps:
            message += f" · {fps:g} fps"
        if audio:
            message += " · audio bor"

        return {"ok": True, "stage": "tayyor", "message": message,
                "codec": codec, "needs_transcode": needs_transcode,
                "resolution": resolution, "fps": fps, "audio": audio}
    except (socket.timeout, OSError) as exc:
        # Ba'zi NVR'lar mavjud bo'lmagan kanal/oqim so'ralganda ulanishni
        # majburan uzadi (ConnectionReset) — bu tizim xatosi emas,
        # "bunday oqim yo'q" degani.
        return fail("rtsp", f"Kamera ulanishni uzib qo'ydi "
                            f"({exc.__class__.__name__}) — bu yo'l/kanal "
                            f"mavjud emas bo'lishi mumkin")
    finally:
        try:
            sock.close()
        except OSError:
            pass
