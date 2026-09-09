"""Nigoh — server tomonda video devor (mozaika).

Bir necha kameraning SUB-oqimini bitta katakli video oqimga birlashtiradi:
brauzer 36 ta emas, BITTA oqim ochadi. Necha brauzer ochsa ham hammasi
o'sha bitta mozaika oqimini ko'radi — dekodlash yuki brauzerdan serverga
o'tadi va bir marta bajariladi.

Bu modul faqat FFmpeg BUYRUG'INI quradi (mosaic_args). Uni ishga tushirish
`stream_launcher.py` ning `wall_*` tarmog'ida, kamera tanlovi esa
`media/walls.py` registrida.

Nega xstack:
  N ta kirishni bitta filtr bosqichida setkaga tizadi (overlay'da N-1 ta
  bosqich kerak bo'lardi). FFmpeg 5.1+ da `grid=CxR` qisqartmasi bor, lekin
  u aynan C*R ta kirish talab qiladi va hamma katak bir xil o'lchamda
  bo'lishi shart — shuning uchun har kirish avval `scale` bilan tenglashtiriladi,
  yetishmagan kataklar esa qora `color` manbasi bilan to'ldiriladi.

Robustlik: har kirishga qisqa timeout va qayta ulanish qo'yiladi. Bitta
kamera yiqilsa uni MediaMTX relay ushlab turadi (mozaika undan o'qiydi,
kameradan emas), shuning uchun bitta o'lik manba butun setkani qotira
olmaydi — o'sha katak qora bo'lib qoladi, xolos.
"""
from __future__ import annotations

import math

# Katak (tile) standart o'lchami. Sub-oqim odatda shundan katta emas;
# kattaroq bo'lsa scale kichraytiradi, kichik bo'lsa kattalashtiradi.
TILE_W = 640
TILE_H = 360

# Har mozaika kirishi uchun ulanish bayroqlari. RTSP TCP, tez tahlil,
# va o'lik manba butun grafni bloklab qo'ymasligi uchun timeout.
#   -timeout — soket I/O da shuncha mikrosoniya jim tursa uziladi
#     (FFmpeg 8.x da eski `-rw_timeout` olib tashlangan; RTSP demuxer
#      uchun to'g'ri opsiya — `-timeout`, mikrosoniyada).
# analyzeduration/probesize — bir necha relay BIRDAN ochilganda band-kenglik
# bo'linadi va FFmpeg 1 soniyada ba'zi oqimning SPS'ini (o'lchamini) topa
# olmaydi ("unspecified size") — natijada butun mozaika yiqiladi. Yakka
# oqim uchun 1s yetardi, mozaikada 5s qo'yamiz (ochilish biroz sekinroq,
# lekin ishonchli).
_INPUT_FLAGS = [
    "-fflags", "nobuffer", "-flags", "low_delay",
    "-analyzeduration", "5000000", "-probesize", "5000000",
    "-rtsp_transport", "tcp",
    "-timeout", "5000000",
]


def grid_for(n: int, cols: int | None = None, rows: int | None = None) -> tuple[int, int]:
    """Kameralar soniga qarab eng ixcham setka (ustun, qator).

    Aniq setka berilsa (masalan 4×4) — o'sha ishlatiladi. Aks holda
    kvadratga yaqin: 5 kamera -> 3×2, 16 -> 4×4, 30 -> 6×5.
    """
    if cols and rows:
        return cols, rows
    n = max(1, n)
    c = math.ceil(math.sqrt(n))
    r = math.ceil(n / c)
    return c, r


def mosaic_args(inputs: list[str], *, cols: int | None = None, rows: int | None = None,
                dst_url: str, gpu: bool = False, tile_w: int = TILE_W,
                tile_h: int = TILE_H, bitrate: str = "6M", fps: int = 12) -> list[str]:
    """N ta RTSP kirishdan bitta katakli mozaika oqimini yasovchi FFmpeg
    argumentlari.

    inputs   — kirish URL'lari (odatda rtsp://127.0.0.1/<slug>_sub?token=...)
    cols/rows— aniq setka; berilmasa kamera soniga qarab tanlanadi
    dst_url  — chiqish (rtsp://127.0.0.1/wall_xxx?token=...)
    gpu      — NVENC (dekod+kodlash GPU'da); False bo'lsa libx264 (CPU)
    fps      — chiqish kadr tezligi; devor uchun 10-15 yetarli, yukni kamaytiradi
    """
    cols, rows = grid_for(len(inputs), cols, rows)
    cells = cols * rows
    if len(inputs) > cells:
        inputs = inputs[:cells]

    args: list[str] = ["-hide_banner", "-loglevel", "warning"]
    black = ["-f", "lavfi", "-i", f"color=c=#0B1220:s={tile_w}x{tile_h}:r={fps}"]

    # 1) kirishlar. None (o'chiq/yo'q kamera) — O'SHA katakda qora manba,
    #    shunda tirik kameralar joyini o'zgartirmaydi.
    for url in inputs:
        if url is None:
            args += black
        else:
            args += _INPUT_FLAGS + ["-i", url]

    # 2) yetishmagan kataklar — qora manba (lavfi), shunda grid to'ladi
    for _ in range(cells - len(inputs)):
        args += black

    # 3) filtr: har kirishni bir xil o'lchamga keltirib, xstack setkasiga
    parts = []
    for i in range(cells):
        # fps={fps} — avval kadrni doimiy tezlikka keltiramiz (yetishmasa
        #   oxirgi kadr takrorlanadi, ortiqcha tashlanadi).
        # setpts=N/({fps}*TB) — so'ng PTS'ni KADR RAQAMIDAN qayta yaratamiz:
        #   har kirish 0 dan boshlanuvchi bir xil, toza CFR vaqt o'qiga tushadi.
        #   Jonli RTSP oqimlar har xil mutlaq PTS (va B-kadr reorder) bilan
        #   keladi; xstack framesync filtr sifatida kataklarni bir xil vaqt
        #   belgisida kutadi — PTS'ni tenglamasak birinchi kompozit kadrni
        #   bera olmay QOTADI (publish umuman boshlanmaydi).
        # scale/pad/setsar — kataklar bir xil o'lchamda bo'lsin (xstack sharti).
        parts.append(f"[{i}:v]fps={fps},setpts=N/({fps}*TB),"
                     f"scale={tile_w}:{tile_h}:"
                     f"force_original_aspect_ratio=decrease,"
                     f"pad={tile_w}:{tile_h}:(ow-iw)/2:(oh-ih)/2:color=#0B1220,"
                     f"setsar=1[v{i}]")
    labels = "".join(f"[v{i}]" for i in range(cells))
    parts.append(f"{labels}xstack=inputs={cells}:grid={cols}x{rows}:fill=#0B1220[out]")
    filtergraph = ";".join(parts)

    args += ["-filter_complex", filtergraph, "-map", "[out]"]

    # 4) kodlash
    if gpu:
        args += ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll",
                 "-rc", "cbr", "-b:v", bitrate, "-maxrate", bitrate,
                 "-bufsize", bitrate]
    else:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                 "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate,
                 "-pix_fmt", "yuv420p"]
    args += ["-g", str(fps * 2), "-bf", "0", "-an",
             "-pkt_size", "1200", "-f", "rtsp", "-rtsp_transport", "tcp", dst_url]
    return args
