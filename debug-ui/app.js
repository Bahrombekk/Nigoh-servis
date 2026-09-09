"use strict";
/* Nigoh — diagnostika konsoli (v4, "Nigoh Diagnostika" maketi asosida).

   Konsol bitta savolga javob beradi: "xato qayerda — kamerada,
   registratorda, tugunda yoki brauzerda?" Bosh sahifa shu savolning
   o'zi: muammoli kameralar chapda, tanlangani bo'yicha hukm va zanjir
   bo'ylab dalillar o'ngda. Qolgan sahifalar hukmni tekshirish uchun:
   ochilish tezligi (open_ms budjeti), topologiya (tugun → registrator),
   kameralar jadvali, resurs, uzilishlar, devor, kamera sahifasi.

   Hamma ma'lumot haqiqiy API'dan — soxta ko'rsatkich yo'q, o'lchanmagan
   narsa "—" bo'lib chiqadi:
     /api/v1/admin/cameras   ro'yxat (to'liq maydonlar)
     /api/v1/cameras/status  snapshot_at va yangi holat
     /api/v1/admin/runtime   slug -> baytlar/tomoshabinlar (tezlik farqdan)
     /api/v1/admin/uptime    uzilishlar reytingi (kamera / guruh kesimida)
     /api/v1/admin/outages/hourly, /admin/cameras/{id}/history
     /api/v1/admin/events    hodisalar jurnali (sabab kesimi shundan)
     /api/v1/events          SSE — holat o'zgarishlari
     /health, /admin/status, /admin/nodes  resurs va topologiya          */

/* ═════════ yordamchi ═════════ */
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const age = (s) => s == null || s < 0 ? "yo'q"
  : s < 60 ? Math.round(s) + " s"
  : s < 3600 ? Math.round(s / 60) + " daq" : Math.round(s / 3600) + " soat";
const clock = () => new Date().toTimeString().slice(0, 8);
const LBL = {online:"online", stalled:"stalled · kadr yo'q", offline:"offline",
             unknown:"unknown · tekshirilmagan", disabled:"o'chirilgan",
             nostream:"oqim yo'q"};
/* Muammo-yo'naltirilgan ko'rinish: e'tibor talab qiladigan holatlar va
   ularning tartibi — yomoni tepada. */
const PROB = new Set(["offline", "stalled", "unknown"]);
/* Ko'rsatish uchun holat: port 554 ochiq bo'lsa health "online" deydi,
   lekin RTSP tekshiruvi kodek olmagan bo'lsa oqim aslida ishlamaydi
   (ko'pincha login/parol noto'g'ri yoki RTSP yo'l xato). Bunday kamerani
   alohida "nostream" toifasiga chiqaramiz va MUAMMOLI hisoblaymiz. */
function effState(c) {
  if (c.state === "online" && c.ip && !c.codec && c.source_type !== "manual") return "nostream";
  return c.state;
}
/* Kamera e'tibor talab qiladimi (muammoli ro'yxati, filtr, sanoq shu). */
function isProb(c) {
  const e = effState(c);
  return e === "offline" || e === "stalled" || e === "unknown" || e === "nostream";
}
const RANK = {offline:0, nostream:1, stalled:2, unknown:3, disabled:4, online:5};
/* Holat -> nuqta rangi (CSS o'zgaruvchilari style.css da). */
const DOT = {online:"var(--d-on)", stalled:"var(--d-stall)", offline:"var(--d-off)",
             unknown:"var(--d-unk)", disabled:"var(--d-dis)", nostream:"var(--d-stall)"};
/* Hukm turi -> nuqta rangi / matn rangi. */
const KDOT = {ok:"var(--d-on)", warn:"var(--d-stall)", bad:"var(--d-off)", idle:"var(--d-unk)"};
const KINK = {ok:"var(--green)", warn:"var(--amber)", bad:"var(--red)", idle:"var(--gray)"};
const stateKind = (st) => st === "online" ? "ok" : st === "stalled" || st === "nostream" ? "warn"
  : st === "offline" ? "bad" : "idle";

/* Ikonka — index.html dagi SVG sprite'dan. */
const ic = (n, cls = "") => `<svg class="ic ${cls}"><use href="#i-${n}"/></svg>`;
const OYLAR = ["yan", "fev", "mar", "apr", "may", "iyun", "iyul", "avg", "sen", "okt", "noy", "dek"];
const dateLbl = (d = new Date()) => `${d.getDate()} ${OYLAR[d.getMonth()]} ${d.getFullYear()}`;
const pad2 = (n) => (n < 10 ? "0" : "") + n;
const dd = pad2;
/* Server UTC beradi, operator mahalliy vaqtni ko'radi. */
/* Server vaqtni UTC'da, ko'pincha zonasiz beradi ("2026-09-07 10:00:00" yoki
   "...T10:00:00"). Zonasiz satr brauzerda mahalliy deb o'qilardi va hamma
   vaqt 5 soat siljib ko'rinardi — shuning uchun zona yo'q bo'lsa Z qo'shiladi. */
const utc = (iso) => {
  if (!iso) return new Date(NaN);
  let s = String(iso).trim().replace(" ", "T");
  if (!/(Z|[+-]\d\d:?\d\d)$/.test(s)) s += "Z";
  return new Date(s);
};
const localHM = (iso) => { const d = utc(iso); return isNaN(d) ? "—" : dd(d.getHours()) + ":" + dd(d.getMinutes()); };
const localDM = (iso) => { const d = utc(iso); return isNaN(d) ? "—" : dd(d.getDate()) + "." + dd(d.getMonth() + 1); };
/* Sekundlarni odam o'qiydigan davomiylikka: 0 bo'lsa chiziqcha. */
const durHM = (sec) => {
  if (!sec) return "—";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h}h ${pad2(m)}m` : `${m}m`;
};
const fmtDur = (s) => s < 60 ? Math.round(s) + " s"
  : s < 3600 ? Math.round(s / 60) + " daqiqa"
  : s < 86400 ? (s / 3600).toFixed(1) + " soat" : (s / 86400).toFixed(1) + " kun";
/* Uptime foizining rangi: 99% dan yuqori — normal, 95% gacha — e'tibor. */
const upColor = (p) => p >= 99 ? "var(--green)" : p >= 95 ? "var(--amber)" : "var(--red)";
/* ISO vaqtdan hozirgacha o'tgan soniyalar (noto'g'ri bo'lsa -1). */
const sinceSec = (iso) => {
  if (!iso) return -1;
  const t = utc(iso).getTime();
  return isNaN(t) ? -1 : Math.max(0, (Date.now() - t) / 1000);
};

function toast(t, b, k = "") {
  const d = document.createElement("div");
  d.className = "toast " + k;
  d.innerHTML = `<b>${esc(t)}</b><span>${esc(b)}</span>`;
  $("#toasts").append(d);
  setTimeout(() => { d.style.opacity = 0; d.style.transition = "opacity .3s";
    setTimeout(() => d.remove(), 300); }, 3400);
}

async function api(path, options = {}) {
  if (options.body && typeof options.body !== "string") {
    options.body = JSON.stringify(options.body);
    options.headers = {"Content-Type": "application/json", ...options.headers};
  }
  const t0 = performance.now();
  const res = await fetch(path, options);
  noteApi(performance.now() - t0);
  if (res.status === 401) { showGate(); throw new Error("Kirish kerak"); }
  if (!res.ok) {
    let detail = res.status + "-xato";
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

/* API kechikishi — shu brauzerning o'z so'rovlaridan p95. Resurs
   sahifasida chiqadi: "konsol sekin" degan shikoyat servisdami yoki
   tarmoqdami — shu raqam aytadi. */
const API_LAT = [];
function noteApi(ms) {
  API_LAT.push(ms);
  if (API_LAT.length > 200) API_LAT.shift();
}
function apiP95() {
  if (!API_LAT.length) return null;
  const a = [...API_LAT].sort((x, y) => x - y);
  return Math.round(a[Math.min(a.length - 1, Math.round((a.length - 1) * 0.95))]);
}

/* ═════════ yuqori chiziq ═════════
   Sahifadan qat'i nazar ko'rinadi: "hozir qanday" degan savolga javob
   sahifa almashtirmasdan olinadi. Health tsikli 60 s, reconciler 30 s —
   serverdagi doimiylar (core/health.py, media/reconciler.py). */
function drawTopbar() {
  const cams = S.cams || [];
  const cnt = (st) => cams.filter((c) => c.state === st).length;
  const prob = cams.filter((c) => isProb(c)).length;
  const kind = !cams.length ? "okp" : prob ? (cnt("offline") ? "bad" : "warn") : "okp";
  $("#hpill").className = "hchip " + kind;
  $("#tbtitle").textContent = cams.length ? `${prob} muammoli` : "yuklanmoqda…";
  $("#hcamsn").textContent = `${cams.length} kamera`;
  const h = S.health;
  $("#tbmeta").textContent = h ? `managed ${h.managed} · tomoshabin ${h.readers}` : "—";
}
setInterval(() => { $("#clock").textContent = clock(); $("#cdate").textContent = dateLbl(); }, 1000);

/* Brauzer H.265 ni o'zi o'qiy oladimi — olsa server o'girmaydi.

   DIQQAT: savol WebRTC uchun so'raladi. Server bitta yo'l qaytaradi,
   pleyer esa avval WebRTC'ni sinaydi. Brauzerning HLS (MSE) tomoni
   H.265 ni bilishi, WebRTC tomoni esa ko'rsatmasligi mumkin —
   Windows'dagi Edge aynan shunday. "Ikkisidan biri bilsa yetadi" deb
   hisoblansa xom H.265 WebRTC'ga beriladi va baytlar oqib turgan holda
   tasvir birinchi kadrda qotib qoladi. Shuning uchun WebRTC bor bo'lsa
   hukmni faqat u chiqaradi. */
const HEVC_OK = (() => {
  try {
    const caps = RTCRtpReceiver.getCapabilities("video");
    if (caps) return caps.codecs.some((c) => /H265|hevc/i.test(c.mimeType));
  } catch (e) {}
  const type = 'video/mp4; codecs="hvc1.1.6.L93.B0"';
  try { if (window.MediaSource && MediaSource.isTypeSupported(type)) return true; } catch (e) {}
  return document.createElement("video").canPlayType(type) === "probably";
})();

/* ═════════ holat ═════════ */
const S = {
  cams: [], byId: new Map(),
  rt: {},                 // slug -> {ready, readers, bytes_received, warm}
  rates: {},              // slug -> Mbit/s (ikki so'rov orasidagi farq)
  prevRt: null,           // {t, paths}
  filt: "prob", sortK: "state", sortD: 1, picked: new Set(),
  up7: null, up7At: 0,    // kamera id -> 7 kunlik uptime (jadval ustuni)
  wallN: 9, wallMode: "all", wallView: "snap", wallRegion: "", wallNode: "", wallQ: "", wallPage: 1,
  evlog: [], evFilt: "all", evPause: false, evN: 0,
  gBy: "region", gHours: 24, groups: null,      // topologiya: reyting jadvali
  tHours: 24, stat: null, worst: null, causes: null,   // uzilishlar sahifasi
  diagDay: 0, hist: null,                       // kamera sahifasi
  sel: null, busy: null, note: {}, vsort: "worst",   // "xato qayerda?" tanlovi va amal izi
  camsAt: 0, tq: "",
  cpage: 1, cper: 25,          // kameralar jadvali sahifasi
  curId: null, page: "verdict",
  nodes: [], health: null, status: null, recentEv: [],
};

/* Kameraning hosilaviy runtime qiymatlari (jadval/devor uchun). */
function camRt(cam) {
  const r = (slug) => S.rates[slug] || 0;
  const p = (slug) => S.rt[slug];
  const variants = [cam.slug, cam.slug + "_sub", cam.slug + "_h264"];
  return {
    inMbps: r(cam.slug) + r(cam.slug + "_sub"),
    readers: variants.reduce((s, v) => s + ((p(v) || {}).readers || 0), 0),
    ready: !!(p(cam.slug) || {}).ready,
    warm: variants.some((v) => (p(v) || {}).warm),
    bytes: variants.reduce((s, v) => s + ((p(v) || {}).bytes_received || 0), 0),
    // Shu kameraga ochilgan RTSP sessiyalar (asosiy + sub). Registrator
    // bir vaqtdagi sessiyalarni cheklaydi — sanash shu uchun.
    sessions: (p(cam.slug) ? 1 : 0) + (p(cam.slug + "_sub") ? 1 : 0),
    transcoding: !!p(cam.slug + "_h264"),
  };
}
function snapAge(cam) { return sinceSec(cam.snapshot_at); }
/* Kamera qaysi tugunda — nom bilan. */
function nodeName(c) {
  const n = (S.nodes || []).find((x) => x.id === (c.node_id || 1));
  return n ? n.name : (c.node_id || 1) === 1 ? "asosiy tugun" : "tugun #" + c.node_id;
}

/* ═════════ registrator (bitta IP ortidagi kanallar) ═════════

   Bazada "registrator" degan alohida obyekt yo'q — bitta IP manzilda
   bir necha kamera bo'lsa, bu registratorning kanallari. Ularning
   holatini birga ko'rish hukm uchun eng kuchli dalil: barcha kanal
   birga yo'qolgan bo'lsa aybdor kamera emas, registrator (quvvat,
   magistral). Bitta kanal yo'qolgan bo'lsa — kanalning o'zi.        */
function ipGroup(c) {
  if (!c.ip) return [c];
  return S.cams.filter((x) => x.ip === c.ip);
}
function nvrInfo(c) {
  const g = ipGroup(c);
  const n = g.length;
  const off = g.filter((x) => x.state === "offline").length;
  const stall = g.filter((x) => x.state === "stalled").length;
  const on = g.filter((x) => x.state === "online").length;
  const sessions = g.reduce((s, x) => s + camRt(x).sessions, 0);
  return {ip: c.ip, cams: g, n, off, stall, on, sessions,
          isNvr: n > 1,
          allDown: n > 1 && off === n,
          // Bitta registratorga 6 dan ortiq bir vaqtdagi sessiya —
          // ko'p DVR'larda chegara shu atrofda (o'lchov: 8 kanalli
          // Dahua 8 ta, Hikvision 6-8 ta). Bu aniq limit emas, belgi.
          crowded: sessions > 6};
}

/* ═════════ kirish ═════════ */
function showGate() { $("#gate").classList.remove("hidden"); $("#app").classList.remove("on"); }

/* Kirish so'rovi api() orqali KETMAYDI. api() 401 ni "sessiya yo'q" deb
   tushunib showGate() qiladi va serverning "Login yoki parol noto'g'ri"
   xabari yo'qolardi. Ustiga server brute-force himoyasi bor: bitta IP
   dan 5 xatodan keyin javob 1, 2, 4 … 30 soniya kechiktiriladi
   (api/auth.py). O'sha kutish davomida tugma hech narsa ko'rsatmasdi —
   "so'rov ketmadi" degan taassurot aynan shu edi. Nginx ortida
   X-Forwarded-For bo'lmasa hamma foydalanuvchi bitta IP hisoblanadi va
   birovning xatosi hammaga kutish bo'ladi.

   Shuning uchun: bir vaqtda bitta so'rov, tugma bloklanadi, 1,5 s dan
   keyin "server tekshiryapti" izohi, 40 s da taym-aut, xato bo'lsa
   serverning o'z xabari. Forma — Enter ikkala maydonda ham ishlaydi,
   parol menejerlari to'g'ri to'ldiradi. */
let loginBusy = false;
async function doLogin() {
  if (loginBusy) return;
  const u = $("#u").value.trim(), p = $("#p").value;
  $("#gerr").textContent = "";
  if (!u || !p) {
    $("#gerr").textContent = "Login va parolni kiriting";
    (!u ? $("#u") : $("#p")).focus();
    return;
  }
  loginBusy = true;
  const btn = $("#glogin");
  btn.disabled = true; btn.textContent = "Kirilyapti…";
  const slow = setTimeout(() => {
    $("#gwait").textContent = "Server tekshiryapti… Ko'p xato urinishdan keyin javob 30 soniyagacha kechikadi.";
  }, 1500);
  const ctl = new AbortController();
  const tmo = setTimeout(() => ctl.abort(), 40000);
  const t0 = performance.now();
  try {
    const res = await fetch("/api/v1/auth/login", {
      method: "POST", cache: "no-store", signal: ctl.signal,
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({username: u, password: p}),
    });
    noteApi(performance.now() - t0);
    if (!res.ok) {
      let detail = res.status + "-xato";
      try { detail = (await res.json()).detail || detail; } catch (e) {}
      if (res.status === 404) detail = "Kirish yuzasi yopiq — serverda ENABLE_UI=1 o'rnatilmagan";
      if (res.status >= 500) detail = `Server xatosi (${res.status}) — servis jurnalini ko'ring`;
      throw new Error(detail);
    }
    enter();
  } catch (e) {
    $("#gerr").textContent = e.name === "AbortError"
      ? "Server 40 soniya ichida javob bermadi — tarmoq yoki servis to'xtagan"
      : e.message === "Failed to fetch" ? "Serverga ulanib bo'lmadi — tarmoq yoki servis to'xtagan"
      : e.message;
    $("#p").focus(); $("#p").select();
  } finally {
    clearTimeout(slow); clearTimeout(tmo);
    $("#gwait").textContent = "";
    btn.disabled = false; btn.textContent = "Kirish";
    loginBusy = false;
  }
}
$("#gform").addEventListener("submit", (e) => { e.preventDefault(); doLogin(); });
$("#out").onclick = async () => {
  try { await api("/api/v1/auth/logout", {method: "POST"}); } catch (e) {}
  location.reload();
};

let entered = false;
/* Qayta kirish ham shu yerdan o'tadi: sessiya (12 soat) tugasa api() 401
   olib showGate() qiladi, foydalanuvchi parolni qayta kiritadi. Ilgari
   bu funksiya `entered` bo'lsa darhol qaytardi — kirish 200 bilan
   o'tsa ham oyna yopilmasdi, "so'rov ketmadi" degandek turardi. Endi
   oyna har safar yopiladi, fon tsikllari esa bir marta ishga tushadi. */
function enter() {
  $("#gate").classList.add("hidden");
  $("#app").classList.add("on");
  $("#p").value = "";
  if (entered) { loadCams(); loadStatus(); return; }
  entered = true;
  loadCams(); pollRuntime(); loadStatus(); loadStat(); startSSE();
  setInterval(loadCams, 15000);
  setInterval(pollRuntime, 3000);
  setInterval(loadStatus, 30000);
  // Uzilishlar agregati sekin o'zgaradi — daqiqada bir marta yetadi.
  setInterval(() => { if (S.page === "out") loadStat(); }, 60000);
  setInterval(() => { if (S.page === "res") drawSys(); }, 3000);
}
(async function boot() {
  try {
    const me = await api("/api/v1/auth/me");
    if (me.authenticated) { enter(); return; }
  } catch (e) {}
  showGate();
})();

/* ═════════ ma'lumot yuklash ═════════ */
async function loadCams() {
  try {
    const all = [];
    for (let offset = 0; ; offset += 500) {
      const page = await api(`/api/v1/admin/cameras?limit=500&offset=${offset}`);
      all.push(...page.cameras);
      if (all.length >= page.total || !page.cameras.length) break;
    }
    // snapshot_at faqat status endpointida bor — birlashtiramiz.
    try {
      const st = await api("/api/v1/cameras/status?all=1");
      const byId = new Map(st.cameras.map((c) => [c.id, c]));
      all.forEach((c) => {
        const s = byId.get(c.id);
        if (s) { c.snapshot_at = s.snapshot_at; c.state = s.state; }
      });
    } catch (e) {}
    S.cams = all;
    S.byId = new Map(all.map((c) => [c.id, c]));
    S.camsAt = Date.now();
    // Skan formasidagi "Hudud" uchun mavjud hududlar taklifi.
    const regs = [...new Set(all.map((c) => c.region).filter(Boolean))].sort();
    $("#regions").innerHTML = regs.map((r) => `<option value="${esc(r)}">`).join("");
    drawCams(); drawNav(); drawTopbar();
    if (S.page === "verdict") drawVerdict();
    if (S.page === "topo") drawTopo();
    if (S.page === "wall") drawWall(false);
    if (S.page === "diag" && S.curId != null) drawDiag();
  } catch (e) { /* 401 gate'ni o'zi ochadi */ }
}

async function pollRuntime() {
  if (!entered) return;
  try {
    const r = await api("/api/v1/admin/runtime");
    const now = performance.now();
    if (S.prevRt && r.mediamtx) {
      const dt = (now - S.prevRt.t) / 1000;
      if (dt > 0.5) {
        const rates = {};
        for (const [slug, p] of Object.entries(r.paths)) {
          const prev = S.prevRt.paths[slug];
          if (prev && p.bytes_received >= prev.bytes_received) {
            rates[slug] = (p.bytes_received - prev.bytes_received) * 8 / dt / 1e6;
          }
        }
        S.rates = rates;
      }
    }
    S.prevRt = {t: now, paths: r.paths};
    S.rt = r.paths;
    if (S.page === "cams") drawCams();
    if (S.page === "verdict") drawVerdict();
    if (S.page === "topo") drawTopo();
    if (S.page === "wall") updateWallFoot();
    if (S.page === "diag" && S.curId != null) { drawDiagCards(); drawSince(); }
    drawTopbar();
  } catch (e) {}
}

async function loadStatus() {
  if (!entered) return;
  try {
    S.status = await api("/api/v1/admin/status");
    S.health = await api("/health");
    S.nodes = (await api("/api/v1/admin/nodes")).nodes;
    // So'nggi uzilishlar — "online" shovqini kerak emas.
    try {
      S.recentEv = ((await api("/api/v1/admin/events?limit=60")).events || [])
        .filter((e) => e.kind !== "online");
    } catch (e) {}
    drawTopbar();
    if (S.page === "res") drawSys();
    if (S.page === "speed") drawSpeed();
    if (S.page === "topo") drawTopo();
    if (S.page === "verdict") drawVerdict();
  } catch (e) {}
}

/* 7 kunlik mavjudlik — kameralar jadvalidagi ustun. 5000 kamerada javob
   ~600 KB, shuning uchun faqat jadval ochilganda va daqiqada bir marta. */
async function loadUptime7() {
  if (Date.now() - S.up7At < 60000) return;
  S.up7At = Date.now();
  try {
    const r = await api("/api/v1/admin/uptime?hours=168&limit=5000");
    S.up7 = new Map((r.cameras || []).map((c) => [c.id, c]));
    if (S.page === "cams") drawCams();
  } catch (e) {}
}

/* ═════════ navigatsiya ═════════ */
function go(p) {
  S.page = p;
  $$(".tabs .pill").forEach((b) => b.classList.toggle("sel",
    b.dataset.p === p || (p === "diag" && b.dataset.p === "cams")));
  $$(".page").forEach((s) => s.classList.toggle("on", s.id === "p-" + p));
  if (p === "wall") drawWall(true); else stopWall();
  if (p !== "diag") closeLive();          // sahifadan chiqilsa video to'xtaydi
  if (p === "verdict") drawVerdict();
  if (p === "speed") { drawSpeed(); loadStatus(); }
  if (p === "topo") { drawTopo(); drawGroups(); loadGroups(); }
  if (p === "cams") { drawCams(); loadUptime7(); }
  if (p === "res") { drawSys(); loadStatus(); }
  if (p === "out") { drawStat(); loadStat(); }
  if (p === "ev") renderFeed();
  if (p === "cams") drawNav();
  window.scrollTo(0, 0);
}
$$(".tabs .pill").forEach((b) => (b.onclick = () => go(b.dataset.p)));
$("#dback").onclick = () => go("cams");
$$("[data-go]").forEach((b) => (b.onclick = () => go(b.dataset.go)));
$("#refresh").onclick = async () => {
  const b = $("#refresh");
  b.classList.add("spin");
  await Promise.all([loadCams(), loadStatus(), S.page === "out" ? loadStat() : null]);
  setTimeout(() => b.classList.remove("spin"), 400);
};
$("#vsort").onchange = () => { S.vsort = $("#vsort").value; drawVerdict(); };
$("#vrefresh").onclick = async () => { await loadCams(); toast("Ro'yxat yangilandi", `${S.cams.length} kamera`); };
$("#spweye").onclick = () => {
  const i = $("#spw"), show = i.type === "password";
  i.type = show ? "text" : "password";
  $("#spweye").innerHTML = ic(show ? "eye" : "eye-off");
};

function drawNav() {
  const prob = S.cams.filter((c) => isProb(c)).length;
  $("#nv").textContent = S.cams.length ? (prob || "✓") : "";
  $("#nv").className = "n" + (prob ? " alert" : "");
  $("#nc").textContent = S.cams.length || "";
  $("#nw").textContent = S.picked.size || "";
  $("#wseln").textContent = S.picked.size || "";
  const t = S.stat ? S.stat.total : 0;
  $("#nt").textContent = t ? t : "";
  $("#nt").className = "n" + (t ? " alert" : "");
}

/* ═════════ "xato qayerda?" — hukm dvijoki ═════════

   Hukm taxmin emas, dalillardan xulosa. Har qatlam uchun bitta o'lchov
   bor va u to'g'ridan ko'rsatiladi:
     Kamera        — health sweep (TCP 554), kamera holati
     Registrator   — shu IP dagi boshqa kanallar holati, faol sessiyalar
     Tugun         — MediaMTX'da yo'l bormi, bayt oqyaptimi (runtime)
     Chipta        — tomoshabin bor-yo'qligi (chipta amal qilganini
                     bildiradi: readers > 0 bo'lsa 401 bo'lmagan)
     Brauzer       — surat yoshi, o'girish, watchdog
   Qatlamlarning qay biri birinchi "qizil" bo'lsa — aybdor o'sha.     */
function verdictFor(c) {
  const rt = camRt(c), a = snapAge(c), nvr = nvrInfo(c);
  const port = c.port || 554;
  const step = (label, tag, kind, probe, note) => ({label, tag, kind, probe, note});
  const nvrLabel = nvr.isNvr ? `Registrator ${c.ip}` : "Registrator";
  const nvrIdle = step(nvrLabel, "yakka kamera", "idle", "—",
    "Bu IP da boshqa kanal yo'q — registrator dalili yo'q, kamera o'zi baholanadi.");
  const nvrStep = (k, tag, note) => nvr.isNvr ? step(nvrLabel, tag, k, `${nvr.n} kanal`, note) : nvrIdle;
  const nodeNm = nodeName(c);
  const note = S.note[c.id];

  if (c.state === "disabled" || c.enabled === false) {
    return {
      badge: "o'chirib qo'yilgan", kind: "idle",
      title: "Admin kamerani ataylab o'chirgan",
      body: "Oqim ham, surat ham berilmaydi; health tsikli tekshirmaydi. Bu nosozlik emas — yoqilsa hamma qatlam qayta ishga tushadi.",
      actLabel: "Yoqish", actKind: "enable", actNote: "oqim va surat qaytadan beriladi",
      chain: [
        step("Kamera · RTSP " + port, "tekshirilmaydi", "idle", "—", `${c.ip || "manzil yo'q"} — o'chirilgan kamera sweep'ga kirmaydi.`),
        nvrStep(nvr.allDown ? "bad" : "idle", nvr.allDown ? "javob yo'q" : `${nvr.on} tirik`, `${nvr.n} kanal · ${nvr.sessions} faol sessiya.`),
        step("Tugun · MediaMTX", "yo'l yo'q", "idle", nodeNm, "O'chirilgan kamera uchun yo'l yaratilmaydi."),
        step("Chipta", "berilmaydi", "idle", "auth", "Oqim so'rovi 403 bilan rad etiladi."),
        step("Brauzer", "—", "idle", "—", "Ko'rish mumkin emas."),
      ],
    };
  }

  if (c.state === "offline") {
    const down = nvr.allDown;
    const seen = sinceSec(c.last_seen);
    return {
      badge: down ? "registrator tomonida" : "kamera tomonida", kind: "bad",
      title: down
        ? `${c.ip}: barcha ${nvr.n} kanal birga yo'qolgan — muammo servisda emas`
        : nvr.isNvr ? "Registrator tirik, lekin bu kanal portga javob bermayapti"
        : "Kamera tarmoqdan javob bermayapti",
      body: down
        ? `TCP tekshiruv ${c.ip}:${port} ni ko'rmayapti va shu manzildagi qolgan kanallar ham offline. Bitta registrator ortidagi barcha kanal birga yo'qolsa — bu quvvat yoki magistral tarmoq. Servis, MediaMTX va chiptalar bu holatga aloqador emas.`
        : nvr.isNvr
        ? `Shu IP dagi ${nvr.on} kanal ishlayapti, ya'ni registrator va tarmoq joyida. Kanal registratorda o'chirilgan, RTSP yo'li o'zgargan yoki kamera kabeli uzilgan bo'lishi mumkin. "Qayta tekshirish" bosqichma-bosqich aytadi: tarmoq → login → yo'l.`
        : `TCP tekshiruv ${c.ip}:${port} portiga ulanolmadi. Kabel va kommutatorni ko'ring; sabab aniq bo'lmasa "Qayta tekshirish" bosqichma-bosqich aytadi (tarmoq / parol / yo'l). Surat ataylab to'silgan — eski kadr jonli bo'lib ko'rinmasin.`,
      actLabel: down ? "Registratorni qayta tekshirish" : "Kanalni qayta tekshirish", actKind: "probe",
      actNote: `${c.ip}:${port}` + (seen >= 0 ? ` · oxirgi javob ${age(seen)} oldin` : ""),
      chain: [
        step("Kamera · RTSP " + port, "javob yo'q", "bad", "TCP 60s",
          `${c.ip}:${port} portiga ulanish timeout bilan tugadi` + (seen >= 0 ? ` · oxirgi javob ${age(seen)} oldin.` : ".")),
        nvrStep(down ? "bad" : "ok", down ? "javob yo'q" : `${nvr.on}/${nvr.n} tirik`,
          down ? "Shu IP dagi barcha kanallar offline — registratorning o'zi yo'q."
               : `Shu IP dagi ${nvr.on} kanal online — registrator va tarmoq joyida, ayb kanalda.`),
        step("Tugun · MediaMTX", rt.ready ? "yo'l qotgan" : "yo'l yaratilmagan", "idle", nodeNm,
          rt.ready ? "Yo'l ro'yxatda qolgan, reconciler 30 s ichida tozalaydi."
                   : "Manba javob bermaganda yo'l ochilmaydi — bu kutilgan xatti-harakat, xato emas."),
        step("Chipta", "berilmaydi", "idle", "auth", "Offline kameraga /stream 409 qaytaradi — chipta yaratilmaydi."),
        step("Brauzer", "surat to'silgan", "warn", "404",
          "Surat ataylab 404 — eski JPEG jonli deb chalg'itmasin. Eski kadr `?stale=1` bilan ochiladi."),
      ],
    };
  }

  if (c.state === "stalled") {
    const crowded = nvr.crowded;
    return {
      badge: crowded ? "registrator tomonida" : "kamera tomonida", kind: "warn",
      title: crowded
        ? `${c.ip}: ${nvr.sessions} ta faol sessiya — registrator limiti ehtimoli`
        : "Ulanish bor, lekin kadr kelmayapti",
      body: crowded
        ? `Reconciler 30 soniya ichida bitta bayt ko'rmadi — oqim muzlagan. TCP tekshiruv buni ko'rmaydi: registrator portga javob beraveradi. Shu IP ga ${nvr.sessions} ta sessiya ochilgan; ko'p DVR'larda chegara 6–8. Sessiyalar to'lganda yangi kanal ulanmaydi yoki eskisi uzib qo'yiladi.`
        : `Reconciler baytlarni sanaydi va 30 soniya jimlikni "stalled" deb belgilaydi. Odatda kamera qayta yuklanayotganda yoki tarmoq uzilganda bo'ladi — reconciler o'zi tiklaydi. Tiklanmasa yo'lni qayta yaratish kerak.`,
      actLabel: "Oqimni qayta ulash", actKind: "reconnect",
      actNote: crowded ? "yo'l o'chirilib qayta yaratiladi — eski sessiya bo'shaydi" : "MediaMTX yo'lini o'chirib qayta yaratadi",
      chain: [
        step("Kamera · RTSP " + port, "port ochiq", "ok", "TCP 60s",
          `${c.ip}:${port} javob beryapti` + (c.codec ? ` · kodek ${c.codec}` : "") + (c.resolution ? ` · ${c.resolution}` : "") + "."),
        nvrStep(crowded ? "warn" : nvr.stall > 1 ? "warn" : "ok",
          crowded ? `${nvr.sessions} sessiya` : nvr.stall > 1 ? `${nvr.stall} kanal muzlagan` : "sog'lom",
          crowded ? `${nvr.n} kanal, ${nvr.sessions} faol sessiya — limitga yaqin yoki to'lgan.`
                  : nvr.stall > 1 ? `Shu IP da ${nvr.stall} kanal birga muzlagan — registrator yoki uning tarmog'i.`
                  : `${nvr.n} kanal · ${nvr.sessions} faol sessiya · ${nvr.on} online.`),
        step("Tugun · MediaMTX", "bayt kelmadi", "warn", `${nodeNm} · 30s`,
          `Yo'l ro'yxatda${rt.ready ? " va tayyor" : ""}, lekin oxirgi 30 soniyada bayt hisobi o'smadi (hozir ${rt.inMbps.toFixed(1)} Mbit/s).`),
        step("Chipta", rt.readers ? "amal qiladi" : "tomoshabin yo'q", rt.readers ? "ok" : "idle", "auth",
          rt.readers ? `${rt.readers} tomoshabin ulangan — 401 qaytmagan, himoya qatlami sabab emas.` : "Hozir hech kim ko'rmayapti — chipta so'ralmagan."),
        step("Brauzer", rt.readers ? "watchdog kutyapti" : "—", rt.readers ? "warn" : "idle", "12s",
          rt.readers ? "Pleyer kadrlar to'xtaganini sezadi va 12 soniyada qayta ulanadi." : "Ko'rish urinishi yo'q."),
      ],
    };
  }

  if (c.state === "unknown") {
    return {
      badge: "hali ma'lum emas", kind: "idle",
      title: "Birinchi tekshiruv navbatda",
      body: "Kamera bazaga qo'shilgan, lekin health tsikli (60 s) unga hali yetmagan. Holat aniqlanmaguncha ro'yxatda kulrang turadi — yoki hozir tekshiring.",
      actLabel: "Hozir tekshirish", actKind: "probe", actNote: "RTSP probe: tarmoq → login → kodek → o'lcham",
      chain: [
        step("Kamera · RTSP " + port, "tekshirilmagan", "idle", "—", `${c.ip || "manzil yo'q"}:${port} hali so'ralmagan.`),
        nvrStep(nvr.allDown ? "bad" : "idle", nvr.allDown ? "javob yo'q" : `${nvr.on} tirik`, `${nvr.n} kanal · ${nvr.on} online · ${nvr.off} offline.`),
        step("Tugun · MediaMTX", "yo'l yo'q", "idle", nodeNm, "Talab bo'yicha yaratiladi — oldindan ro'yxatga olinmaydi."),
        step("Chipta", "—", "idle", "auth", "Oqim so'ralmagan."),
        step("Brauzer", "—", "idle", "—", "Ko'rish urinishi bo'lmagan."),
      ],
    };
  }

  if (c.state === "online" && c.ip && !c.codec && c.source_type !== "manual") {
    const nvr2 = nvrInfo(c);
    return {
      badge: "kamera tomonida", kind: "warn",
      title: "Port ochiq, lekin RTSP oqim bermayapti",
      body: `${c.ip}:${port} TCP javob beryapti (health shuning uchun "online" deydi), lekin RTSP tekshiruvi kodek ololmadi — oqim aslida kelmayapti. Ko'pincha login/parol noto'g'ri yoki RTSP yo'l xato. "Qayta tekshirish" aniq bosqichni aytadi: tarmoq → login → yo'l.`,
      actLabel: "Qayta tekshirish", actKind: "probe",
      actNote: `${c.ip}:${port} · foydalanuvchi ${c.username || "admin"}`,
      chain: [
        step("Kamera · RTSP " + port, "oqim yo'q", "warn", "RTSP", `${c.ip}:${port} porti ochiq, lekin DESCRIBE kodek qaytarmadi — login/parol yoki yo'l (${esc(c.rtsp_path || "/")}) xato bo'lishi mumkin.`),
        nvrStep(nvr2.crowded ? "warn" : "ok", nvr2.crowded ? `${nvr2.sessions} sessiya` : `${nvr2.on} online`, `${nvr2.n} kanal · ${nvr2.sessions} faol sessiya${nvr2.crowded ? " — limitga yaqin" : ""}.`),
        step("Tugun · MediaMTX", "yo'l ochilmaydi", "warn", nodeNm, "Manba RTSP'ni bera olmaganda MediaMTX yo'lni tayyor qilolmaydi — brauzer HLS 400 / WHEP 500 oladi."),
        step("Chipta", "amal qiladi", "idle", "auth", "Chipta imzolanadi, lekin oqim bo'lmagani uchun foyda bermaydi."),
        step("Brauzer", "oqim ochilmaydi", "warn", "400/500", "Devorda ochilsa xato beradi — shuning uchun ommaviy ochishda bunday kameralar chetlab o'tiladi."),
      ],
    };
  }

  // online
  const snapStale = a >= 0 && a > 600 && c.ip;
  const snapNone = a < 0 && c.ip;
  const kind = snapStale ? "warn" : "ok";
  return {
    badge: snapStale ? "surat tomonida" : c.transcode ? "hammasi joyida · o'girish" : "hammasi joyida", kind,
    title: snapStale ? "Video ishlayapti, surat eskirgan" : "Zanjir toza — aralashish kerak emas",
    body: snapStale
      ? `Oqim normal, lekin oxirgi surat ${age(a)} oldin olingan — snapshot manbai (kameraning HTTP yuzasi) javob bermayotgan bo'lishi mumkin. Asosiy tizimdagi surat ham eskirgan bo'ladi.`
      : `Kameradan iste'molchigacha uzilish ko'rinmaydi.` +
        (rt.ready ? ` Yo'l tayyor, kirish ${rt.inMbps.toFixed(1)} Mbit/s, ${rt.readers} tomoshabin.` : " Hozir hech kim ko'rmayapti — yo'l talab bo'lganda ochiladi.") +
        (c.transcode ? ` Kodek ${c.codec} — brauzer uddalamasa FFmpeg H.264 ga o'giradi (~200-400 MB, NVENC sessiyasi).` : "") +
        (rt.warm ? " Yo'l issiq to'plamda — qayta ochilish bir soniyagacha." : ""),
    actLabel: snapStale ? "Suratni yangilash" : "Jonli ko'rish", actKind: snapStale ? "snap" : "view",
    actNote: snapStale ? "kameradan yangi JPEG so'raladi" : `tomoshabin ${rt.readers} · yo'l ${rt.ready ? "tayyor" : "kutmoqda"}`,
    chain: [
      step("Kamera · RTSP " + port, "sog'lom", "ok", "TCP 60s",
        `${c.ip || "tayyor oqim"}${c.codec ? " · " + c.codec : ""}${c.resolution ? " · " + c.resolution : ""}${c.fps ? " · " + c.fps + " fps" : ""}${c.always_on ? " · tez ochilish yoqilgan" : ""}.`),
      nvrStep(nvr.crowded ? "warn" : nvr.off ? "warn" : "ok",
        nvr.crowded ? `${nvr.sessions} sessiya` : nvr.off ? `${nvr.off} kanal offline` : "sog'lom",
        `${nvr.n} kanal · ${nvr.sessions} faol sessiya · ${nvr.on} online${nvr.off ? ` · ${nvr.off} offline` : ""}.`),
      step("Tugun · MediaMTX", rt.transcoding ? "o'girish ishlayapti" : rt.ready ? "xom uzatyapti" : "talab kutilmoqda",
        rt.transcoding ? "warn" : rt.ready ? "ok" : "idle", nodeNm,
        rt.transcoding ? `Brauzer ${c.codec} ni uddalamadi — FFmpeg H.264 ga o'giryapti (1 NVENC sessiyasi).`
          : rt.ready ? `Yo'l ${c.slug} tayyor, o'girish yo'q — eng arzon yo'l.` : "Yo'l ko'rish so'ralganda yaratiladi."),
      step("Chipta", rt.readers ? "amal qiladi" : "so'ralmagan", rt.readers ? "ok" : "idle", "auth",
        "Imzoli, 1 soatlik, faqat shu yo'lga bog'langan. MediaMTX har so'rovni backend'dan tekshirtiradi."),
      step("Brauzer", snapStale ? "surat eskirgan" : snapNone ? "surat hali yo'q" : "surat " + age(a),
        snapStale ? "warn" : "ok", "poster",
        snapStale ? `Oxirgi surat ${age(a)} oldin — snapshot tsikli bu kameraga yetmayapti.` : "Pleyer avval WebRTC'ni sinaydi, ishlamasa HLS'ga tushadi."),
    ],
  };
}

/* Hukm sahifasidagi amal. Har biri haqiqiy API chaqiruvi; natija
   S.note ga yoziladi va karta ostida ko'rinadi. */
async function vAct(kind, c) {
  if (S.busy) return;
  if (kind === "view") { openDiag(c.id); openLive(c); return; }
  S.busy = c.id;
  drawVerdict();
  const done = (msg, k) => {
    S.busy = null;
    S.note[c.id] = msg;
    drawVerdict();
    if (k) toast(c.name, msg, k);
  };
  try {
    if (kind === "probe") {
      const r = await api("/api/v1/admin/probe", {method: "POST", body: {
        ip: c.ip, port: c.port || 554, username: c.username || "",
        rtsp_path: c.rtsp_path || "/", camera_id: c.id}});
      probeResult = {...r, camera: c.id};
      pushEv(r.ok ? "amal" : "xato", `<b>${esc(c.name)}</b> probe · ${esc(r.message)}`, c.id);
      done(r.ok ? `Probe o'tdi: ${r.message}` : `Probe to'xtadi (${r.stage}): ${r.message}`);
      loadCams();
    } else if (kind === "reconnect") {
      // Yo'lni qayta yaratishning yagona yo'li — kamerani o'chirib
      // yoqish: MediaMTX yo'li olib tashlanadi, registratordagi
      // sessiya bo'shaydi, keyin yo'l qaytadan qo'shiladi.
      await api(`/api/v1/admin/cameras/${c.id}/enabled`, {method: "POST", body: {enabled: false}});
      await new Promise((r) => setTimeout(r, 1200));
      const r = await api(`/api/v1/admin/cameras/${c.id}/enabled`, {method: "POST", body: {enabled: true}});
      S.byId.set(c.id, r); S.cams = S.cams.map((x) => x.id === c.id ? r : x);
      pushEv("amal", `<b>${esc(c.name)}</b> yo'l qayta yaratildi`, c.id);
      done("Yo'l o'chirilib qayta yaratildi. Kadr kelsa reconciler 30 s ichida holatni online qiladi.");
    } else if (kind === "enable") {
      const r = await api(`/api/v1/admin/cameras/${c.id}/enabled`, {method: "POST", body: {enabled: true}});
      S.byId.set(c.id, r); S.cams = S.cams.map((x) => x.id === c.id ? r : x);
      pushEv("amal", `<b>${esc(c.name)}</b> yoqildi`, c.id);
      done("Yoqildi — health tsikli bir daqiqada holatni aniqlaydi.");
    } else if (kind === "snap") {
      const res = await fetch(`/api/v1/cameras/${c.id}/snapshot?t=${Date.now()}`, {cache: "no-store"});
      done(res.ok ? "Surat yangilandi — disk zaxirasiga yozildi."
        : `Surat olinmadi: ${res.status === 404 ? "manba javob bermadi" : res.status + "-xato"}.`);
      loadCams();
    } else done("Noma'lum amal.");
  } catch (e) { done("Xato: " + e.message, "bad"); }
}

/* Muammo qachondan beri — soniyada (-1: ma'lum emas). */
function suspectSec(c) {
  if (c.state === "offline") return sinceSec(c.last_seen);
  if (c.state === "stalled" || effState(c) === "nostream") return snapAge(c);
  return -1;
}
function suspectAge(c) { const s = suspectSec(c); return s >= 0 ? age(s) : "—"; }

const STEP_ICON = ["cam", "nvr", "server", "shield", "image"];
const STEP_GO = ["diag", "topo", "res", "res", "diag"];
function verdictAdvice(c, v) {
  const nvr = nvrInfo(c);
  if (c.state === "offline") return nvr.allDown
    ? "avval registrator quvvati va magistral tarmoqni tekshiring — barcha kanallar birga yo'qolgan, kamera aybdor emas."
    : "avval kamera quvvati, kabel va kommutator holatini tekshiring. Muammo davom etsa, tarmoq va parol sozlamalarini tekshiring.";
  if (c.state === "stalled") return "oqimni qayta ulang. Takrorlansa registratordagi sessiya limitini va kameraning GOP oralig'ini ko'ring.";
  if (c.state === "unknown") return "birinchi tekshiruv 60 soniya ichida o'tadi; kutmasdan «Hozir tekshirish» ni bosing.";
  if (c.state === "disabled" || c.enabled === false) return "yoqilgach health tsikli holatni bir daqiqada aniqlaydi.";
  return v.kind === "warn" ? "surat yangilanmasa kameraning HTTP yuzasi (snapshot) tekshirilsin — video bunga bog'liq emas."
    : "aralashish kerak emas. Ochilish sekin bo'lsa «Ochilish tezligi» sahifasini ko'ring.";
}

function drawVerdict() {
  if (S.page !== "verdict") return;
  const sort = S.vsort || "worst";
  const probs = S.cams.filter((c) => isProb(c)).sort((a, b) => {
    if (sort === "name") return String(a.name || "").localeCompare(String(b.name || ""));
    if (sort === "recent") {
      const sa = suspectSec(a), sb = suspectSec(b);
      return (sa < 0 ? 1e12 : sa) - (sb < 0 ? 1e12 : sb);
    }
    return (RANK[effState(a)] ?? 9) - (RANK[effState(b)] ?? 9) ||
           String(a.name || "").localeCompare(String(b.name || ""));
  });
  $("#vn").textContent = S.cams.length ? `${probs.length} ta` : "yuklanmoqda…";
  $("#vupd").textContent = S.camsAt ? new Date(S.camsAt).toTimeString().slice(0, 8) : "—";

  // Tanlov: muammoli ro'yxatda qolgan bo'lsa saqlanadi, aks holda eng yomoni.
  let cur = S.sel != null ? S.byId.get(S.sel) : null;
  if (!cur || (!isProb(cur) && probs.length)) cur = probs[0] || null;
  if (!cur && S.cams.length) cur = S.byId.get(S.sel) || null;

  $("#vsus").innerHTML = probs.length ? probs.slice(0, 40).map((c) => {
    const es = effState(c), k = stateKind(es);
    return `<button class="sus ${es}${cur && c.id === cur.id ? " sel" : ""}" data-id="${c.id}"
      style="box-shadow:inset 3px 0 0 ${DOT[c.state]}">
      <span class="row"><i class="dot" style="background:${DOT[es]}"></i><b>${esc(c.name)}</b>
        <span class="tag ${k === "bad" ? "bad" : k === "warn" ? "mid" : ""}">${esc(LBL[es] || es)}</span>
        <span class="age">${esc(suspectAge(c))}</span>${ic("chev-r", "sm")}</span>
      <span class="where">${ic("pin")}${esc(c.region || "hududsiz")}${c.ip ? " · " + esc(c.ip) : ""} · ${esc(nodeName(c))}</span>
    </button>`;
  }).join("") + (probs.length > 40
      ? `<div class="meta" style="padding:6px 4px">…yana ${probs.length - 40} ta — Kameralar jadvalida</div>` : "")
    : S.cams.length
      ? `<div class="tip ok" style="margin:0">${ic("check")}<span><b>Hammasi joyida.</b> ${S.cams.length} kamera ishlayapti.</span></div>`
      : `<div class="meta">yuklanmoqda…</div>`;
  $$("#vsus .sus").forEach((b) => (b.onclick = () => { S.sel = +b.dataset.id; drawVerdict(); }));

  if (!cur) {
    $("#vright").innerHTML = S.cams.length ? "" : `<div class="empty">Kameralar yuklanmoqda…</div>`;
    return;
  }
  S.sel = cur.id;
  const v = verdictFor(cur);
  const busy = S.busy === cur.id;
  const nt = S.note[cur.id];
  const sw = (S.health && S.health.health) || (S.status && S.status.health) || {};
  const swAt = sinceSec(sw.finished_at || sw.at);
  const since = suspectSec(cur);
  const startedLbl = since >= 0 ? `${age(since)} oldin boshlangan` : "";
  $("#vright").innerHTML = `
    <div class="card vcard tint-${v.kind}">
      <div class="badge-row"><span class="badge ${v.kind}"><i class="dot" style="background:${KDOT[v.kind]}"></i>${esc(v.badge)}</span>
        <span class="meta" style="color:var(--ink-3)">${esc(cur.name)}${cur.ip ? " · " + esc(cur.ip) : ""}</span>
        <span class="rt">${startedLbl ? `<span>${ic("clock")}${esc(startedLbl)}</span>` : ""}
          <span>${ic("monitor")}${esc(nodeName(cur))}</span></span></div>
      <span class="art" style="color:${KINK[v.kind]}">${ic(cur.state === "offline" ? "cam-off" : "cam")}</span>
      <p class="title">${esc(v.title)}</p>
      <p class="body">${esc(v.body)}</p>
      <div class="acts">
        ${busy ? `<span class="busy"><i></i>bajarilyapti…</span>`
               : `<button class="btn" id="vact">${ic("play", "sm")}${esc(v.actLabel)}</button>`}
        <span class="note">${esc(v.actNote)}</span>
        <button class="btn ghost" id="vdiag" style="margin-left:auto">${ic("ext", "sm")}Kamera sahifasi →</button>
      </div>
      ${nt && !busy ? `<p class="vnote">${esc(nt)}</p>` : ""}
    </div>
    <div class="card">
      <div class="card-h"><div class="ttl"><h3>Zanjir bo'ylab dalillar
          <span class="h3-sub">${swAt >= 0 ? `sweep ${age(swAt)} oldin` : "health 60s / reconciler 30s"}</span></h3></div>
        <span class="r ${swAt >= 0 && swAt < 120 ? "ok" : "warn"}"><i class="dot" style="background:${swAt >= 0 && swAt < 120 ? "var(--d-on)" : "var(--d-stall)"}"></i>${
          swAt >= 0 && swAt < 120 ? "Tekshiruv yakunlandi" : swAt >= 0 ? "Tekshiruv eskirgan" : "Tekshiruv kutilmoqda"}</span></div>
      <div class="chain">${v.chain.map((s, i) => `
        <button class="step tint-${s.kind} k-${s.kind}" data-go="${STEP_GO[i]}"><span class="num">${i + 1}</span>
          <span class="tile-i sm line">${ic(STEP_ICON[i])}</span>
          <div class="cnt"><div class="ln"><b>${esc(s.label)}</b>
            <span class="tag ${s.kind === "bad" ? "bad" : s.kind === "warn" ? "mid" : s.kind === "ok" ? "good" : ""}">${esc(s.tag)}</span></div>
          <div class="nt">${esc(s.note)}</div></div>
          <span class="chip pr">${esc(s.probe)}</span>${ic("chev-r", "sm chev")}</button>`).join("")}</div>
      <div class="tip ${v.kind}">${ic("bulb")}<span><b>Maslahat:</b> ${esc(verdictAdvice(cur, v))}</span></div>
    </div>`;
  const b = $("#vact");
  if (b) b.onclick = () => vAct(v.actKind, cur);
  $("#vdiag").onclick = () => openDiag(cur.id);
  $$("#vright .step").forEach((s) => (s.onclick = () => {
    const to = s.dataset.go;
    if (to === "diag") openDiag(cur.id); else go(to);
  }));
}

/* ═════════ ochilish tezligi ═════════

   Pleyer har ochilishni uch bo'lakka bo'lib o'lchaydi va serverga
   yuboradi (POST /metrics/open); /health transport kesimida p50/p95
   qaytaradi. Kamera kesimida bo'linish yo'q — server saqlamaydi, va bu
   to'g'ri: "sekin" degan savolga transport kesimi javob beradi.       */
const TR_LBL = {webrtc: "WebRTC", hls: "HLS", hls_fallback: "HLS · WebRTC yiqilgach"};
const TR_HINT = {webrtc: "signal_ms — WHEP so'rovi", hls: "signal bosqichi yo'q",
                 hls_fallback: "WebRTC urinishi vaqtga qo'shilgan"};

function drawSpeed() {
  if (S.page !== "speed") return;
  const om = (S.health && S.health.open_ms) || {};
  const tr = Object.entries(om).filter(([, v]) => v && v.n).sort((a, b) => b[1].n - a[1].n);
  const stat = (lbl, val, note, k, st) => `<div class="stat">
    <div class="lbl">${lbl}${ic("bars")}</div>
    <div class="val" style="color:${k === "ok" ? "var(--ink)" : KINK[k]}">${val}</div>
    <div class="foot"><span class="note">${note}</span>${st ? `<span class="stt ${k}"><i class="dot" style="background:${KDOT[k]}"></i>${st}</span>` : ""}</div></div>`;
  if (!tr.length) {
    $("#spstats").innerHTML =
      stat("p50 ochilish", "—", "hali o'lchov yo'q", "idle") + stat("p95 ochilish", "—", "hali o'lchov yo'q", "idle") +
      stat("keyframe kutish", "—", "frame_ms · kamera GOP", "idle") + stat("servis ulushi", "—", "stream_ms · backend + MediaMTX", "idle");
    $("#spbudget").innerHTML = `<div class="empty" style="margin-top:8px">O'lchov yo'q. Pleyer har ochilishda vaqtni yuboradi —
      kamerani jonli oching, raqamlar shu yerda paydo bo'ladi.<br>Server qayta ishga tushsa hisob noldan boshlanadi.</div>`;
    $("#spadv").innerHTML = `<div class="tip">${ic("info")}<span><b>Eslatma:</b> ochilish vaqti taxmin qilinmaydi — faqat o'lchanadi.</span></div>`;
    return;
  }
  const main = tr[0][1];
  const p = (t, f, q) => (t[f] && t[f][q]) || 0;
  const slow = 3000;
  const p50 = p(main, "total_ms", "p50"), p95 = p(main, "total_ms", "p95");
  const frame = p(main, "frame_ms", "p50"), stream = p(main, "stream_ms", "p50"), signal = p(main, "signal_ms", "p50");
  const n = tr.reduce((s, [, v]) => s + v.n, 0);
  const grade = (v, ok, warn) => v <= ok ? "ok" : v <= warn ? "warn" : "bad";
  const LBLK = {ok: "Yaxshi holat", warn: "Diqqat kerak", bad: "Ehtiyot chorasi"};
  const k50 = grade(p50, 1500, 3000), k95 = grade(p95, 3000, 6000), kf = grade(frame, 1000, 2500), ks = grade(stream, 200, 600);
  $("#spstats").innerHTML =
    stat("p50 ochilish", (p50 / 1000).toFixed(2) + "<u>s</u>", `${TR_LBL[tr[0][0]] || tr[0][0]} · ${n} o'lchov`, k50, LBLK[k50]) +
    stat("p95 ochilish", (p95 / 1000).toFixed(2) + "<u>s</u>", "eng yuqori 5%", k95, LBLK[k95]) +
    stat("keyframe kutish", frame + "<u>ms</u>", "o'rtacha frame_ms · kamera GOP", kf, LBLK[kf]) +
    stat("servis ulushi", stream + "<u>ms</u>", "stream_ms · backend + MediaMTX", ks, LBLK[ks]);

  const maxT = Math.max(1, ...tr.map(([, v]) => p(v, "total_ms", "p50")));
  $("#spbudget").innerHTML = tr.map(([k, v]) => {
    const t = p(v, "total_ms", "p50");
    const st = p(v, "stream_ms", "p50"), sg = p(v, "signal_ms", "p50"), fr = p(v, "frame_ms", "p50");
    const w = (x) => Math.round(x / maxT * 100);
    const seg = (x, col) => x ? `<i style="width:${w(x)}%;background:${col}">${w(x) > 11 ? x + " ms" : ""}</i>` : "";
    const kt = grade(t, slow / 2, slow);
    return `<div class="budget">
      <div class="nm"><span class="tile-i sm line">${ic(k === "webrtc" ? "cam" : "play")}</span>
        <div class="cnt"><b>${esc(TR_LBL[k] || k)}</b><span>${v.n} o'lchov · ${esc(TR_HINT[k] || "")}</span></div></div>
      <div><div class="bar">${seg(st, "#2563EB")}${seg(sg, "#059669")}${seg(fr, "#D97706")}</div>
        <div class="det">stream ${st} ms · signal ${sg} ms · frame ${fr} ms · p95 ${(p(v, "total_ms", "p95") / 1000).toFixed(2)}s</div></div>
      <div class="tot"><span>Jami</span><b style="color:${kt === "ok" ? "var(--ink)" : KINK[kt]}">${(t / 1000).toFixed(2)} s</b><small>(P50)</small></div>
    </div>`;
  }).join("");

  const tot = Math.max(1, stream + signal + frame);
  const big = frame >= stream && frame >= signal ? "frame" : signal >= stream ? "signal" : "stream";
  const kb = big === "frame" ? kf : big === "signal" ? grade(signal, 300, 1000) : ks;
  const head = big === "frame" ? `Ochilishning ${Math.round(frame / tot * 100)}% i keyframe kutishga ketyapti`
    : big === "signal" ? `Signalizatsiya ${signal} ms — bu tarmoq/proksi belgisi`
    : `Servis ulushi ${stream} ms — eng katta bo'lak backend/MediaMTX'da`;
  const text = big === "frame"
    ? `O'rtacha frame_ms ${frame} ms, servis ulushi esa ${stream} ms. Tezlikni backend'da emas, registratorlarda I Frame Interval'ni qisqartirib olish kerak — bepul va barcha kanalga ta'sir qiladi.`
    : big === "signal"
    ? `Servis ${stream} ms, keyframe ${frame} ms — sekinlik kamera va server orasidagi yo'lda. Uzoq kameralar uchun o'sha joyga alohida MediaMTX tuguni qo'ying: kamera trafigi lokal qoladi, magistralga faqat ko'rilayotgan oqim chiqadi.`
    : `MediaMTX'da ortiqcha yo'llar (pending_paths) yoki uzoq tugun API'si tekshirilsin. Resurs sahifasidagi "managed" soni tomoshabinlarga qarab o'sishi kerak, kameralarga qarab emas.`;
  const tips = big === "frame"
    ? ["Registratorda I Frame Interval'ni fps ning 1–2 baravariga tushiring.",
       "Sekin ochiladigan kameralarda «Tez ochilish» (always_on) ni yoqing — narxi ~200 MB xotira.",
       "H.265 kameralar brauzerda o'girilmasin — sub oqimni H.264 qiling."]
    : big === "signal"
    ? ["Uzoq hududlar uchun MediaMTX edge tuguni o'rnating.",
       "Kamera va server orasidagi tarmoq yo'lini tekshiring (RTT, yo'qotishlar).",
       "WHEP/proksi konfiguratsiyasini tekshiring, TLS kechikishini kamaytiring.",
       "Kamera GOP va keyframe oralig'ini sozlang (1–2 s)."]
    : ["MediaMTX'da ortiqcha yo'llarni (pending_paths) tozalang — Resurs sahifasida ko'rinadi.",
       "Uzoq tugun API'si sekin bo'lsa tugunni kameraga yaqin joylashtiring.",
       "Resurs sahifasida «managed» soni tomoshabinga qarab o'sishini tekshiring."];
  const fb = om.hls_fallback && om.hls_fallback.n;
  const note = (p95 > slow ? `P95 ochilish ${(p95 / 1000).toFixed(2)} s — eng yomon 5% holat sekin. ` : "Ochilish vaqtlari me'yorda. ") +
    (fb ? `WebRTC yiqilib HLS'ga tushgan ochilishlar: ${fb} — bu UDP yopiq muhit yoki kodek mos kelmasligi belgisi.` : "");
  $("#spadv").innerHTML = `<div class="card ${kb === "ok" ? "tint-ok" : kb === "warn" ? "tint-warn" : "tint-bad"}">
    <div class="two">
      <div><div class="adv-h ${kb}">${ic("target")}${kb === "ok" ? "Zanjir toza" : "Asosiy muammo aniqlandi"}</div>
        <div class="adv-t">${esc(head)}</div><p class="adv-p">${esc(text)}</p></div>
      <div><div class="adv-h">${ic("bulb")}Tavsiyalar</div>
        <ol class="numlist">${tips.map((t, i) => `<li><i>${i + 1}</i>${esc(t)}</li>`).join("")}</ol></div>
    </div>
    <div class="tip" style="margin-top:14px">${ic("info")}<span><b>Eslatma:</b> ${esc(note)}</span></div>
  </div>`;
}

/* ═════════ topologiya ═════════
   Tugun → registrator (IP) → kanallar. Registrator qatori bosilsa
   kameralar jadvali o'sha IP bo'yicha filtrlanadi.                   */
function drawTopo() {
  if (S.page !== "topo") return;
  const nodes = (S.nodes || []).length ? S.nodes
    : [{id: 1, name: "Asosiy", status: S.health ? (S.health.mediamtx ? "online" : "offline") : "unknown"}];
  const q = (S.tq || "").toLowerCase().trim();
  const KIND = {online: "ok", degraded: "warn", offline: "bad"};
  $("#tnodes").innerHTML = nodes.map((n) => {
    const cams = S.cams.filter((c) => (c.node_id || 1) === n.id);
    const kind = KIND[n.status] || "idle";
    const rtm = n.runtime || {};
    const ready = rtm.ready ?? n.ready ?? 0, readers = rtm.readers ?? n.readers ?? 0;
    const off = cams.filter((c) => c.state === "offline").length;
    const groups = new Map();
    cams.forEach((c) => {
      const k = c.ip || "tayyor oqim";
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(c);
    });
    const rows = [...groups.entries()].map(([ip, g]) => {
      const info = nvrInfo(g[0]);
      const goff = g.filter((x) => x.state === "offline").length;
      const stall = g.filter((x) => x.state === "stalled").length;
      const on = g.filter((x) => x.state === "online").length;
      const sess = g.reduce((s, x) => s + camRt(x).sessions, 0);
      const single = g.length === 1;
      const allDown = !single && goff === g.length && g[0].ip;
      const k = allDown ? "bad" : (stall || (goff && !single) || info.crowded) ? "warn" : goff ? "bad" : on ? "ok" : "idle";
      const tag = allDown ? "javob yo'q" : info.crowded ? "sessiya ko'p" : stall ? `${stall} kanal muzlagan`
        : goff ? (single ? "javob yo'q" : `${goff} kanal offline`) : on ? "sog'lom" : "tekshirilmagan";
      const note = allDown ? "Barcha kanallar birga yo'qolgan — quvvat yoki magistral."
        : goff && single ? "Yakka kamera javob bermayapti — kabel, quvvat yoki kameraning o'zi."
        : info.crowded ? `${sess} faol sessiya — ko'p DVR'larda chegara 6–8. Yangi ulanish uchun eski sessiya bo'shashi kerak.`
        : stall ? "Ulanish bor, bayt kelmayapti — reconciler kuzatyapti."
        : goff ? "Registrator tirik, ayrim kanal javob bermayapti — kanal o'chirilgan yoki kabel."
        : on ? `${on} online · ${sess} faol sessiya.` : "Birinchi tekshiruv navbatda.";
      const names = g.map((x) => x.name).join(" ");
      return {ip, g, k, tag, note, sess, on, single, names, rank: k === "bad" ? 0 : k === "warn" ? 1 : 2};
    }).sort((a, b) => a.rank - b.rank || b.g.length - a.g.length)
      .filter((r) => !q || r.ip.toLowerCase().includes(q) || r.names.toLowerCase().includes(q) ||
                     (r.single ? "kamera" : "registrator").includes(q));
    const shown = rows.slice(0, 60);
    const host = n.api_base || "http://127.0.0.1:9997";
    const nstat = (icon, k, v, sub, subk) => `<div class="nstat"><span class="tile-i sm ${k}">${ic(icon)}</span>
      <div class="cnt"><b>${v}</b><span>${sub[0]}</span>${sub[1] ? `<span class="sub ${subk || ""}">${sub[1]}</span>` : ""}</div></div>`;
    return `<div class="node">
      <div class="node-h">
        <div class="nm"><div class="ln"><i class="dot" style="background:${KDOT[kind]}"></i><b>${esc(n.name)}</b>
            <span class="st ${kind}">${esc(n.status || "—")}</span></div>
          <span class="m">kamera ${cams.length} · yo'l ${ready} · tomoshabin ${readers}${n.pending_paths ? ` · ortiqcha yo'l ${n.pending_paths}` : ""}</span>
          <span class="m"><a href="${esc(host)}" target="_blank" rel="noopener">${ic("ext", "sm")}${esc(host)}</a></span></div>
        <div class="nstats">
          ${nstat("monitor", "", cams.length, ["Jami kamera", off ? `<i class="dot sm" style="background:var(--d-off)"></i>${off} offline` : `<i class="dot sm" style="background:var(--d-on)"></i>hammasi tirik`], off ? "bad" : "ok")}
          ${nstat("topo", "", ready, ["Jami yo'l", n.stalled ? `<i class="dot sm" style="background:var(--d-stall)"></i>${n.stalled} muzlagan` : `<i class="dot sm" style="background:var(--d-on)"></i>bayt oqyapti`], n.stalled ? "warn" : "ok")}
          ${nstat("users", "", readers, ["Tomoshabin", `${readers ? readers + " faol sessiya" : "hozir ko'rilmayapti"}`], "")}
          <div class="nstat"><span class="tile-i sm">${ic("link")}</span><div class="cnt"><span>Onlayn manzil</span>
            <b class="mono">${esc(host.replace(/^https?:\/\//, ""))}</b><span>${n.api_base ? "tashqi tugun" : "ichki tarmoq"}</span></div></div>
        </div>
      </div>
      <div class="node-body">
        <div class="tbl-tools"><h3>Registratorlar va kameralar</h3><span class="chip">${rows.length} ta qurilma</span>
          ${nodes[0] === n ? `<label class="search-box">${ic("search")}<input data-tq placeholder="IP, nom yoki tur bo'yicha qidirish…" value="${esc(S.tq || "")}"></label>` : ""}</div>
        <div class="tbl tbl-scroll"><table class="ttab"><thead><tr><th>Holat</th><th>IP manzil / nom</th><th>Turi</th><th>Kanal</th>
          <th>Sessiya</th><th>Foydalanish</th><th>Holat / natija</th><th>Diagnostika</th><th></th></tr></thead>
        <tbody>${shown.map((r) => `<tr class="click" data-ip="${esc(r.ip)}">
          <td><i class="dot" style="background:${KDOT[r.k]}"></i></td>
          <td class="nm"><b>${esc(r.ip)}</b><span>${r.single ? esc(r.g[0].name) : `${r.g.length} kanal · registrator`}</span></td>
          <td><span class="ty"><span class="tile-i sm line">${ic(r.single ? "cam" : "nvr")}</span>${r.single ? "Kamera" : "Registrator"}</span></td>
          <td class="meta">${r.g.length} kanal</td>
          <td class="meta">sessiya ${r.sess}</td>
          <td><div class="use"><div class="bar-h"><i style="width:${Math.min(100, r.sess / 8 * 100)}%;background:${r.sess > 6 ? "var(--d-stall)" : "var(--d-on)"}"></i></div><span>${Math.min(100, Math.round(r.sess / 8 * 100))}%</span></div></td>
          <td><span class="tag ${r.k === "bad" ? "bad" : r.k === "warn" ? "mid" : r.k === "ok" ? "good" : ""}">${esc(r.tag)}</span></td>
          <td class="dg">${esc(r.note)}</td>
          <td class="act"><span class="btn ghost sm icon">${ic("chev-r", "sm")}</span></td>
        </tr>`).join("")}</tbody></table></div>
        ${rows.length > 60 ? `<div class="node-more">…yana ${rows.length - 60} qurilma — Kameralar jadvalida</div>` : ""}
        ${!rows.length ? `<div class="node-more">${q ? "Qidiruvga mos qurilma yo'q." : "Bu tugunga kamera biriktirilmagan."}</div>` : ""}
      </div>
    </div>`;
  }).join("");
  $$("#tnodes tr[data-ip]").forEach((b) => (b.onclick = () => {
    const ip = b.dataset.ip;
    $("#csearch").value = ip === "tayyor oqim" ? "" : ip;
    S.filt = "all";
    $$("#cfilt .pill").forEach((x) => x.classList.toggle("sel", x.dataset.f === "all"));
    go("cams");
  }));
  const inp = $("#tnodes [data-tq]");
  if (inp) {
    inp.oninput = () => { S.tq = inp.value; const pos = inp.selectionStart; drawTopo();
      const again = $("#tnodes [data-tq]"); if (again) { again.focus(); again.setSelectionRange(pos, pos); } };
  }
}

/* ═════════ kameralar jadvali ═════════ */
$$("#cfilt .pill").forEach((b) => (b.onclick = () => {
  S.filt = b.dataset.f; S.cpage = 1;
  $$("#cfilt .pill").forEach((x) => x.classList.toggle("sel", x === b));
  drawCams();
}));
$("#csearch").oninput = () => { S.cpage = 1; drawCams(); };
$$(".chead button[data-s]").forEach((th) => (th.onclick = () => {
  const k = th.dataset.s;
  S.sortD = S.sortK === k ? -S.sortD : 1; S.sortK = k; S.cpage = 1; drawCams();
}));

function up7(c) { return S.up7 ? S.up7.get(c.id) : null; }

function tableRows() {
  const q = $("#csearch").value.toLowerCase().trim();
  const key = (c) => {
    if (S.sortK === "state") return RANK[c.state] ?? 9;   // muammolilar tepada
    if (S.sortK === "inMbps") return camRt(c).inMbps;
    if (S.sortK === "readers") return camRt(c).readers;
    if (S.sortK === "snapAge") return snapAge(c);
    if (S.sortK === "uptime") { const u = up7(c); return u ? u.uptime_pct : 101; }
    if (S.sortK === "ip") return (c.ip || "") + " " + (c.region || "");
    return c[S.sortK] ?? "";
  };
  const match = (c) => S.filt === "all" ? true
    : S.filt === "prob" ? isProb(c) : S.filt === "nostream" ? effState(c) === "nostream" : c.state === S.filt;
  return S.cams.filter((c) =>
      match(c) &&
      (!q || (c.name || "").toLowerCase().includes(q) ||
        (c.ip || "").includes(q) || (c.external_id || "").toLowerCase().includes(q) ||
        (c.region || "").toLowerCase().includes(q)))
    .sort((a, b) => {
      const x = key(a), y = key(b);
      const d = (typeof x === "number" ? x - y
        : String(x).localeCompare(String(y))) * S.sortD;
      return d || String(a.name || "").localeCompare(String(b.name || ""));
    });
}

function drawCams() {
  if (S.page !== "cams") { drawNav(); return; }
  const rs = tableRows();
  const cnt = (s) => S.cams.filter((c) => effState(c) === s).length;
  const probN = S.cams.filter((c) => isProb(c)).length;
  const N = Math.max(1, S.cams.length);
  const seg = ["online", "nostream", "stalled", "offline", "unknown", "disabled"];
  const pct = (s) => (cnt(s) / N * 100);
  $("#csummary").innerHTML = S.cams.length ? `
    <div><span class="tile-i xl">${ic("cam", "lg")}</span><div class="big"><span>Jami kameralar</span><b>${S.cams.length}</b>
      <div class="d"><span class="ok">${ic("up", "sm")}${cnt("online")} online</span>
        ${cnt("offline") ? `<span class="bad">${ic("down", "sm")}${cnt("offline")} offline</span>` : ""}
        ${cnt("nostream") ? `<span class="warn">${cnt("nostream")} oqim yo'q</span>` : ""}
        ${cnt("stalled") ? `<span class="warn">${cnt("stalled")} stalled</span>` : ""}</div></div></div>
    <div class="mid"><div class="park-bar">${seg.map((s) => `<i style="width:${pct(s).toFixed(2)}%;background:${DOT[s]}"></i>`).join("")}</div>
      <div class="park-leg">${seg.filter((s) => cnt(s)).map((s) =>
        `<span><i style="background:${DOT[s]}"></i>${cnt(s)} ${s === "nostream" ? "oqim yo'q" : s} (${pct(s).toFixed(1)}%)</span>`).join("")}</div></div>
    <button class="prob ${probN ? "" : "ok"}" id="csumprob"><span class="tile-i xl ${probN ? "bad" : "ok"}">${ic(probN ? "alert" : "check", "lg")}</span>
      <div class="cnt"><span>Muammoli kameralar</span><b>${probN}</b><small>${probN ? "Diqqat talab qiladi" : "Hammasi ishlayapti"}</small></div>
      ${ic("chev-r", "chev")}</button>` : "";
  const sp = $("#csumprob");
  if (sp) sp.onclick = () => { S.filt = "prob"; $$("#cfilt .pill").forEach((x) => x.classList.toggle("sel", x.dataset.f === "prob")); drawCams(); };

  const per = S.cper, pages = Math.max(1, Math.ceil(rs.length / per));
  if (S.cpage > pages) S.cpage = pages;
  if (S.cpage < 1) S.cpage = 1;
  const pageRows = rs.slice((S.cpage - 1) * per, S.cpage * per);
  $("#ctb").innerHTML = pageRows.map((c) => {
    const rt = camRt(c), a = snapAge(c), u = up7(c);
    const es = effState(c), k = stateKind(es);
    const since = c.state === "offline" ? (sinceSec(c.last_seen) >= 0 ? age(sinceSec(c.last_seen)) + " dan beri" : "")
      : c.state === "stalled" ? (a >= 0 ? age(a) + " dan beri" : "")
      : c.state === "unknown" ? "yangi" : "";
    return `<button class="crow${S.picked.has(c.id) ? " pick" : ""}" data-id="${c.id}"
      style="box-shadow:inset 3px 0 0 ${es === "online" ? "transparent" : DOT[es]}">
      <span class="cbx">${S.picked.has(c.id) ? "✓" : ""}</span>
      <span class="nm"><i class="dot" style="background:${DOT[es]}"></i>
        <span class="cnt"><b>${esc(c.name)}</b>
          <span class="ln"><span class="tag ${k === "bad" ? "bad" : k === "warn" ? "mid" : k === "ok" ? "good" : ""}">${esc(LBL[es] || es)}</span>
            ${since ? `<span>${esc(since)}</span>` : ""}${c.external_id ? `<span class="mono">${esc(c.external_id)}</span>` : ""}</span></span></span>
      <span class="col"><span class="m1">${esc(c.ip || "tayyor oqim")}</span>
        <span class="m2">${esc(c.region || "hududsiz")} · ${esc(nodeName(c))}</span></span>
      <span class="col"><span class="m1">${c.codec ? esc(c.codec) + (c.sub_codec ? " · " + esc(c.sub_codec) : "") + (c.transcode ? " → H264" : "")
        : es === "nostream" ? '<span style="color:var(--amber)">kodek aniqlanmadi</span>' : "—"}</span>
        <span class="m2" style="color:${rt.ready ? "var(--green)" : "var(--mute)"}">yo'l: ${rt.ready ? "managed" : rt.warm ? "issiq" : "yo'q"}</span></span>
      <span class="right">${rt.inMbps ? rt.inMbps.toFixed(1) + " Mb/s" : "—"}</span>
      <span class="col">${u
        ? `<span class="m1" style="color:${upColor(u.uptime_pct)}">${u.uptime_pct}%</span>
           <span class="m2">${u.outages ? "— " + u.outages + " uzilish · " + durHM(u.offline_seconds) : "— uzilishsiz"}</span>`
        : `<span class="m1" style="color:var(--mute)">—</span>`}</span>
      <span class="right">${rt.readers || "—"}</span>
      <span class="right" style="color:var(--mute)">${c.state === "offline"
        ? '<span class="tag bad">yopiq</span>' : esc(age(a))}</span>
      <span class="acts"><span class="btn ghost sm">Batafsil ${ic("chev-r", "sm")}</span></span>
    </button>`;}).join("");
  $$("#ctb .crow").forEach((r) => {
    const id = +r.dataset.id;
    r.onclick = (e) => {
      if (e.target.closest(".cbx")) {
        S.picked.has(id) ? S.picked.delete(id) : S.picked.add(id);
        drawCams();
      } else openDiag(id);
    };
  });
  const totalIn = S.cams.reduce((s, c) => s + camRt(c).inMbps, 0);
  const from = rs.length ? (S.cpage - 1) * per + 1 : 0, to = Math.min(rs.length, S.cpage * per);
  $("#cfoot").innerHTML = rs.length
    ? `<span>${from}–${to} ko'rsatilyapti · ${rs.length} tadan${rs.length !== S.cams.length ? ` (jami ${S.cams.length})` : ""} · ${totalIn.toFixed(1)} Mbit/s kirish</span>
       <span class="pager"><span class="meta">Sahifada</span>
         <label class="select"><select id="cper">${[25, 50, 100].map((n) => `<option value="${n}"${n === per ? " selected" : ""}>${n}</option>`).join("")}</select>${ic("chev-d", "sm")}</label>
         <button class="btn ghost sm icon" id="cprev"${S.cpage <= 1 ? " disabled" : ""}>${ic("chev-r", "sm")}</button>
         <span class="meta">${S.cpage} / ${pages}</span>
         <button class="btn ghost sm icon" id="cnext"${S.cpage >= pages ? " disabled" : ""}>${ic("chev-r", "sm")}</button></span>` : "";
  const per_ = $("#cper"); if (per_) per_.onchange = () => { S.cper = +per_.value; S.cpage = 1; drawCams(); };
  const prev = $("#cprev"); if (prev) prev.onclick = () => { if (S.cpage > 1) { S.cpage--; drawCams(); window.scrollTo({top: 0, behavior: "smooth"}); } };
  const next = $("#cnext"); if (next) next.onclick = () => { if (S.cpage < pages) { S.cpage++; drawCams(); window.scrollTo({top: 0, behavior: "smooth"}); } };
  $("#cempty").innerHTML = rs.length ? "" : `<div class="empty">${
    S.filt === "prob" && S.cams.length
      ? `✓ Muammoli kamera yo'q — ${S.cams.length} kamera ishlayapti.<br>Hammasini ko'rish uchun «Hammasi» filtrini tanlang.`
      : "Bu filtrga mos kamera yo'q.<br>Filtrni kengaytiring yoki qidiruvni tozalang."}</div>`;
  $("#cbulk").innerHTML = S.picked.size ? `<div class="bulk">
    <b>${S.picked.size} ta</b> tanlandi
    <span class="meta">devorda sub oqim ochiladi</span>
    <button class="btn" style="margin-left:auto" id="bwall">${ic("grid", "sm")}Devorda ochish</button>
    <button class="btn ghost" id="bclr">Bekor</button></div>` : "";
  if (S.picked.size) {
    $("#bwall").onclick = () => { S.wallMode = "sel"; go("wall"); };
    $("#bclr").onclick = () => { S.picked.clear(); drawCams(); };
  }
  $$("#cfilt .pill").forEach((b) => {
    const f = b.dataset.f;
    b.querySelector(".n").textContent =
      f === "all" ? S.cams.length : f === "prob" ? probN : cnt(f);
  });
  $$(".chead button[data-s]").forEach((b) => {
    b.classList.toggle("sel", b.dataset.s === S.sortK);
    b.querySelector(".arrow").textContent = b.dataset.s === S.sortK ? (S.sortD === 1 ? "▲" : "▼") : "";
  });
  $("#csum").textContent = `${rs.length} ta ko'rsatilyapti · ${S.cams.length} dan · ${cnt("online")} online` +
    (probN ? ` · ${probN} muammoli` : "") + ` · ${totalIn.toFixed(1)} Mbit/s kirish`;
  drawNav();
}
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && !/INPUT|TEXTAREA/.test(e.target.tagName)) {
    e.preventDefault(); go("cams"); $("#csearch").focus();
  }
});

/* ═════════ video pleyer ═════════ */
const FAIL_MSG = "oqim ochilmadi";

/* Watchdog: oqim "ulangan" bo'lib turib qotib qolishi eng ko'p uchraydigan
   nosozlik, va uni na connectionState, na hls.js xatosi ko'rsatadi. Shuning
   uchun harakat o'lchanadi — WebRTC'da dekodlangan kadrlar, HLS'da
   currentTime.

   Chegara 6 soniya EDI va bu SOG'LOM oqimni uzardi. O'lchov (ikki xil
   Dahua kanali, kameradan TO'G'RIDAN o'qib, MediaMTX ham, relay ham
   ishtirok etmagan holda): ma'lumot 5-8 soniyalik portlashlar bilan
   keladi, orada 3-6 soniya BUTUNLAY jim. Ya'ni normal ishlayotgan
   kamerada ham dekodlangan kadrlar muntazam 3-6 soniya qimirlamaydi —
   6 soniyalik chegara aynan shunga tushib, pleyer ulanishni uzib qayta
   ochardi. Jurnalda bu bir soatda 98 marta "peer connection closed"
   bo'lib ko'rindi, tomoshabin uchun esa uzluksiz uzilish edi.

   12 soniya — o'lchangan eng uzun jimlikdan (6 s) ikki barobar katta.
   Haqiqatan o'lgan oqim baribir ushlanadi, ustiga serverda mustaqil
   nazorat bor: reconciler 20 soniyada `stalled` hodisasini yozadi. */
const WATCH_MS = 2000;
const WATCH_DEAD = 6;
const MAX_RETRY = 3;          // ketma-ket shuncha urinishdan keyin taslim
const RETRY_WINDOW = 60000;   // shuncha tinch turgandan keyin hisob yangilanadi
/* WebRTC jitter buferi nishoni (ms) — brauzer tasvirni ko'rsatishdan
   oldin shuncha ushlab turadi.

   Nolga majburlash (playoutDelayHint = 0) intuitiv, lekin xato: bufer
   nolda tursa uzoq tarmoqda kadr yetishmay tasvir uzuq bo'ladi. Lekin
   200 ms ham kam ekan — o'lchov (Edge, 10.30.x.x tarmog'idagi kamera,
   30 soniya):

       200 ms  — 7 marta qotish, jami 3,3 s (11 %), 29 kadr tashlandi,  6 PLI
      1000 ms  — 1 marta qotish, jami 0,3 s ( 1 %),  0 kadr tashlandi,  0 PLI

   Sababi: kamera kanalida paket yo'qolsa RTSP/TCP qayta yuborishni
   kutadi, oqim to'xtaydi va keyin to'p-to'p bo'lib quvib yetadi. Kichik
   bufer bu tebranishni yutolmaydi va brauzer aynan shuni ko'rsatadi.
   Bir soniyalik kechikish kuzatuv uchun sezilmaydi, qotish esa darhol
   ko'rinadi — shuning uchun silliqlik afzal ko'rilgan.

   Faqat Chromium'da bor; qolganida jimgina e'tiborsiz qoladi. */
const JITTER_MS = 1000;
const RENEW_MARGIN = 5 * 60000;   // chipta muddatidan shuncha oldin yangilanadi
/* Manzil eskirganda (401/404) qayta ochish. Chegara YO'Q — kutish
   oralig'i o'sadi. Sabab: ba'zi kameralar RTSP ulanishini muntazam
   uzadi (o'lchov: bitta qurilma 22-50 soniyada), MediaMTX manbani
   qayta ulaguncha esa HLS 401/404 qaytaradi. Uch marta urinib taslim
   bo'lgan pleyer o'sha kamerani BUTUNLAY yo'qotardi — tomoshabin qora
   katak ko'rardi va sahifani qayta yuklashi kerak edi.

   Kutish oralig'i BOSHIDA QISQA: o'lchov ko'rsatdiki manba odatda 3
   soniyada qaytadi (yo'l ready=False -> 3 s -> ready=True). Darhol
   uzun kutishga o'tilsa tomoshabin bekorga 8-16 soniya qora ekran
   ko'radi. Uzun oraliqlar faqat kamera haqiqatan o'lik bo'lganda
   kerak — o'shanda ham server bo'g'ilmasin. */
// Kutish kengayib boradi, lekin 8 soniyadan oshmaydi. Ilgari 15 s va
// 30 s bor edi: manba bir necha soniyada qaytadigan kamerada (o'lchov:
// uzilgandan 5-6 s keyin qaytadi) tomoshabin bekorga yarim daqiqa qora
// ekranga qarab turardi, chunki hisob faqat tasvir kelganda nolga
// qaytadi va uzuq-yuluq manbada u o'sib ketadi.
const REOPEN_BACKOFF = [1000, 1500, 2000, 3000, 5000, 8000];
const REOPEN_MAX_WAIT = 8000;

/* WebRTC bu muhitda umuman ishlamasa (UDP yopiq), har ochilishda 3,5 soniya
   bekorga kutmaslik uchun yiqilish eslab qolinadi va keyingi ochilishlar
   to'g'ridan HLS'dan boshlanadi.

   Muhimi: BITTA kamera yiqilishi (kodek mos emas, kanal band) butun
   panelni HLS'ga tushirmasligi kerak — shuning uchun bayroq faqat ikki
   XIL kamera yiqilganda qo'yiladi va birinchi muvaffaqiyatda darhol
   tozalanadi. sessionStorage: xotira yorliq bilan ketadi, kunlab
   osilib qolmaydi. */
const RTC_RETRY_MS = 5 * 60 * 1000;
const RTC_FAIL_STREAK = 2;
const _store = {
  get(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, v); } catch (e) {} },
  del(k) { try { sessionStorage.removeItem(k); } catch (e) {} },
};
let _rtcFailedAt = +_store.get("nigoh_rtc_fail") || 0;
const _rtcFailIds = new Set();

// Bitta kamera ketma-ket shuncha marta yiqilsa — SHU kamera uchun
// to'g'ridan HLS'ga o'tiladi. Nima uchun kerak: global bayroq ikki XIL
// kamera yiqilishini talab qiladi, ya'ni bitta kamerani kuzatib
// turgan odam uchun u hech qachon qo'yilmaydi va har qayta ulanishda
// WHEP bekorga sinaladi (o'lchov: har urinish ~3,5 s). Serverda UDP
// yopiq bo'lsa bu har uzilishda bekorga sarflangan vaqt.
// Global bayroqni qo'ymaymiz — sabab kameraga xos bo'lishi ham mumkin.
const RTC_CAM_FAIL_STREAK = 2;
const _rtcCamFails = new Map();      // kamera id -> {n, at}

function noteRtcFail(camId) {
  const rec = _rtcCamFails.get(camId) || {n: 0, at: 0};
  rec.n++;
  rec.at = Date.now();
  _rtcCamFails.set(camId, rec);
  _rtcFailIds.add(camId);
  if (_rtcFailIds.size < RTC_FAIL_STREAK) return;   // hali bitta kamera — muhit aybdor emas
  _rtcFailedAt = Date.now();
  _store.set("nigoh_rtc_fail", _rtcFailedAt);
}

function noteRtcOk(camId) {
  if (camId !== undefined) _rtcCamFails.delete(camId);
  _rtcFailIds.clear();
  if (!_rtcFailedAt) return;
  _rtcFailedAt = 0;
  _store.del("nigoh_rtc_fail");
}

/* Shu kamera uchun WebRTC'ni sinash ma'noga egami. */
function rtcWorthFor(camId) {
  const now = Date.now();
  if (now - _rtcFailedAt <= RTC_RETRY_MS) return false;      // muhit aybdor
  const rec = _rtcCamFails.get(camId);
  if (rec && rec.n >= RTC_CAM_FAIL_STREAK && now - rec.at <= RTC_RETRY_MS) {
    return false;                                            // shu kamerada ishlamayapti
  }
  return true;
}

/* Oldindan isitish: diagnostika sahifasi ochilganda playlist bir marta
   so'raladi — MediaMTX kameraga ulanib segment yig'a boshlaydi, keyframe
   so'rovi birinchi segmentni tezlashtiradi. Play bosilganda oqim tayyor
   turadi — ochilish 8-10 s dan 2-3 s ga tushadi. */
function warmStream(c) {
  if (!c || !c.ip || c.state === "offline" || c.state === "disabled") return;
  api(`/api/v1/admin/cameras/${c.id}/keyframe`, {method: "POST"}).catch(() => {});
  // Chipta so'rovi arzon: u faqat yo'lni MediaMTX'da sozlaydi va issiq
  // to'plamga qo'shadi — kameraga ULANMAYDI.
  //
  // Ilgari bu yerda pleylist ham so'ralardi (`fetch(u.stream_url)`), va
  // aynan o'sha kameraga qo'shimcha RTSP ulanish ochardi: MediaMTX
  // sourceOnDemand yo'lini pleylist so'rovi bilan tortishni boshlaydi.
  // Kamera esa bizdan ochilgan bir vaqtdagi sessiyalar sonini
  // ko'tarmaydi — o'lchov (kamera 10.30.11.65, keepalive'siz sinov):
  //
  //     servis to'xtagan  — 84, 84, 85 s (barqaror)
  //     servis ishlaganda — 3, 16, 28, 35, 54, 86 s (tarqoq)
  //
  // Ya'ni ortiqcha ulanish tomoshaning o'zini uzadi. Diagnostika
  // sahifasini ochish uchun kameraga ulanishning ma'nosi yo'q: play
  // bosilganda pleyer baribir ulanadi, keyframe so'rovi esa birinchi
  // kadrni tezlashtiradi va u kameraning HTTP yuzasi orqali ketadi.
  api(`/api/v1/cameras/${c.id}/stream?hevc=${HEVC_OK ? 1 : 0}`).catch(() => {});
}

function createPlayer(video, msgEl) {
  const p = {video, msgEl, hls: null, pc: null, token: 0, mode: "",
             cam: null, quality: "", watch: null, retries: 0, lastRetry: 0,
             renew: null, reopens: 0, reopenTimer: null};

  p.stopWatch = () => { if (p.watch) { clearInterval(p.watch); p.watch = null; } };
  p.stopRenew = () => { if (p.renew) { clearTimeout(p.renew); p.renew = null; } };

  /* Chiptani yangilash uchun qayta ochish. p.retry() dan farqi: bu
     nosozlik EMAS, shuning uchun urinishlar hisobiga kirmaydi va
     "taslim bo'lish" chegarasiga yaqinlashtirmaydi. */
  p.reopen = (why) => {
    if (!p.cam) return;
    const wait = REOPEN_BACKOFF[Math.min(p.reopens, REOPEN_BACKOFF.length - 1)]
                 || REOPEN_MAX_WAIT;
    p.reopens++;
    const sec = Math.round(wait / 1000);
    console.log(`[pleyer] ${why} — ${sec} s dan keyin qayta ochiladi `
                + `(urinish ${p.reopens})`);
    msgEl.textContent = p.reopens <= 3 ? "qayta ulanmoqda…"
                        : `kamera javob bermayapti — qayta urinilmoqda (${p.reopens})`;
    // Eski oqim tozalanadi, lekin token oshirilmaydi — kutish davomida
    // kelgan kech javoblar o'z-o'zidan e'tiborsiz qoladi.
    if (p.hls) { p.hls.destroy(); p.hls = null; }
    if (p.pc) { p.pc.close(); p.pc = null; }
    p.stopWatch();
    p.stopRenew();
    if (p.reopenTimer) clearTimeout(p.reopenTimer);
    p.reopenTimer = setTimeout(() => {
      p.reopenTimer = null;
      if (p.cam) p.open(p.cam, p.quality);
    }, wait);
  };

  /* Chipta bir soat yashaydi, HLS esa playlistni cheksiz so'rayveradi.
     Muddat tugashidan RENEW_MARGIN oldin oqim jimgina qayta ochiladi —
     aks holda uzoq tomoshada birdan 401 boshlanardi. Muddat manzilning
     o'zida: token = "<epoch>.<imzo>". */
  p.scheduleRenew = (url) => {
    p.stopRenew();
    const m = /[?&]token=(\d{9,})\./.exec(url || "");
    if (!m) return;
    const left = (+m[1] * 1000) - Date.now() - RENEW_MARGIN;
    if (left <= 0 || left > 24 * 3600 * 1000) return;
    p.renew = setTimeout(() => p.reopen("chipta muddati tugayapti"), left);
  };

  p.stop = () => {
    p.token++;
    p.stopWatch();
    p.stopRenew();
    if (p.reopenTimer) { clearTimeout(p.reopenTimer); p.reopenTimer = null; }
    if (p.hls) { p.hls.destroy(); p.hls = null; }
    if (p.pc) { p.pc.close(); p.pc = null; }
    video.pause();
    video.removeAttribute("src");
    video.srcObject = null;
    video.load();
  };

  /* Watchdog yoki uzilish aniqlagan qayta ulanish. p.open() token'ni
     oshiradi, shuning uchun eski oqimning kech kelgan javoblari o'z-o'zidan
     e'tiborsiz qoladi. Kamera haqiqatan o'lik bo'lsa cheksiz aylanmaslik
     uchun urinishlar sanaladi — muvaffaqiyatli "playing" hisobni nolga
     qaytaradi. */
  p.retry = (why) => {
    if (!p.cam) return;
    const now = Date.now();
    // Bir daqiqa tinch ishlagandan keyingi uzilish — yangi voqea, eski
    // hisob bilan bog'lanmasin (aks holda bir marta taslim bo'lgan
    // pleyer soatlab qayta urinmay qoladi).
    if (now - p.lastRetry > RETRY_WINDOW) p.retries = 0;
    p.lastRetry = now;
    if (p.retries >= MAX_RETRY) {
      p.stop();
      msgEl.textContent = FAIL_MSG;
      console.log(`[pleyer] ${why} — ${MAX_RETRY} urinish natija bermadi, to'xtatildi`);
      return;
    }
    p.retries++;
    console.log(`[pleyer] ${why} — qayta ulanmoqda (${p.retries}/${MAX_RETRY})`);
    p.open(p.cam, p.quality);
    msgEl.textContent = "qayta ulanmoqda…";
  };

  p.open = (cam, quality) => {
    p.stop();
    p.cam = cam;
    p.quality = quality || "";
    const my = ++p.token;
    const stale = () => p.token !== my;
    msgEl.textContent = "ulanmoqda…";

    /* Ochilish vaqti bosqichlarga bo'lib o'lchanadi — "sekin" degan
       shikoyatga javob berish uchun bitta raqam yetmaydi. t0 bosildi,
       tStream manzil keldi, tSignal WHEP javobi keldi, birinchi kadr esa
       "playing" hodisasida. Server p50/p95 ni /health da ko'rsatadi. */
    const t0 = performance.now();
    let tStream = 0, tSignal = 0, transport = "";


    const report = () => {
      if (!tStream || (cam && cam._urls)) return;   // mozaika o'lchovga kirmaydi
      const now = performance.now();
      api("/api/v1/metrics/open", {method: "POST", body: {
        camera_id: cam.id, mode: p.mode || "", transport: transport || "hls",
        stream_ms: Math.round(tStream - t0),
        signal_ms: tSignal ? Math.round(tSignal - tStream) : 0,
        frame_ms: Math.round(now - (tSignal || tStream)),
        total_ms: Math.round(now - t0),
      }}).catch(() => {});
    };

    // Devor mozaikasi: manzil oldindan tayyor (/api/v1/walls dan), kamera
    // yo'q — /stream so'ralmaydi, o'sha oqim to'g'ridan ochiladi.
    if (cam && cam._urls) {
      tStream = performance.now();
      p.mode = cam._urls.mode || "direct";
      p.scheduleRenew(cam._urls.stream_url);
      attach(cam._urls, stale, () => { if (!stale()) msgEl.textContent = FAIL_MSG; });
      return;
    }

    api(`/api/v1/cameras/${cam.id}/stream?hevc=${HEVC_OK ? 1 : 0}` +
        (quality ? `&quality=${quality}` : ""))
      .then((urls) => {
        if (stale()) return;
        tStream = performance.now();
        p.mode = urls.mode;
        p.scheduleRenew(urls.stream_url);
        const onFail = urls.mode === "sub"
          ? () => { if (!stale()) p.open(cam, ""); }        // sub yiqilsa asosiy
          : () => { if (!stale()) msgEl.textContent = FAIL_MSG; };
        attach(urls, stale, onFail);
      })
      .catch((e) => { if (!stale()) msgEl.textContent = e.message; });

    function attach(urls, staleFn, onFail) {
      video.addEventListener("playing", () => {
        if (staleFn()) return;
        msgEl.textContent = "";
        p.retries = 0;              // tasvir keldi — urinishlar hisobi tozalanadi
        p.reopens = 0;
        report();                   // birinchi kadr keldi — o'lchov to'liq
      }, {once: true});
      const rtcWorth = rtcWorthFor(cam.id);
      if (urls.webrtc_url && rtcWorth) {
        playWebRtc(urls.webrtc_url, staleFn).catch((e) => {
          if (staleFn()) return;
          // Manzil eskirgan (chipta o'lgan yoki yo'l yo'qolgan) — bu
          // WebRTC nosozligi EMAS. Buni "WebRTC ishlamaydi" deb belgilash
          // butun panelni keraksiz HLS'ga o'tkazib yuborardi.
          const st = e && e.status;
          if (st === 401 || st === 403 || st === 404) {
            p.reopen(`WHEP ${st} — oqim hozir mavjud emas`);
            return;
          }
          noteRtcFail(cam.id);
          playHls(urls.stream_url, staleFn, onFail);
        });
        return;
      }
      if (!urls.stream_url) { msgEl.textContent = "manzil yo'q"; return; }
      playHls(urls.stream_url, staleFn, onFail);
    }

    async function playWebRtc(whepUrl, staleFn) {
      transport = "webrtc";
      const pc = new RTCPeerConnection({iceServers: []});
      p.pc = pc;
      pc.addTransceiver("video", {direction: "recvonly"});
      pc.ontrack = (e) => {
        if (staleFn()) return;
        // Jitter buferi. Nolga majburlash (playoutDelayHint = 0) intuitiv,
        // lekin xato: bufer doim nolda tursa uzoq tarmoqda kadr yetishmay
        // tasvir uzuq-yuluq bo'ladi. Kichik, lekin nolmas nishon — RTP
        // qayta yuborishga vaqt qoladi, kechikish esa sezilmaydi.
        // Faqat Chromium'da bor; qolganida jimgina e'tiborsiz qoladi.
        try { e.receiver.jitterBufferTarget = JITTER_MS; } catch (err) {}
        video.srcObject = e.streams[0];
        video.play().catch(() => {});
      };
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await new Promise((resolve) => {
        if (pc.iceGatheringState === "complete") return resolve();
        const done = () => { pc.removeEventListener("icegatheringstatechange", check); resolve(); };
        const check = () => { if (pc.iceGatheringState === "complete") done(); };
        pc.addEventListener("icegatheringstatechange", check);
        setTimeout(done, 900);
      });
      const res = await fetch(whepUrl, {method: "POST",
        headers: {"Content-Type": "application/sdp"}, body: pc.localDescription.sdp});
      if (!res.ok) {
        pc.close();
        const err = new Error("WHEP " + res.status);
        err.status = res.status;      // 404 — yo'l yo'q, WebRTC'ning aybi emas
        throw err;
      }
      const answer = await res.text();
      tSignal = performance.now();          // signalizatsiya tugadi
      if (staleFn()) { pc.close(); return; }
      await pc.setRemoteDescription({type: "answer", sdp: answer});
      await new Promise((resolve, reject) => {
        // srcObject'ga qarab bo'lmaydi: track hodisasi ICE ulanmasidan
        // oldin ham keladi (signal bosqichida). Faqat haqiqiy ulanish
        // (connectionState) hisob — aks holda UDP yopiq muhitda qora
        // ekranda qotib, HLS'ga tushmasdik.
        // ICE odatda 1-2 soniyada ulanadi; ulanmasa kutish HLS'ni
        // kechiktiradi xolos — 3,5 soniya yetarli.
        const timer = setTimeout(() => {
          if (pc.connectionState === "connected") resolve();
          else { pc.close(); reject(new Error("WebRTC jim")); }
        }, 3500);
        pc.addEventListener("connectionstatechange", () => {
          if (pc.connectionState === "connected") { clearTimeout(timer); resolve(); }
          if (pc.connectionState === "failed") { clearTimeout(timer); pc.close();
            reject(new Error("WebRTC uzildi")); }
        });
      });

      // Ulandi. Yuqoridagi Promise ALLAQACHON hal bo'lgan — undagi
      // tinglovchining reject'i endi hech narsa qilmaydi, ya'ni bundan
      // keyingi uzilishlar ishlov ko'rmay qolardi. Doimiy tinglovchi
      // aynan shu bo'shliqni yopadi.
      noteRtcOk(cam.id);
      let dropped = null;
      pc.addEventListener("connectionstatechange", () => {
        if (staleFn() || pc !== p.pc) return;
        const state = pc.connectionState;
        if (state === "connected") {
          clearTimeout(dropped); dropped = null;
          msgEl.textContent = "";
          return;
        }
        if (state === "failed" || state === "closed") {
          clearTimeout(dropped);
          p.retry(`WebRTC ${state}`);
          return;
        }
        if (state === "disconnected" && !dropped) {
          // "disconnected" ko'pincha o'zi tiklanadi (ICE qayta tekshiruvi)
          // va 10-15 soniya osilib turadi — shuncha kutmaymiz, 3 soniya.
          msgEl.textContent = "aloqa uzildi…";
          dropped = setTimeout(() => {
            if (!staleFn() && pc === p.pc) p.retry("WebRTC disconnected");
          }, 3000);
        }
      });
      armWebRtcWatch(pc, staleFn);
    }

    /* Kadr hisoblagichi. MediaMTX sessiyani yopganda ham brauzer buni bir
       necha soniya sezmaydi (bluenviron/mediamtx#4525, #2758), kamera
       muzlaganda esa umuman sezmaydi — connectionState "connected" bo'lib
       qolaveradi. framesDecoded yolg'on gapirmaydi. */
    function armWebRtcWatch(pc, staleFn) {
      let prev = -1, still = 0;
      p.stopWatch();
      p.watch = setInterval(() => {
        if (staleFn() || pc !== p.pc) { p.stopWatch(); return; }
        pc.getStats().then((stats) => {
          let frames = null;
          stats.forEach((s) => {
            if (s.type === "inbound-rtp" && s.kind === "video" &&
                typeof s.framesDecoded === "number") frames = s.framesDecoded;
          });
          if (frames === null) return;              // statistika hali yo'q
          if (frames === prev) still++; else { still = 0; prev = frames; }
          if (still >= WATCH_DEAD) { p.stopWatch(); p.retry("kadrlar to'xtadi"); }
        }).catch(() => {});
      }, WATCH_MS);
    }

    /* HLS uchun xuddi shu vazifa: o'ynayotgan videoda currentTime o'smasa
       oqim qotgan.

       Shart `readyState < 2` EMAS, `currentTime <= 0`. Nima uchun: bufer
       bo'shaganda readyState 2 dan 1 ga (HAVE_METADATA) tushadi — ya'ni
       aynan QOTGAN holatda. Eski shart bilan kuzatuvchi shunda butunlay
       chiqib ketardi, hisob yurmasdi va qotgan oqim hech qachon
       aniqlanmasdi: tasvir abadiy muzlab turardi. `currentTime <= 0` esa
       ochilish paytidagi soxta uzilishdan xuddi shunday himoya qiladi
       (ijro boshlanmaguncha 0), lekin bir marta ketgandan keyin muzlashni
       ko'rmay qolmaydi. */
    function armHlsWatch(staleFn) {
      let prev = -1, still = 0;
      p.stopWatch();
      p.watch = setInterval(() => {
        if (staleFn()) { p.stopWatch(); return; }
        if (video.paused || video.currentTime <= 0) return;
        const now = video.currentTime;
        if (Math.abs(now - prev) < 0.05) still++; else { still = 0; prev = now; }
        if (still >= WATCH_DEAD) { p.stopWatch(); p.retry("HLS qotdi"); }
      }, WATCH_MS);
    }

    function playHls(url, staleFn, onFail) {
      // HLS'ning signal bosqichi yo'q. WebRTC yiqilib bu yerga tushgan
      // bo'lsak alohida belgilanadi — u holda kutishga WebRTC'ning
      // muvaffaqiyatsiz urinishi ham qo'shilgan, va bu statistikada
      // toza HLS bilan aralashib ketmasligi kerak.
      transport = transport === "webrtc" ? "hls_fallback" : "hls";
      tSignal = 0;
      if (!url) { msgEl.textContent = FAIL_MSG; return; }
      // WebRTC muvaffaqiyatsiz tugab HLS'ga tushganda uning o'lik oqimi
      // videoda qolib ketadi; srcObject har doim src'dan ustun bo'lgani
      // uchun tozalanmasa MediaSource ochilmaydi — abadiy qora ekran.
      if (p.pc) { p.pc.close(); p.pc = null; }
      video.srcObject = null;
      video.removeAttribute("src");
      video.load();
      const isHls = url.includes(".m3u8");
      if (isHls && window.Hls && Hls.isSupported()) {
        // liveSync 2 segment (~4 s) — jonli chetiga 1 segment yaqin
        // turishdan barqarorroq: tarmoq titrasa ham qotmaydi. Bufer 12 s —
        // qisqa uzilishlarni yutib yuboradi.
        // lowLatencyMode O'CHIQ: server oddiy fMP4 beradi (mediamtx.yml da
        // hlsVariant: fmp4, LL-HLS emas), part'lar umuman yo'q — rejimni
        // yoqish faqat keraksiz kutish va noto'g'ri jonli chekka hisobiga
        // olib keladi.
        // maxBufferLength 12 emas, 30: manba kanali siqilganda kamera RTP
        // paketlarini tashlaydi, kalit kadr yo'qoladi va MediaMTX segment
        // uzunligini cho'zadi — serverda o'lchandi:
        //     [RTSP source] 1753 RTP packets lost
        //     [muxer] segment duration changed from 2s to 10s
        // 12 s bufer bunday segmentni BITTA ham sig'dira olmaydi, ya'ni
        // har cho'zilishda bufer bo'shab bufferStalledError beradi. 30 s
        // uch-to'rtta segmentni ushlaydi. Kechikishga ta'sir qilmaydi:
        // jonli chekka `liveSyncDurationCount` bilan belgilanadi, bu esa
        // faqat oldindan yig'ish chegarasi (pleylistda 7 segment turadi —
        // mediamtx.yml, hlsSegmentCount).
        const hls = new Hls({lowLatencyMode: false, maxBufferLength: 30,
          backBufferLength: 8, liveSyncDurationCount: 2,
          maxLiveSyncPlaybackRate: 1.1,
          manifestLoadingTimeOut: 25000,
          // Worker blob/eval bilan yaratiladi — qattiq CSP (masalan,
          // oldindagi proxy qo'shgani) uni bloklasa hls.js jim qotadi:
          // playlist aylanadi, segment so'ralmaydi. Worker'siz rejim
          // sekinmas va CSP'ga befarq.
          enableWorker: false});
        p.hls = hls;
        // Pleyer jurnali — konsolda [hls] bilan boshlanadi. Video chiqmasa
        // qaysi bosqichda qotganini shu yozuvlar aytadi.
        hls.on(Hls.Events.MEDIA_ATTACHED, () =>
          console.log("[hls] videoga ulandi (MediaSource ochildi)"));
        hls.on(Hls.Events.MANIFEST_PARSED, (_, d) =>
          console.log(`[hls] manifest o'qildi: ${d.levels.length} daraja`));
        hls.on(Hls.Events.LEVEL_LOADED, (_, d) =>
          console.log(`[hls] playlist: ${d.details.fragments.length} segment,`
            + ` jonli=${d.details.live}, boshi=${d.details.startSN}`));
        hls.on(Hls.Events.FRAG_LOADING, (_, d) =>
          console.log(`[hls] segment so'ralmoqda: №${d.frag.sn}`));
        hls.on(Hls.Events.FRAG_BUFFERED, (_, d) =>
          console.log(`[hls] segment buferda: №${d.frag.sn}`));
        /* Umumiy xato jurnali. bufferStalledError bu yerda ATAYLAB
           chetlab o'tiladi: uni pastdagi maxsus ishlovchi hal qiladi va
           nima qilganini o'zi yozadi ("bufer teshigi — sakradik" yoki
           "master qayta yuklanmoqda"). Ikkinchi marta yozilsa konsolda
           yolg'on signal chiqadi — xato hal qilingan bo'lsa ham
           "XATO: bufferStalledError" ko'rinib turadi va tuzatish
           ishlamayotgandek tuyuladi (aynan shu chalkashlik bo'lgan). */
        hls.on(Hls.Events.ERROR, (_, d) => {
          if (d.details === Hls.ErrorDetails.BUFFER_STALLED_ERROR) return;
          console.log(`[hls] XATO: ${d.details} fatal=${d.fatal}`);
        });
        video.addEventListener("playing", () => console.log("[hls] video ketdi"),
          {once: true});
        hls.loadSource(url);
        hls.attachMedia(video);
        video.play().catch((e) => console.log("[hls] play rad etildi:", e.name));
        // Fatal xato — darhol taslim bo'lish EMAS. hls.js xatolarning
        // ko'pini o'zi tiklay oladi; avval destroy() qilinsa bufer bir
        // marta to'xtaganda ham oqim butunlay o'lardi. Faqat tiklash ikki
        // marta natija bermagandan keyin boshqa yo'l (sub -> asosiy yoki
        // xato xabari) qidiriladi.
        let netFails = 0, mediaFails = 0, authReloads = 0, authJump = false;
        let stalls = 0;

        /* Buferning holatini jurnal uchun matnga aylantiradi. Qotib
           qolishni boshqa hech narsa tushuntirib bermaydi: teshik
           qayerda, currentTime qayerda — faqat shu ko'rinadi. */
        function bufInfo() {
          try {
            const b = video.buffered;
            let s = "";
            for (let i = 0; i < b.length; i++) {
              s += `[${b.start(i).toFixed(1)}-${b.end(i).toFixed(1)}]`;
            }
            return `t=${video.currentTime.toFixed(1)} rs=${video.readyState}`
                   + ` bufer=${s || "bo'sh"}`;
          } catch (e) { return "bufer o'qilmadi"; }
        }

        /* Buferdagi TESHIKdan o'tib ketadi.

           `video.buffered` — bir necha alohida oraliq bo'lishi mumkin,
           va aynan shu yerda oldingi urinishim xato edi: faqat tashqi
           konvert (start(0)…end(oxirgi)) tekshirilardi. currentTime ikki
           oraliq ORASIDAGI teshikda turganda konvert ichida bo'ladi,
           ya'ni "hammasi joyida" deb hisoblanardi — funksiya false
           qaytarardi va tasvir qotib qolardi.

           Teshik ikki sababdan paydo bo'ladi:
             * muxer qaytadan yaratilganda vaqt o'qi noldan boshlanadi va
               currentTime hamma oraliqdan tashqarida qoladi;
             * kamera kadr tashlaganda (kanal siqilgan) segment yetib
               kelmaydi va o'rtada bo'shliq qoladi.

           Uch holat farqlanadi:
             1) currentTime biror oraliq ichida, oldida joy bor -> teshik
                yo'q, false;
             2) currentTime teshikda yoki o'qdan tashqarida -> oldindagi
                eng yaqin oraliq boshiga (yo'q bo'lsa jonli chekkaga)
                sakraymiz, true;
             3) currentTime oxirgi oraliqning chekkasida -> bu teshik
                emas, bufer BO'SHAGAN. Sakrash foydasiz, false qaytaramiz
                va yuqoridagi eskalatsiya (master qayta yuklash) ishlaydi. */
        function jumpOverHole() {
          try {
            const b = video.buffered;
            if (!b.length) return false;
            const t = video.currentTime;
            let inside = false, next = -1;
            for (let i = 0; i < b.length; i++) {
              // Oraliq ichidami — oxiridan 0,2 s berida bo'lishi kerak,
              // aks holda "chekkada turish" ham "ichida" bo'lib chiqadi.
              if (t >= b.start(i) - 0.1 && t < b.end(i) - 0.2) inside = true;
              if (b.start(i) > t + 0.1 && next < 0) next = b.start(i);
            }
            if (inside) return false;                       // 1-holat
            if (next >= 0) {                                // 2-holat: teshik
              video.currentTime = next + 0.05;
              video.play().catch(() => {});
              return true;
            }
            const oxiri = b.end(b.length - 1);
            if (Math.abs(t - oxiri) > 0.5) {                // 2-holat: yangi o'q
              video.currentTime = oxiri;
              video.play().catch(() => {});
              return true;
            }
            return false;                                   // 3-holat: bo'shagan
          } catch (e) {
            return false;   // bufer hali o'qilmasa — play o'zi tiklaydi
          }
        }

        // Segment buferga tushdi — demak oqim tiklandi, hisoblarni nolga
        // qaytaramiz (uzoq tomoshada chegara bekorga tugamasin).
        //
        // Master qayta yuklangandan keyin esa alohida ish bor: bufer
        // NOLdan boshlanadi, shuning uchun birinchi segment kelganda
        // jonli chekkaga sakraymiz va ijroni qayta boshlaymiz.
        hls.on(Hls.Events.FRAG_BUFFERED, () => {
          authReloads = 0;
          stalls = 0;
          if (!authJump) return;
          authJump = false;
          jumpOverHole();
          video.play().catch(() => {});
        });
        hls.on(Hls.Events.ERROR, (_, d) => {
          if (staleFn()) return;
          /* Bufer to'xtashi fatal deb belgilanmaydi, lekin ekranni aynan
             shu qotiradi.

             Ilgari bu yerda `hls.startLoad()` chaqirilardi. U YORDAM
             BERMAYDI: to'xtashning sababi yuklash to'xtagani emas,
             buferda TESHIK borligi (`jumpOverHole` izohi). Yuklash esa
             allaqachon ketayotgan bo'ladi, ya'ni chaqiruv faqat hls.js'ning
             o'z tiklanishini (nudge) uzib qo'yadi. Natijada xato
             takrorlanaveradi — ishlab chiqarish konsolida ko'rindi:

                 [hls] XATO: bufferStalledError fatal=false   (qayta-qayta)

             va currentTime 6 soniya siljimagach kuzatuvchi (`armHlsWatch`,
             WATCH_DEAD=3) butun pleyerni yopib ochadi — tomoshabin uchun
             qora ekran. Bizda manba har 60 soniyada uziladi (shlyuz RTSP
             ulanishini uzadi, sabab tarmoqda), ya'ni bu daqiqada bir marta
             takrorlanardi.

             To'g'ri javob uch qadamli:
               1) teshik bo'lsa — jonli chekkaga sakraymiz (asl sabab shu);
               2) teshik bo'lmasa — hls.js o'zi turtib ko'rsin, xalal
                  bermaymiz (u buni bir necha marta uzluksiz sinaydi);
               3) to'xtash baribir qaytarsa — master pleylistni qayta
                  olamiz. Bu 401 dagi bilan bir xil yo'l va o'lchov bilan
                  tasdiqlangan: master qayta so'ralganda muxer qaytadan
                  yaratilgan bo'lsa ham oqim tiklanadi. */
          if (d.details === Hls.ErrorDetails.BUFFER_STALLED_ERROR) {
            const holat = bufInfo();
            if (jumpOverHole()) {
              console.log(`[hls] bufer teshigi — o'tib ketdik (${holat})`);
              stalls = 0;
              return;
            }
            // Teshik yo'q, ya'ni bufer bo'shagan. Bir-ikki marta hls.js
            // o'zi turtib ko'rsin; keyin master qayta yuklanadi.
            if (++stalls <= 2) {
              console.log(`[hls] bufer bo'shadi ${stalls}/2 — `
                          + `hls.js tiklashi kutilmoqda (${holat})`);
              return;
            }
            stalls = 0;
            console.log(`[hls] bufer qayta-qayta to'xtadi — `
                        + `master qayta yuklanmoqda (${holat})`);
            authJump = true;
            hls.loadSource(url);
            hls.startLoad(-1);              // -1 = jonli chekkadan
            return;
          }
          /* 401/403 — chipta o'lgan; 404 — yo'l MediaMTX'da yo'q.
             Uchalasida ham o'sha manzilni qayta yuklash befoyda, chunki
             muammo manzilning ichida. Yagona yechim — /stream ni qayta
             chaqirish: u yangi chipta beradi VA yo'lni qayta yaratadi
             (ensure_path).

             Ikkalasi ham normal holat, nosozlik emas:
               * chipta backend qayta ishga tushganda o'ladi (oqim
                 sessiyalari xotirada yashaydi);
               * yo'l MediaMTX qayta ko'tarilganda yo'qoladi — yo'llar
                 talab bo'yicha yaratiladi va faylda saqlanmaydi. */
          const code = d.response && d.response.code;
          /* 401/403 — bu chipta o'lgani EMAS. MediaMTX HLS uchun ruxsatni
             sessiya darajasida eslab qoladi va har segment uchun backend'ni
             qayta so'ramaydi; o'sha sessiya eskirganda esa 401 qaytaradi.
             Ishlab chiqarishda o'lchandi (negoh.das-uty.uz, manbasi
             mutlaqo barqaror kamerada ham ~60 soniyada takrorlanadi):

                 aynan shu variant manzili qayta   -> 401
                 YANGI chipta bilan o'sha manzil   -> 401
                 master pleylist qayta olindi      -> 200

             Ya'ni yangi chipta so'rashning foydasi yo'q — master'ni qayta
             yuklash kerak, u MediaMTX'da sessiyani qaytadan ochadi.
             Pleyerni butunlay yopib ochish esa qimmat: yangi chipta,
             avval WebRTC urinishi (u yiqilsa 404 va kutish), keyin HLS —
             tomoshabin uchun bu bir necha soniyalik qora ekran va
             kengayib boradigan kutish (1 s, 2 s, ... 15 s). */
          if ((code === 401 || code === 403) && ++authReloads <= 3) {
            // Birinchi urinish arzon: o'sha chipta bilan master qayta
            // o'qiladi. Agar yetmasa — YANGI chipta so'raladi: MediaMTX
            // bola pleylistiga chiptani so'rovdan ko'chiradi, ya'ni eski
            // chipta bilan master har safar AYNAN O'SHA bola manzilini
            // qaytaradi va bir xil so'rov bekorga takrorlanadi (o'lchov:
            // v1.20.0 da 401 shu bilan yo'qolmaydi).
            console.log(`[hls] ${code} — ruxsat sessiyasi eskirgan, `
                        + `${authReloads === 1 ? "master qayta yuklanmoqda"
                                               : "yangi chipta olinmoqda"} `
                        + `(${authReloads}/3)`);
            authJump = true;
            if (authReloads === 1) {
              hls.loadSource(url);
              hls.startLoad(-1);          // -1 = jonli chekkadan
              return;
            }
            api(`/api/v1/cameras/${cam.id}/stream?hevc=${HEVC_OK ? 1 : 0}`
                + (p.quality ? `&quality=${p.quality}` : ""))
              .then((u) => {
                if (staleFn() || !u.stream_url) return;
                p.scheduleRenew(u.stream_url);
                hls.loadSource(u.stream_url);
                hls.startLoad(-1);        // -1 = jonli chekkadan
              })
              .catch(() => { if (!staleFn()) p.reopen("chipta yangilandi"); });
            return;
          }
          if (code === 401 || code === 403 || code === 404) {
            console.log(`[hls] ${code} — oqim hozir mavjud emas `
                        + `(chipta eskirgan yoki manba uzilgan)`);
            hls.destroy(); p.hls = null; p.stopWatch();
            if (!staleFn()) p.reopen("chipta yangilandi");
            return;
          }
          if (!d.fatal) return;
          if (d.type === Hls.ErrorTypes.NETWORK_ERROR && ++netFails <= 2) {
            console.log(`[hls] tarmoq xatosi ${netFails}/2 — qayta yuklanmoqda`);
            hls.startLoad();
            return;
          }
          if (d.type === Hls.ErrorTypes.MEDIA_ERROR && ++mediaFails <= 2) {
            console.log(`[hls] media xatosi ${mediaFails}/2 — tiklanmoqda`);
            if (mediaFails === 2) hls.swapAudioCodec();
            hls.recoverMediaError();
            return;
          }
          hls.destroy(); p.hls = null;
          p.stopWatch();
          if (onFail) onFail(); else msgEl.textContent = FAIL_MSG;
        });
        armHlsWatch(staleFn);
      } else if (isHls && video.canPlayType("application/vnd.apple.mpegurl")) {
        video.src = url;
        video.onerror = () => { if (!staleFn()) msgEl.textContent = FAIL_MSG; };
        video.play().catch(() => {});
        armHlsWatch(staleFn);        // Safari/native: qotishni o'zi aytmaydi
      } else {
        video.src = url;
        video.onerror = () => { if (!staleFn()) msgEl.textContent = FAIL_MSG; };
        video.play().catch(() => {});
        armHlsWatch(staleFn);        // Safari/native: qotishni o'zi aytmaydi
      }
    }
  };
  return p;
}


/* ═════════ topologiya: uzilishlar reytingi (guruh kesimida) ═════════

   /admin/uptime dan oziqlanadi — hisob serverda, events jadvalidan.
   5000 kamerani brauzerga tortib guruhlashning ma'nosi yo'q: guruh
   javobi 2-11 KB, kamera kesimidagi to'liq ro'yxat esa ~900 KB.      */

const HOURS_LBL = {24: "24 soat", 168: "7 kun", 720: "30 kun"};

async function loadGroups() {
  try {
    const r = await api(`/api/v1/admin/uptime?hours=${S.gHours}&group_by=${S.gBy}`);
    S.groups = r.groups || [];
  } catch (e) { S.groups = []; toast("Reyting olinmadi", e.message, "bad"); }
  drawGroups();
}

function drawGroups() {
  if (S.page !== "topo") return;
  const gs = S.groups;
  if (gs === null) { $("#gsum").textContent = "yuklanmoqda…"; return; }
  const outages = gs.reduce((a, g) => a + g.outages, 0);
  $("#gsum").textContent = `${gs.length} ta guruh · ${outages} uzilish · ${HOURS_LBL[S.gHours]}`;

  $("#gtb").innerHTML = gs.map((g, i) => {
    const n = Math.max(1, g.cameras);
    const w = (v) => (100 * v / n).toFixed(2) + "%";
    // Hududsiz kameralar alohida guruh — ular xaritada ham, hisobotda
    // ham yo'qoladi, shuning uchun qizil bilan belgilanadi.
    const orphan = g.key === "belgilanmagan";
    return `<tr class="click" data-k="${esc(g.key)}">
      <td class="meta">${i + 1}</td>
      <td class="name" style="color:${orphan ? "var(--red)" : "var(--ink)"}">${esc(g.key)}</td>
      <td class="meta">${g.cameras}</td>
      <td><div class="bar-h" style="height:8px">
        <i style="width:${w(g.online)};background:var(--d-on)"></i>
        <i style="width:${w(g.offline)};background:var(--d-off)"></i>
        <i style="width:${w(g.unknown + g.disabled)};background:var(--d-unk)"></i>
      </div></td>
      <td class="meta" style="color:${upColor(g.uptime_pct)}">${g.uptime_pct}%</td>
      <td class="meta" style="color:${g.outages ? "var(--amber)" : "var(--mute)"}">${g.outages || "—"}</td>
      <td class="meta">${durHM(g.offline_seconds)}</td>
    </tr>`;
  }).join("");

  // Guruhga bosilsa kameralar ro'yxati o'sha guruh bo'yicha filtrlanadi —
  // "aybdorni topdim, endi qaysi kamera" degan keyingi qadam.
  $$("#gtb tr").forEach((tr) => {
    tr.onclick = () => {
      const key = tr.dataset.k;
      $("#csearch").value = key === "belgilanmagan" ? "" : key;
      S.filt = "prob";
      $$("#cfilt .pill").forEach((b) => b.classList.toggle("sel", b.dataset.f === "prob"));
      go("cams");
    };
  });
  $("#gempty").innerHTML = gs.length ? "" :
    `<div class="empty" style="margin-top:12px">Bu davrda ma'lumot yo'q.<br>Hodisalar 30 kun saqlanadi.</div>`;
}

$$("#gby .pill").forEach((b) => (b.onclick = () => {
  S.gBy = b.dataset.g;
  $$("#gby .pill").forEach((x) => x.classList.toggle("sel", x === b));
  S.groups = null; drawGroups(); loadGroups();
}));
$$("#ghours .pill").forEach((b) => (b.onclick = () => {
  S.gHours = +b.dataset.h;
  $$("#ghours .pill").forEach((x) => x.classList.toggle("sel", x === b));
  S.groups = null; drawGroups(); loadGroups();
}));

/* ═════════ uzilishlar ═════════
   Sutka bo'ylab taqsimot, sabab kesimi (hodisa turlari bo'yicha) va
   eng ishonchsiz kameralar. Sabab kesimi hodisalar jurnalidan
   hisoblanadi: offline — registrator/kamera javob bermadi, stalled —
   oqim muzladi, mediamtx — tugun qayta ko'tarildi.                   */
const CAUSE = [
  ["offline", "Registrator / kamera javob bermadi (offline)", "var(--d-off)"],
  ["stalled", "Oqim muzladi (stalled · bayt yo'q)", "var(--d-stall)"],
  ["mediamtx", "MediaMTX qayta ko'tarildi", "var(--d-unk)"],
];

async function loadStat() {
  // Zona mijozdan boradi: hodisalar bazada UTC'da, "cho'qqi 08:00 da"
  // degan xulosa esa faqat mahalliy vaqtda ma'noga ega.
  const tz = -new Date().getTimezoneOffset();
  try {
    const [hist, worst, ev, fleet] = await Promise.all([
      api(`/api/v1/admin/outages/hourly?hours=${S.tHours}&tz_offset_minutes=${tz}`),
      api(`/api/v1/admin/uptime?hours=${S.tHours}&limit=8`),
      api("/api/v1/admin/events?limit=500"),
      api(`/api/v1/admin/uptime?hours=${S.tHours}&group_by=node`),
    ]);
    S.stat = hist;
    S.worst = worst.cameras || [];
    // Park bo'yicha yig'indi: o'chiq vaqt va o'rtacha mavjudlik (tugun kesimi eng kichik javob).
    const g = fleet.groups || [];
    const offSec = g.reduce((a, x) => a + x.offline_seconds, 0), camsN = g.reduce((a, x) => a + x.cameras, 0);
    const span = Math.max(1, camsN * S.tHours * 3600);
    S.fleet = {offline: offSec, cameras: camsN, uptime: Math.round(1000 * (span - offSec) / span) / 10};
    const since = Date.now() - S.tHours * 3600 * 1000;
    const evs = (ev.events || []);
    const inWin = evs.filter((e) => utc(e.ts).getTime() >= since);
    const counts = {};
    inWin.forEach((e) => { counts[e.kind] = (counts[e.kind] || 0) + 1; });
    S.causes = {counts, n: inWin.length,
                // 500 ta yozuv davrga sig'magan bo'lsa — kesim to'liq emas.
                truncated: evs.length >= 500 && inWin.length === evs.length};
  } catch (e) { S.stat = null; toast("Uzilishlar olinmadi", e.message, "bad"); }
  drawStat();
  drawNav();
}

const CAUSE_ICON = {offline: ["monitor", "bad"], stalled: ["warn", "warn"], mediamtx: ["server", "violet"], other: ["more", "idle"]};
function drawStat() {
  if (S.page !== "out") return;
  const st = S.stat;
  $("#tsum").textContent = st
    ? `${st.total} uzilish · ${HOURS_LBL[S.tHours]} · hodisalar jadvalidan` : "Kameralar va kanallar bo'yicha uzilishlar statistikasi";
  if (!st) { $("#thist").innerHTML = ""; $("#tchips").innerHTML = ""; return; }

  const max = Math.max(1, ...st.hourly);
  const peak = st.peak || {from_hour: 0, to_hour: 0, outages: 0};
  // Cho'qqi oyna sutka aylanasidan o'tishi mumkin (23:00–02:00).
  const span = (peak.to_hour - peak.from_hour + 24) % 24 || 3;
  const inPeak = (h) => (h - peak.from_hour + 24) % 24 < span;
  const peakLbl = peak.outages ? `${pad2(peak.from_hour)}:00–${pad2(peak.to_hour)}:00` : "";
  $("#tsub").textContent = `Har soatdagi uzilish soni · ${S.cams.length} ta kamera` + (peakLbl ? ` · eng band vaqt ${peakLbl}` : " · cho'qqi yo'q");
  const f = S.fleet;
  const chip = (icon, k, v, l) => `<div class="schip tint-${k}"><span class="tile-i sm ${k}">${ic(icon)}</span>
    <div class="cnt"><b>${v}</b><span>${l}</span></div></div>`;
  $("#tchips").innerHTML =
    chip("alert", st.total ? "bad" : "ok", st.total, "Jami uzilish") +
    chip("clock", "blue", f ? durHM(f.offline) : "—", "Umumiy o'chiq vaqt") +
    chip("shield-ok", f && f.uptime >= 99 ? "ok" : f && f.uptime >= 95 ? "warn" : "bad", f ? f.uptime + "%" : "—", "O'rtacha mavjudlik");
  $("#thisty").innerHTML = `<span>${max}</span><span>${Math.round(max / 2)}</span><span>0</span>`;
  $("#thist").innerHTML = st.hourly.map((v, h) => `<i class="hbar"
    style="height:${v ? Math.max(3, Math.round(v / max * 100)) : 2}%;
           background:${!v ? "var(--line-2)" : v === max ? "#EF4444" : inPeak(h) && peak.outages ? "#FCA5A5" : "#BFDBFE"}"
    title="${pad2(h)}:00–${pad2((h + 1) % 24)}:00 — ${v} uzilish"></i>`).join("");
  const cz = S.causes;
  const peakN = st.hourly.filter((_, h) => inPeak(h)).reduce((a, b) => a + b, 0);
  const main = cz && cz.n ? CAUSE.map(([k, l]) => [l, cz.counts[k] || 0]).sort((a, b) => b[1] - a[1])[0] : null;
  $("#ttip").innerHTML = st.total ? `<div class="tip">${ic("info")}<span><b>Tahlil:</b> uzilishlarning ${Math.round(peakN / st.total * 100)}% qismi ${peakLbl} oralig'ida kuzatilgan.${
      main && main[1] ? ` Asosiy sabab — ${esc(main[0].toLowerCase())}.` : ""}</span>
    <button class="link" data-go="ev">${ic("ext", "sm")}Hodisalar jadvalini ochish →</button></div>`
    : `<div class="tip ok">${ic("check")}<span><b>Toza:</b> bu davrda uzilish qayd etilmagan.</span></div>`;
  $$("#ttip [data-go]").forEach((b) => (b.onclick = () => go(b.dataset.go)));

  if (cz && cz.n) {
    const other = cz.n - CAUSE.reduce((a, [k]) => a + (cz.counts[k] || 0), 0);
    const rows = CAUSE.map(([k, l, col]) => [k, l, cz.counts[k] || 0, col]);
    if (other > 0) rows.push(["other", "Boshqa (online/resumed va h.k.)", other, "var(--d-unk)"]);
    $("#tcausen").textContent = `${cz.n} hodisa` + (cz.truncated ? " · oxirgi 500" : "");
    $("#tcauses").innerHTML = rows.map(([k, l, n, col]) => `<div class="cause">
      <span class="tile-i sm ${CAUSE_ICON[k][1]}">${ic(CAUSE_ICON[k][0])}</span>
      <div class="cnt"><div class="ln"><b>${esc(l)}</b><span>${n} · ${Math.round(n / cz.n * 100)}%</span></div>
      <div class="bar-h"><i style="width:${Math.round(n / cz.n * 100)}%;background:${col}"></i></div></div></div>`).join("");
  } else {
    $("#tcausen").textContent = "0 hodisa";
    $("#tcauses").innerHTML = `<div class="meta" style="padding:8px 0">Bu davrda hodisa yo'q.</div>`;
  }

  const rows = S.worst || [];
  $("#tworstn").textContent = `${rows.length} kamera`;
  $("#tworst").innerHTML = rows.length ? `<div class="worst h"><span>#</span><span>Kamera</span><span>Uzilish</span><span>Mavjudlik</span><span>Oxirgi uzilish</span><span>Holat</span></div>` +
    rows.map((c, i) => {
      const k = stateKind(c.state);
      const ago = c.last_offline_at ? sinceSec(c.last_offline_at) : -1;
      return `<button class="worst" data-id="${c.id}">
      <span class="rank r${i + 1}">${i + 1}</span>
      <span class="nm"><i class="dot" style="background:${DOT[c.state] || DOT.unknown}"></i><b>${esc(c.name)}</b></span>
      <span class="m">${c.outages}</span>
      <span class="m" style="color:${upColor(c.uptime_pct)}">${c.uptime_pct}%</span>
      <span class="m">${c.last_offline_at ? esc(localDM(c.last_offline_at) + " " + localHM(c.last_offline_at)) : "—"}${ago >= 0 ? `<small>${age(ago)} oldin</small>` : ""}</span>
      <span><span class="tag ${k === "bad" ? "bad" : k === "warn" ? "mid" : k === "ok" ? "good" : ""}">${esc(c.state)}</span></span>
    </button>`;}).join("") : "";
  $$("#tworst .worst[data-id]").forEach((b) => (b.onclick = () => {
    const id = +b.dataset.id, c = S.byId.get(id);
    if (c && isProb(c)) { S.sel = id; go("verdict"); } else openDiag(id);
  }));
  $("#tempty").innerHTML = rows.length ? "" :
    `<div class="empty" style="margin-top:8px">Bu davrda uzilish qayd etilmagan.</div>`;
}

$$("#thours .pill").forEach((b) => (b.onclick = () => {
  S.tHours = +b.dataset.h;
  $$("#thours .pill").forEach((x) => x.classList.toggle("sel", x === b));
  loadStat();
}));

/* ═════════ devor ═════════
   Video BOSILGANDA ochiladi — devor ochilishi bilan hamma oqim birdan
   tortilmasin (kamera va tarmoqqa ortiqcha yuk bo'lardi). "Hammasini
   jonli ochish" — ataylab, bir bosishda.                              */
/* Pleyerlar kamera id bo'yicha: devor qayta chizilganda qaysi katak
   qaysi pleyerga tegishli ekani shu yerdan topiladi. */
const wallPlayers = new Map();
$$("[data-w]").forEach((b) => (b.onclick = () => {
  S.wallN = +b.dataset.w; S.wallPage = 1;
  $$("[data-w]").forEach((x) => x.classList.toggle("sel", x === b));
  if (S.wallView === "live" && S.wallN >= 16) heavyWarn();
  drawWall(true);
}));
$$("#wview .pill").forEach((b) => (b.onclick = () => {
  S.wallView = b.dataset.wv;
  $$("#wview .pill").forEach((x) => x.classList.toggle("sel", x === b));
  if (S.wallView === "live" && S.wallN >= 16) heavyWarn();
  drawWall(true);
}));
let heavyWarnedAt = 0;
function heavyWarn() {
  if (Date.now() - heavyWarnedAt < 30000) return;
  heavyWarnedAt = Date.now();
  toast("Ko'p jonli oqim og'ir", `${S.wallN} katak jonli video brauzerni sekinlashtiradi — ko'p kamera uchun «Surat» rejimi tavsiya etiladi`, "mid");
}
$$("#wmode .pill").forEach((b) => (b.onclick = () => {
  S.wallMode = b.dataset.wm; S.wallPage = 1;
  $$("#wmode .pill").forEach((x) => x.classList.toggle("sel", x === b));
  drawWall(true);
}));
$("#wregion").onchange = () => { S.wallRegion = $("#wregion").value; S.wallPage = 1; drawWall(true); };
$("#wnode").onchange = () => { S.wallNode = $("#wnode").value; S.wallPage = 1; drawWall(true); };
$("#wsearch").oninput = () => { S.wallQ = $("#wsearch").value; S.wallPage = 1; drawWall(true); };
$("#wclear").onclick = () => { S.picked.clear(); if (S.wallMode === "sel") S.wallMode = "all"; drawWall(true); drawNav(); };
/* "Hammasini jonli ochish": oqimlar BIRDAN emas, ketma-ket ochiladi.
   Sabab: 25 katakni bir vaqtda ochsak MediaMTX 25 ta RTSP ulanishni
   birdan tortadi, har biri tayyor bo'lguncha HLS 400, WHEP 500 qaytaradi
   va konsol xatoga to'ladi (ishlab chiqarishda aynan shu ko'rindi).
   Har 300 ms da bittadan ochsak, har yo'l tayyor bo'lishga ulguradi.
   Yana: faqat ONLINE kameralar ochiladi — offline/stalled/unknown oqim
   bermaydi, ularni ochish bekorga xato oqizadi (katakni qo'lda bosib
   sinash mumkin). */
let wallBulkTimer = null;
$("#wlive").onclick = () => {
  clearTimeout(wallBulkTimer);
  const playing = $$("#wall .tile.playing");
  if (playing.length) {                       // ochiq bo'lsa — hammasini yopamiz
    playing.forEach((t) => t.click());
    updateWallFoot();
    return;
  }
  // Faqat online VA kodeki aniqlangan kameralar. Kodeksi bo'sh online
  // kamera — porti ochiq, lekin RTSP tekshiruvi oqim ololmagan (ko'pincha
  // login/parol noto'g'ri yoki yo'l xato). Ularni ommaviy ochsak MediaMTX
  // bekorga urinib 400/500 qaytaradi va konsol xatoga to'ladi — shuning
  // uchun chetlab o'tamiz (katakni qo'lda bosib tekshirsa bo'ladi).
  const cams = $$("#wall .tile:not(.playing)").map((t) => ({t, c: S.byId.get(+t.dataset.id)}))
    .filter((o) => o.c && o.t.querySelector("video"));
  const queue = cams.filter((o) => o.c.state === "online" && o.c.codec).map((o) => o.t);
  const skipped = cams.length - queue.length;
  if (!queue.length) {
    toast("Ochiladigan oqim yo'q", "Faqat oqimi tasdiqlangan online kameralar ochiladi", "mid");
    return;
  }
  if (skipped) toast(`${queue.length} ta oqim ochilmoqda`, `${skipped} ta kamera o'tkazib yuborildi (oqim tasdiqlanmagan)`, "");
  let i = 0;
  const step = () => {
    if (S.page !== "wall" || i >= queue.length) { updateWallFoot(); return; }
    const t = queue[i++];
    if (!t.classList.contains("playing")) t.click();
    $("#wlive span").textContent = `Ochilmoqda… ${i}/${queue.length}`;
    wallBulkTimer = setTimeout(step, 300);
  };
  step();
};

function wallCams() {
  const q = (S.wallQ || "").toLowerCase().trim();
  let l = S.cams.filter((c) => c.enabled);
  if (S.wallMode === "online") l = l.filter((c) => c.state === "online" && c.codec);
  else if (S.wallMode === "prob") l = l.filter((c) => isProb(c));
  else if (S.wallMode === "sel") l = l.filter((c) => S.picked.has(c.id));
  if (S.wallRegion) l = l.filter((c) => (c.region || "") === S.wallRegion);
  if (S.wallNode) l = l.filter((c) => String(c.node_id || 1) === S.wallNode);
  if (q) l = l.filter((c) => (c.name || "").toLowerCase().includes(q) ||
      (c.ip || "").includes(q) || (c.region || "").toLowerCase().includes(q));
  // Muammolilar tepada — devorda ham e'tibor kerak bo'lganlar oldin.
  return l.sort((a, b) => (RANK[effState(a)] ?? 9) - (RANK[effState(b)] ?? 9) ||
      String(a.name || "").localeCompare(String(b.name || "")));
}
/* Hudud/tugun tanlagichlarini kameralar ro'yxatidan to'ldiradi. */
function fillWallSelects() {
  const regs = [...new Set(S.cams.map((c) => c.region).filter(Boolean))].sort();
  const cur = $("#wregion");
  if (cur && cur.dataset.n !== String(regs.length)) {
    cur.dataset.n = regs.length;
    cur.innerHTML = `<option value="">Barcha hudud</option>` +
      regs.map((r) => `<option value="${esc(r)}"${r === S.wallRegion ? " selected" : ""}>${esc(r)}</option>`).join("");
  }
  const nodes = [...new Set(S.cams.map((c) => c.node_id || 1))].sort((a, b) => a - b);
  const nc = $("#wnode");
  if (nc && nc.dataset.n !== String(nodes.length)) {
    nc.dataset.n = nodes.length;
    nc.innerHTML = `<option value="">Barcha tugun</option>` +
      nodes.map((n) => `<option value="${n}"${String(n) === S.wallNode ? " selected" : ""}>${esc(nodeName({node_id: n}))}</option>`).join("");
  }
}
let wallSnapTimer = null;
/* Surat rejimida kataklar rasmini muntazam yangilaydi — hammasini birdan
   emas, bo'lib (tarmoq portlamasin). To'liq aylanish ~8 s. Rasm `?stale=1`
   bilan server keshidan olinadi: kameraga qo'shimcha ulanish yo'q. */
function startWallSnap() {
  clearTimeout(wallSnapTimer);
  if (S.page !== "wall" || S.wallView !== "snap") return;
  let i = 0;
  const tick = () => {
    if (S.page !== "wall" || S.wallView !== "snap") { clearTimeout(wallSnapTimer); return; }
    const imgs = $$("#wall .tile-snap");
    if (imgs.length) {
      const batch = Math.max(1, Math.ceil(imgs.length / 4));
      for (let b = 0; b < batch; b++) {
        const img = imgs[(i + b) % imgs.length];
        const cid = img.closest(".tile").dataset.id;
        img.src = `/api/v1/cameras/${cid}/snapshot?stale=1&t=${Date.now()}`;
      }
      i = (i + batch) % imgs.length;
    }
    wallSnapTimer = setTimeout(tick, 2000);
  };
  wallSnapTimer = setTimeout(tick, 2000);
}
function stopWall() {
  clearTimeout(wallBulkTimer);
  clearTimeout(wallSnapTimer);
  wallPlayers.forEach((p) => p.stop());
  wallPlayers.clear();
  if (mosaicPlayer) { mosaicPlayer.stop(); mosaicPlayer = null; }
  S._mosaicSig = null;
}

/* ═════════ kompozit (server mozaikasi) ═════════
   Server bir necha kamerani BITTA katakli oqimga birlashtiradi (FFmpeg
   xstack). Brauzer 36 ta emas, bitta oqim ochadi — 6×6 ham, bir necha
   brauzer ham qotmaydi, chunki dekodlash serverda bir marta bo'ladi.
   Katakni bosish o'sha kamerani ochadi. */
let mosaicPlayer = null;
function drawMosaic(restart) {
  const all = wallCams();
  const n = S.wallN, cols = Math.max(1, Math.round(Math.sqrt(n))), rows = Math.ceil(n / cols);
  const pages = Math.max(1, Math.ceil(all.length / n));
  if (S.wallPage > pages) S.wallPage = pages;
  if (S.wallPage < 1) S.wallPage = 1;
  S._wallTotal = all.length; S._wallPages = pages;
  const page = all.slice((S.wallPage - 1) * n, S.wallPage * n);
  const ids = page.map((c) => c.id);
  const sig = ids.join(",") + "|" + cols + "x" + rows;

  if (restart || !$("#wall .mosaic-tile")) {
    stopWall();
    $("#wall").style.gridTemplateColumns = "1fr";
    $("#wall").innerHTML = ids.length ? `<div class="tile mosaic-tile" style="aspect-ratio:${cols * 16}/${rows * 9}">
      <video muted playsinline></video>
      <div class="tile-msg">tayyorlanmoqda…</div>
      <div class="mosaic-hit" title="Katakni bosing — kamera ochiladi"></div>
      <div class="tile-h"><i class="dot s-online"></i>Kompozit · ${ids.length} kamera · ${cols}×${rows}</div>
      <div class="tile-f"><span>server mozaikasi · bitta oqim</span><span class="sep">|</span><span>H.264</span>
        <span class="r"><span class="rate">—</span></span></div>
    </div>` : "";
    S._mosaicSig = null;
  }
  updateWallFoot();
  $("#wempty").innerHTML = ids.length ? "" : `<span class="wall-empty">${
    S.wallMode === "sel" ? "Devor bo'sh — kamera tanlang."
      : S.wallRegion || S.wallNode || S.wallQ ? "Bu filtrga mos kamera yo'q."
      : "Ko'rsatiladigan kamera yo'q."}</span>`;
  if (!ids.length || sig === S._mosaicSig) return;    // o'zgarmagan — qayta so'ramaymiz
  S._mosaicSig = sig;

  const tile = $("#wall .mosaic-tile"), video = tile.querySelector("video"), msg = tile.querySelector(".tile-msg");
  msg.textContent = "mozaika tayyorlanmoqda… (bir necha soniya)";
  api("/api/v1/walls", {method: "POST", body: {camera_ids: ids, cols, rows}})
    .then((w) => {
      if (S.page !== "wall" || S.wallView !== "mosaic") return;
      S._mosaicTiles = w.tiles;
      if (!mosaicPlayer) mosaicPlayer = createPlayer(video, msg);
      mosaicPlayer.open({id: 0, name: "Kompozit devor",
        _urls: {stream_url: w.stream_url, webrtc_url: w.webrtc_url, mode: w.mode}});
      const hit = tile.querySelector(".mosaic-hit");
      hit.onclick = (e) => {
        // Bosilgan nuqta -> katak (col,row) -> kamera. Video object-fit:
        // contain, shuning uchun avval haqiqiy tasvir to'rtburchagini
        // (letterbox'siz) hisoblaymiz.
        const r = video.getBoundingClientRect();
        const va = (w.cols * 16) / (w.rows * 9);      // mozaika nisbati
        const ba = r.width / r.height;
        let cw = r.width, ch = r.height, ox = 0, oy = 0;
        if (ba > va) { cw = r.height * va; ox = (r.width - cw) / 2; }
        else { ch = r.width / va; oy = (r.height - ch) / 2; }
        const px = e.clientX - r.left - ox, py = e.clientY - r.top - oy;
        if (px < 0 || py < 0 || px > cw || py > ch) return;
        const col = Math.min(w.cols - 1, Math.floor(px / (cw / w.cols)));
        const row = Math.min(w.rows - 1, Math.floor(py / (ch / w.rows)));
        const t = (w.tiles || []).find((x) => x.col === col && x.row === row);
        if (t) openDiag(t.camera_id);
      };
    })
    .catch((e) => { if (msg) msg.textContent = "mozaika olinmadi: " + e.message; S._mosaicSig = null; });
}
/* Katak turi: video bo'ladimi yoki "ulanish yo'q" yozuvi. Tur o'zgarsa
   katak qayta quriladi, o'zgarmasa pleyer tegilmaydi. */
/* Katak turi ko'rinishga bog'liq:
   SURAT rejimi — jonli video EMAS, muntazam yangilanadigan JPEG. Dekodlash
   yo'q, kameraga qo'shimcha RTSP ulanish yo'q (server keshidagi surat),
   shuning uchun 6×6 ham, bir necha brauzer ham bemalol ko'taradi.
   JONLI rejimi — haqiqiy video (og'ir): bir necha kamerani yaqindan
   kuzatish uchun. */
function tileKind(c) {
  const e = effState(c);
  if (S.wallView === "snap") return c.snapshot_at ? "snap" : "snapoff";
  return e === "offline" || e === "unknown" || e === "disabled" ? "off" : "video";
}
function tileHtml(c) {
  const k = tileKind(c), e = effState(c);
  let body;
  if (k === "snap") {
    body = `<img class="tile-snap" alt="" loading="lazy"
        src="/api/v1/cameras/${c.id}/snapshot?stale=1&t=${Math.floor(Date.now() / 8000)}"
        onerror="this.closest('.tile').classList.add('imgerr')">
      <div class="tile-noimg">${ic("cam-off", "lg")}<span>surat yuklanmadi</span></div>
      ${e !== "online" ? `<div class="tile-badge ${stateKind(e)}"><i class="dot" style="background:${DOT[e]}"></i>${esc(LBL[e] || e)}</div>` : ""}`;
  } else if (k === "snapoff") {
    body = `<div class="tile-off ${e === "unknown" ? "idle" : ""}">${ic("cam-off")}<b>${
        e === "offline" ? "ulanish yo'q" : e === "unknown" ? "tekshirilmagan" : "surat hali yo'q"}</b><span>${
        e === "offline" ? "Kamera aloqasi uzilgan" : e === "unknown" ? "Birinchi tekshiruv navbatda" : "Snapshot tsikli hali yetmagan"}</span></div>`;
  } else if (k === "off") {
    body = `<div class="tile-off ${c.state === "unknown" ? "idle" : ""}">${ic("cam-off")}<b>${
        c.state === "offline" ? "ulanish yo'q" : "tekshirilmagan"}</b><span>${
        c.state === "offline" ? "Kamera aloqasi uzilgan" : "Birinchi tekshiruv navbatda"}</span></div>`;
  } else {
    body = `<video muted playsinline ${c.snapshot_at ? `poster="/api/v1/cameras/${c.id}/snapshot?stale=1"` : ""}></video>
      <div class="tile-msg"></div><div class="tile-play">▶</div>`;
  }
  const hasFrame = k === "snap" || k === "video";
  return `${body}
    <div class="tile-h"><i class="dot" style="background:${DOT[e]}"></i>${esc(c.name)}</div>
    <div class="tile-tr"><span class="snapt">${c.snapshot_at ? esc(localDM(c.snapshot_at) + " " + localHM(c.snapshot_at)) : ""}</span><span class="livet">● JONLI</span>
      ${k === "video" ? `<button class="tb tfull" title="To'liq ekran">${ic("full", "sm")}</button>` : ""}
      ${S.wallMode === "sel" ? `<button class="tb x tile-x" title="Devordan olib tashlash">${ic("x", "sm")}</button>` : ""}</div>
    <div class="tile-f"><span>${esc(c.slug || c.external_id || "")}</span><span class="sep">|</span><span>${esc(c.codec || "—")}</span>${
      c.resolution ? `<span class="sep hide-sm">|</span><span class="hide-sm">${esc(c.resolution)}</span>` : ""}${
      c.fps ? `<span class="sep hide-sm">|</span><span class="hide-sm">${c.fps}fps</span>` : ""}
      <span class="r"><span class="rate"></span>${hasFrame ? `<button class="tb tsnap" title="Oxirgi kadr">${ic("snap", "sm")}</button>` : ""}</span></div>`;
}
function bindTile(t, c) {
  const id = c.id;
  t.querySelector(".tile-h").onclick = (e) => { e.stopPropagation(); openDiag(id); };
  const x = t.querySelector(".tile-x");
  if (x) x.onclick = (e) => { e.stopPropagation(); S.picked.delete(id); drawWall(true); drawNav(); };
  const video = t.querySelector("video");
  const full = t.querySelector(".tfull"), snap = t.querySelector(".tsnap");
  if (full && video) full.onclick = (e) => { e.stopPropagation(); (video.requestFullscreen || video.webkitEnterFullscreen || (() => {})).call(video); };
  if (snap) snap.onclick = (e) => { e.stopPropagation(); window.open(`/api/v1/cameras/${id}/snapshot?stale=1&t=${Date.now()}`, "_blank"); };
  if (!video) { t.onclick = () => openDiag(id); return; }   // surat rejimi: bosilsa kamera sahifasi
  // Video BOSILGANDA ochiladi — devor ochilishi bilan hamma oqim birdan
  // tortilmasin (kamera va tarmoqqa ortiqcha yuk bo'lardi). "Hammasini
  // jonli ochish" tugmasi — ataylab, bir bosishda.
  t.onclick = () => {
    const overlay = t.querySelector(".tile-play");
    let player = wallPlayers.get(id);
    if (!player) {
      player = createPlayer(video, t.querySelector(".tile-msg"));
      wallPlayers.set(id, player);
    }
    if (t.classList.contains("playing")) {
      player.stop();
      t.classList.remove("playing");
      if (overlay) overlay.style.display = "";
    } else {
      player.open(S.byId.get(id), "sub");
      t.classList.add("playing");
      if (overlay) overlay.style.display = "none";
    }
    updateWallFoot();
  };
}

/* restart=true — devor butunlay qayta quriladi (setka yoki rejim
   o'zgardi). restart=false — fon yangilanishi (loadCams har 15 s, SSE
   hodisasi): kameralar to'plami o'sha bo'lsa kataklar va ularning
   pleyerlari SAQLANADI, faqat holat nuqtasi yangilanadi. Ilgari bu
   farq yo'q edi va har 15 soniyada hamma pleyer to'xtatilib qayta
   yaratilardi — tomoshabin uchun "bir necha soniya ko'rsatib uzilib
   qolish" aynan shu edi. */
/* restart=true — user rejim/filtr/setka/sahifani o'zgartirdi yoki devorga
   birinchi kirildi: to'liq quriladi (oqimlar to'xtaydi — kutilgan).
   restart!=true — fon yangilanishi (loadCams 15 s, SSE holat hodisasi):
   OQIMLAR HECH QACHON UZILMAYDI. Faqat mavjud kataklar holati yangilanadi;
   ro'yxat yoki tartib o'zgarsa ham qayta QURILMAYDI. Ilgari bu farq yo'q
   edi va bitta kamera holati o'zgarganda (masalan "online" filtridan
   chiqib ketsa) butun ro'yxat o'zgarib, stopWall() bilan HAMMA oqim
   birdan uzilardi — foydalanuvchi aynan shuni ko'rgan. */
function drawWall(restart) {
  if (S.page !== "wall") return;
  fillWallSelects();
  if (S.wallView === "mosaic") { drawMosaic(restart); return; }
  const all = wallCams();
  const pages = Math.max(1, Math.ceil(all.length / S.wallN));
  if (S.wallPage > pages) S.wallPage = pages;
  if (S.wallPage < 1) S.wallPage = 1;
  S._wallTotal = all.length; S._wallPages = pages;
  const cur = $$("#wall .tile");

  if (!restart && cur.length) {
    // Fon yangilanishi — buzmaymiz, faqat holatni yangilaymiz.
    cur.forEach((t) => {
      const c = S.byId.get(+t.dataset.id);
      if (!c) return;
      if (t.dataset.kind !== tileKind(c)) {
        // Shu bitta kamera turini o'zgartirdi (online↔offline) — faqat
        // O'SHA katak qayta quriladi, boshqalari tegilmaydi.
        const p = wallPlayers.get(c.id);
        if (p) { p.stop(); wallPlayers.delete(c.id); }
        t.className = "tile"; t.dataset.kind = tileKind(c);
        t.innerHTML = tileHtml(c); bindTile(t, c);
      } else {
        const dot = t.querySelector(".tile-h .dot");
        if (dot) dot.style.background = DOT[effState(c)];
      }
    });
    updateWallFoot();
    return;
  }

  // To'liq qurish (user amali yoki birinchi chizish).
  const l = all.slice((S.wallPage - 1) * S.wallN, S.wallPage * S.wallN);
  stopWall();
  const cols = Math.ceil(Math.sqrt(Math.min(S.wallN, Math.max(l.length, 1))));
  $("#wall").style.gridTemplateColumns = `repeat(${cols},minmax(0,1fr))`;
  $("#wall").innerHTML = l.map((c) =>
    `<div class="tile" data-id="${c.id}" data-kind="${tileKind(c)}">${tileHtml(c)}</div>`).join("");
  $$("#wall .tile").forEach((t) => bindTile(t, S.byId.get(+t.dataset.id)));
  startWallSnap();
  updateWallFoot();
  $("#wempty").innerHTML = l.length ? "" : `<span class="wall-empty">${
    S.wallMode === "sel"
      ? "Devor bo'sh — Kameralar jadvalidagi katakchani belgilang yoki kamera sahifasida «Devorga qo'shish» ni bosing."
      : S.wallMode === "prob" ? "Muammoli kamera yo'q — hammasi ishlayapti."
      : S.wallRegion || S.wallNode || S.wallQ ? "Bu filtrga mos kamera yo'q — filtrni kengaytiring."
      : "Ko'rsatiladigan kamera yo'q."}</span>`;
}
function updateWallFoot() {
  let total = 0;
  const tiles = $$("#wall .tile");
  tiles.forEach((t) => {
    const c = S.byId.get(+t.dataset.id);
    if (!c) return;
    const mb = camRt(c).inMbps;
    total += mb;
    const rate = t.querySelector(".tile-f .rate");
    if (rate) rate.textContent = mb ? mb.toFixed(1) + " Mb/s" : "—";
  });
  const playing = $$("#wall .tile.playing").length;
  $("#wlive span").textContent = playing ? "Oqimlarni yopish" : "Hammasini jonli ochish";
  $("#wseln").textContent = S.picked.size || "";
  const eg = S.health ? ` · egress ${Math.round(S.health.egress_mbps)}/${S.health.egress_capacity_mbps} Mbit/s` : "";
  const total_ = S._wallTotal || tiles.length, pages = S._wallPages || 1;
  const from = total_ ? (S.wallPage - 1) * S.wallN + 1 : 0, to = Math.min(total_, S.wallPage * S.wallN);
  const modeLbl = {all: "hammasi", online: "online", prob: "muammoli", sel: "tanlangan"}[S.wallMode] || "hammasi";
  const view = S.wallView;
  $("#wlive").style.display = view === "live" ? "" : "none";   // ommaviy ochish faqat jonlida
  const suffix = view === "snap" ? "surat rejimi · avtomatik yangilanadi"
    : view === "mosaic" ? "kompozit · server bitta oqimga birlashtiradi"
    : `${playing} jonli · kirish ${total.toFixed(1)} Mbit/s${eg}`;
  $("#wsum").textContent = `${total_} kamera · ${modeLbl}${S.wallRegion ? " · " + S.wallRegion : ""} · ${suffix}`;
  $("#wcount").textContent = total_
    ? `${from}–${to} ko'rsatilyapti · ${total_} tadan · ${
        view === "snap" ? "surat (~8 s da yangilanadi)" : view === "mosaic" ? "kompozit · bitta oqim" : "jonli · sub oqim"}` : "kamera yo'q";
  $("#wpager").innerHTML = pages > 1
    ? `<button class="btn ghost sm icon" id="wprev"${S.wallPage <= 1 ? " disabled" : ""}>${ic("chev-r", "sm")}</button>
       <span class="meta">${S.wallPage} / ${pages}</span>
       <button class="btn ghost sm icon" id="wnext"${S.wallPage >= pages ? " disabled" : ""}>${ic("chev-r", "sm")}</button>` : "";
  const pv = $("#wprev"); if (pv) pv.onclick = () => { if (S.wallPage > 1) { S.wallPage--; drawWall(true); } };
  const nx = $("#wnext"); if (nx) nx.onclick = () => { if (S.wallPage < pages) { S.wallPage++; drawWall(true); } };
}

/* ═════════ kamera sahifasi ═════════ */
let probeResult = null;
function openDiag(id) {
  const c = S.byId.get(id);
  if (!c) return;
  if (S.curId !== id) { probeResult = null; closeLive(); S.diagDay = 0; S.hist = null; }
  S.curId = id;
  go("diag");
  drawDiag();
  loadDiagHistory(c);
  warmStream(c);          // play bosilguncha oqim tayyor bo'lib tursin
}

/* ── jonli ko'rish: panel doim ko'rinadi, video bir bosishda ── */
let diagPlayer = null, diagQ = "";
function canLive(c) { return c && c.state !== "offline" && c.state !== "disabled" && c.enabled !== false; }
function openLive(c) {
  if (!canLive(c)) return;
  const box = $("#dlive"), video = $("#dvideo");
  box.classList.add("playing");
  // Surat hech qachon olinmagan bo'lsa so'ramaymiz ham: endpoint
  // ataylab 404 qaytaradi va har so'rov konsolda xato bo'lib chiqadi.
  if (c.snapshot_at) video.poster = `/api/v1/cameras/${c.id}/snapshot`;
  else video.removeAttribute("poster");
  if (!diagPlayer) diagPlayer = createPlayer(video, $("#dlivemsg"));
  diagPlayer.open(c, diagQ);
  drawLiveBadge(c);
}
function closeLive() {
  if (diagPlayer) diagPlayer.stop();
  const box = $("#dlive");
  if (box) { box.classList.remove("playing"); $("#dlivemsg").textContent = ""; }
  const c = S.byId.get(S.curId);
  if (c && S.page === "diag") drawLiveBadge(c);
}
function setLiveQ(q) {
  diagQ = q;
  $("#dlq").classList.toggle("sel", !q);
  $("#dlqsub").classList.toggle("sel", q === "sub");
  const c = S.byId.get(S.curId);
  if (c && $("#dlive").classList.contains("playing")) openLive(c);
}
$("#dlq").onclick = (e) => { e.stopPropagation(); setLiveQ(""); };
$("#dlqsub").onclick = (e) => { e.stopPropagation(); setLiveQ("sub"); };
$("#dlive").onclick = () => {
  const c = S.byId.get(S.curId);
  if (!c) return;
  if ($("#dlive").classList.contains("playing")) closeLive(); else openLive(c);
};
function drawLiveBadge(c) {
  const box = $("#dlive"), playing = box.classList.contains("playing");
  const video = $("#dvideo");
  if (!playing) {
    if (c.snapshot_at && c.state !== "offline") video.poster = `/api/v1/cameras/${c.id}/snapshot?stale=1`;
    else video.removeAttribute("poster");
    $("#dlivemsg").textContent = c.state === "online"
      ? (c.snapshot_at ? "surat · bosib jonli oqimni oching" : "surat hali yo'q · bosib jonli oqimni oching")
      : c.state === "offline" ? `ulanish yo'q${sinceSec(c.last_seen) >= 0 ? " — oxirgi javob " + age(sinceSec(c.last_seen)) + " oldin" : ""}`
      : c.state === "stalled" ? "kadr kelmayapti — oqim muzlagan · bosib sinab ko'ring"
      : c.state === "disabled" || c.enabled === false ? "o'chirib qo'yilgan" : "hali tekshirilmagan";
    $("#dlivemsg").style.color = c.state === "online" ? "var(--dark-ink)" : DOT[c.state] || "var(--dark-ink)";
  } else $("#dlivemsg").style.color = "#fff";
  $("#dlivebadge").innerHTML = canLive(c)
    ? (playing ? `<i></i>JONLI · yopish uchun bosing` : `▶ Jonli ochish`) : "";
  $("#dlivebadge").style.display = canLive(c) ? "" : "none";
}

function drawDiag() {
  const c = S.byId.get(S.curId);
  if (!c) return;
  const k = stateKind(c.state);
  $("#ddot").style.background = DOT[c.state];
  $("#dname").textContent = c.name;
  const stp = $("#dstate");
  stp.textContent = LBL[c.state] || c.state;
  stp.className = "stp tint-" + k;
  stp.style.color = KINK[k];
  $("#dsub").textContent = [c.ip ? `${c.ip}:${c.port || 554}` : "tayyor oqim", c.codec || null,
    c.region || "hududsiz", nodeName(c), c.external_id ? "id " + c.external_id : null,
    ipGroup(c).length > 1 ? `registratorda ${ipGroup(c).length} kanal` : null].filter(Boolean).join(" · ");
  $("#dacts").innerHTML = `
    <button class="btn dark" data-a="verdict">Xato qayerda? →</button>
    ${canLive(c) ? '<button class="btn ghost" data-a="live">▶ Jonli</button>' : ""}
    <button class="btn ghost" data-a="probe">Qayta tekshirish</button>
    <button class="btn ghost" data-a="kf">Keyframe</button>
    <button class="btn ghost" data-a="snap">Suratni yangilash</button>
    <button class="btn ghost" data-a="stale">Oxirgi kadr</button>
    <button class="btn ghost" data-a="wall">Devorga qo'shish</button>
    <button class="btn ghost" data-a="toggle">${c.enabled ? "O'chirib qo'yish" : "Yoqish"}</button>
    <button class="btn danger" data-a="del">O'chirish</button>`;
  $$("#dacts [data-a]").forEach((b) => (b.onclick = () => act(b.dataset.a, c)));
  drawLiveBadge(c);
  drawSince();
  drawDiagHistory();
  drawDiagCards();
  loadHistory(c);
}

/* "Hozir" kartasi: uzilgan bo'lsa qachondan beri, ishlayotgan bo'lsa
   oxirgi kadr va tomoshabin. Runtime bilan 3 soniyada yangilanadi. */
function drawSince() {
  const c = S.byId.get(S.curId);
  if (!c || S.page !== "diag") return;
  const rt = camRt(c), a = snapAge(c);
  let kind, title, label, note;
  if (c.state === "offline") {
    const s = sinceSec(c.last_seen);
    kind = "bad"; title = "hozir uzilgan";
    label = s >= 0 ? age(s) + " dan beri" : "uzilgan";
    const nvr = nvrInfo(c);
    note = (s >= 0 ? `oxirgi javob ${localDM(c.last_seen)} ${localHM(c.last_seen)} · ` : "") +
      (nvr.allDown ? "registrator javob bermayapti" : "TCP 554 javob yo'q");
  } else if (c.state === "stalled") {
    kind = "warn"; title = "hozir muzlagan";
    label = a >= 0 ? age(a) + " dan beri" : "kadr yo'q";
    note = `ulanish bor, bayt kelmayapti · kirish ${rt.inMbps.toFixed(1)} Mbit/s`;
  } else if (c.state === "unknown") {
    kind = "idle"; title = "hozir"; label = "tekshirilmagan"; note = "birinchi tekshiruv navbatda · health 60 s";
  } else if (c.state === "disabled" || c.enabled === false) {
    kind = "idle"; title = "hozir"; label = "o'chirilgan"; note = "admin o'chirib qo'ygan";
  } else {
    kind = "ok"; title = "hozir"; label = "efirda";
    note = `oxirgi kadr ${a >= 0 ? age(a) + " oldin" : "yo'q"} · tomoshabin ${rt.readers}` +
      (rt.ready ? ` · ${rt.inMbps.toFixed(1)} Mbit/s` : " · yo'l kutmoqda");
  }
  $("#dsince").className = "since tint-" + kind;
  $("#dsince").innerHTML = `<div class="l"><span class="t" style="color:${KINK[kind]}">${title}</span>
    <b style="color:${KINK[kind]}">${esc(label)}</b></div><span class="n">${esc(note)}</span>`;
  // Tomoshabin KPI'si runtime bilan yangilanadi — qolganlari history'dan.
  const kv = $("#dkpi .k-live");
  if (kv) kv.textContent = rt.readers;
}

/* ═════════ kamera tahlili: uzilish tarixi ═════════

   Bitta so'rov — /admin/cameras/{id}/history — sahifadagi hamma narsani
   beradi: KPI, 30 kunlik ustunlar, kun kartasi va uzilishlar jurnali.
   Bo'lak-bo'lak so'ralsa ular bir-biriga mos kelmay qolardi, chunki har
   oraliq "hozir" ga bog'langan.                                        */
async function loadDiagHistory(c) {
  const tz = -new Date().getTimezoneOffset();
  try {
    const h = await api(`/api/v1/admin/cameras/${c.id}/history`
      + `?days=30&day=${S.diagDay}&tz_offset_minutes=${tz}`);
    if (S.curId === c.id) S.hist = h;
  } catch (e) { if (S.curId === c.id) S.hist = null; }
  if (S.page === "diag" && S.curId === c.id) drawDiagHistory();
}

function drawDiagHistory() {
  const h = S.hist, c = S.byId.get(S.curId);
  const show = (id, on) => { const el = $(id); if (el) el.style.display = on ? "" : "none"; };
  if (!h || !c) {
    $("#dkpi").innerHTML = ["mavjudlik 30k", "uzilish 30k", "o'chiq 30k", "bugun o'chiq", "mttr", "tomoshabin"]
      .map((l) => `<div class="k"><div class="k-l">${l}</div><div class="k-v" style="color:var(--mute)">${h === null && c ? "…" : "—"}</div></div>`).join("");
    ["#dcalwrap", "#dojwrap"].forEach((id) => show(id, false));
    return;
  }
  ["#dcalwrap", "#dojwrap"].forEach((id) => show(id, true));
  const s = h.summary;
  const today = (h.daily || []).find((d) => d.days_back === 0) || {offline_seconds: 0};

  /* ── KPI (3×2) ── */
  const tile = (lbl, val, color, cls = "") => `<div class="k"><div class="k-l">${lbl}</div>
    <div class="k-v ${cls}" ${color ? `style="color:${color}"` : ""}>${val}</div></div>`;
  $("#dkpi").innerHTML =
    tile("mavjudlik 30k", s.uptime_pct_period + "<u>%</u>", upColor(s.uptime_pct_period)) +
    tile("uzilish 30k", s.outages_period || "0", s.outages_period ? null : "var(--green)") +
    tile("o'chiq 30k", durHM(s.offline_seconds_period), s.offline_seconds_period > 36000 ? "var(--red)" : null) +
    tile("bugun o'chiq", today.offline_seconds ? durHM(today.offline_seconds) : "0m",
         today.offline_seconds ? "var(--amber)" : "var(--green)") +
    tile("mttr", durHM(s.mttr_seconds), null) +
    tile("tomoshabin", camRt(c).readers, null, "k-live");

  /* ── 30 kunlik ustunlar: bosilsa kun tanlanadi ── */
  const daily = (h.daily || []).slice().sort((a, b) => b.days_back - a.days_back);   // eskidan yangiga
  const maxD = Math.max(1, ...daily.map((d) => d.offline_seconds));
  $("#dcal").innerHTML = daily.map((d) => {
    const on = d.days_back === h.day;
    const col = !d.offline_seconds ? (on ? "var(--blue)" : "#DCEFE8") : d.offline_seconds > 3600 ? "var(--d-off)" : "var(--d-stall)";
    return `<button class="${on ? "sel" : ""}" data-d="${d.days_back}"
      title="${esc(d.date)} — ${d.outages ? d.outages + " uzilish · " + durHM(d.offline_seconds) : "toza"}">
      <i style="height:${Math.max(4, Math.round(d.offline_seconds / maxD * 100))}%;background:${col}"></i></button>`;
  }).join("");
  $("#dcalx").innerHTML = daily.map((d, i) => {
    const on = d.days_back === h.day;
    const t = on || i % 5 === 0 || i === daily.length - 1 ? d.date.slice(8, 10) : "";
    return `<span class="${on ? "sel" : ""}">${t}</span>`;
  }).join("");
  $$("#dcal button").forEach((b) => (b.onclick = () => {
    S.diagDay = +b.dataset.d;
    const cam = S.byId.get(S.curId);
    if (cam) loadDiagHistory(cam);
  }));

  /* ── kun kartasi ── */
  const dayName = h.day === 0 ? "bugun" : h.day === 1 ? "kecha" : `${h.day} kun oldin`;
  $("#ddaytitle").textContent = `${dayName} · ${h.selected_date.slice(8, 10)}.${h.selected_date.slice(5, 7)}`;
  $("#ddaynote").textContent = h.outages.length
    ? `${h.outages.length} uzilish · ${durHM(s.offline_seconds_day)} o'chiq` : "uzilish yo'q";
  $("#dtoday").style.display = h.day ? "" : "none";
  $("#dtoday").onclick = () => { S.diagDay = 0; loadDiagHistory(c); };
  const longest = h.outages.length ? Math.max(...h.outages.map((o) => o.seconds)) : 0;
  const peakLbl = h.peak && h.peak.offline_seconds
    ? `${dd(h.peak.from_hour)}:00–${dd(h.peak.to_hour)}:00` : "—";
  const dstat = (l, v, color) => `<div><span class="l">${l}</span><span class="v" style="color:${color || "var(--ink)"}">${v}</span></div>`;
  $("#ddaystats").innerHTML =
    dstat("mavjudlik", s.uptime_pct_day + "%", upColor(s.uptime_pct_day)) +
    dstat("uzilish", h.outages.length, h.outages.length ? null : "var(--green)") +
    dstat("o'chiq", s.offline_seconds_day ? durHM(s.offline_seconds_day) : "0m",
          s.offline_seconds_day ? "var(--amber)" : "var(--green)") +
    dstat("eng uzuni · pik", (longest ? durHM(longest) : "—") + (peakLbl !== "—" ? ` · ${peakLbl}` : ""), null);

  /* ── daqiqalik chiziq: 96 katak × 15 daq ──
     Ustunlar "qachon" ni aytadi, bu chiziq "qanday" ni: bir marta uzoq
     bo'lganmi yoki kun bo'yi uzuq-yuluqmi. */
  const stripMin = h.strip_minutes || 15, cellSec = stripMin * 60;
  $("#dstrip").innerHTML = h.strip.map((v, i) => {
    if (i >= h.strip_elapsed) return `<i class="sc future"></i>`;   // hali kelmagan
    const ratio = v / cellSec;
    const cls = !v ? "ok" : ratio >= 0.5 ? "bad" : "warn";
    return `<i class="sc ${cls}" title="${dd(Math.floor(i * stripMin / 60))}:${
      dd((i * stripMin) % 60)} — ${v ? durHM(v) + " o'chiq" : "uzilishsiz"}"></i>`;
  }).join("");

  /* ── uzilishlar jurnali ── */
  $("#doj").innerHTML = h.outages.map((o) => `<tr>
    <td class="meta" style="color:var(--ink)">${localHM(o.from)}</td>
    <td class="meta" style="color:var(--ink)">${o.recovered ? localHM(o.to) : "davom etyapti"}</td>
    <td class="meta" style="color:${o.recovered ? (o.seconds > 1800 ? "var(--amber)" : "var(--ink-3)") : "var(--red)"};text-align:right">${durHM(o.seconds)}</td>
    <td>${o.recovered ? `<span class="tag good">tiklandi</span>`
      : `<span class="tag bad">davom etyapti</span>`}</td>
  </tr>`).join("");
  $("#dojempty").innerHTML = h.outages.length ? "" :
    `<span class="log-ok">Bu kunda uzilish qayd etilmagan — kamera kun bo'yi efirda bo'lgan.</span>`;

  /* ── tizim harakatlari ──
     Uzilishlar jurnali "tarmoq nima qildi" ni aytadi, bu esa "tizim
     nima qildi" ni: oqim muzladimi, MediaMTX qayta ko'tarildimi. */
  const acts = h.actions || [];
  const KIND = {online: "good", offline: "bad", stalled: "mid", resumed: "good", mediamtx: "mid"};
  $("#dact").innerHTML = acts.map((a) => `<tr>
    <td class="meta">${localHM(a.ts)}</td>
    <td><span class="tag ${KIND[a.kind] || ""}">${esc(a.kind)}</span></td>
    <td class="meta">${a.path === "asosiy" ? "—" : esc(a.path)}</td>
    <td class="meta">${esc(a.detail || "—")}</td>
  </tr>`).join("");
  $("#dactempty").innerHTML = acts.length ? "" :
    `<span class="log-ok" style="color:var(--mute)">Bu kunda tizim yozuvi yo'q.</span>`;
}

function drawDiagCards() {
  const c = S.byId.get(S.curId);
  if (!c || S.page !== "diag") return;
  const rt = camRt(c), a = snapAge(c);
  const gb = rt.bytes > 1e9 ? (rt.bytes / 1e9).toFixed(1) + " GB"
    : rt.bytes ? Math.round(rt.bytes / 1e6) + " MB" : "—";
  const card = (h, kv, extra = "") => `<div class="card"><h3>${h}</h3><div class="kv">${
    kv.map(([x, y]) => `<div class="r"><span class="kk">${x}</span><span class="v">${y}</span></div>`).join("")}</div>${extra}</div>`;
  const snapUrl = `/api/v1/cameras/${c.id}/snapshot`;
  const showSnap = c.state !== "offline" && c.state !== "disabled" && c.snapshot_at;
  $("#dcards").innerHTML =
    card("Ulanish", [
      ["IP", esc(c.ip || "—")], ["Port", c.port || "—"],
      ["Ishlab chiqaruvchi", esc(c.vendor || "—")],
      ["RTSP yo'l", esc(c.rtsp_path || "—")],
      ["Model", esc(c.model || "—")], ["Firmware", esc(c.firmware || "—")],
      ["Registrator", ipGroup(c).length > 1 ? `${ipGroup(c).length} kanal · ${nvrInfo(c).sessions} sessiya` : "yakka kamera"]]) +
    card("Kodeklar", [
      ["Asosiy", esc(c.codec || "—")], ["Sub", esc(c.sub_codec || "topilmadi")],
      ["Sub yo'l", esc(c.sub_path || "—")],
      ["O'lcham", esc(c.resolution || "—")], ["Kadr tezligi", c.fps ? c.fps + " fps" : "—"],
      ["O'girish", c.transcode ? "H.265 → H.264 (kerak bo'lsa)" : "kerak emas"],
      ["Tez ochilish", c.always_on ? "yoqilgan" : "yo'q"]]) +
    card("MediaMTX", [
      ["Tugun", esc(nodeName(c))],
      ["Yo'l", rt.ready ? "tayyor" : "kutmoqda"],
      ["Kirish", rt.inMbps ? rt.inMbps.toFixed(1) + " Mbit/s" : "—"],
      ["Tomoshabin", rt.readers], ["Baytlar", gb],
      ["Issiq to'plam", rt.warm ? "ha" : "yo'q"],
      ["Slug", esc(c.slug || "—")]]) +
    card("Surat", [
      ["Yoshi", c.state === "offline" ? "berilmaydi (404)" : esc(age(a))],
      ["Oxirgi", c.snapshot_at ? esc(localDM(c.snapshot_at) + " " + localHM(c.snapshot_at)) : "—"]],
      showSnap ? `<img class="snap-img" id="dsnap" alt=""
        src="${snapUrl}?stale=1&t=${Math.floor(Date.now() / 30000)}" onerror="this.style.display='none'">` : "") +
    `<div class="card" style="grid-column:1/-1"><h3>Holat tarixi <span class="h3-sub">so'nggi hodisalar · shu kamera</span></h3>
      <div id="dhist" class="meta">yuklanmoqda…</div></div>`;
}
async function loadHistory(c) {
  try {
    const r = await api("/api/v1/admin/events?limit=300");
    const mine = (r.events || []).filter((e) =>
      e.slug && (e.slug === c.slug || e.slug.startsWith(c.slug + "_") ||
                 e.ip === c.ip)).slice(0, 12);
    const el = $("#dhist");
    if (!el) return;
    el.innerHTML = mine.length ? mine.map((e) => `<div class="hist-r">
      <span class="hist-t">${esc(localDM(e.ts) + " " + localHM(e.ts))}</span>
      <i class="dot" style="background:${e.kind === "online" || e.kind === "resumed" ? DOT.online
        : e.kind === "offline" ? DOT.offline : e.kind === "stalled" ? DOT.stalled : DOT.unknown}"></i>
      <span style="font-family:var(--mono);font-size:11px">${esc(e.kind)}</span>
      <span style="color:var(--mute)">${esc(e.detail || "")}</span>
    </div>`).join("") : "Bu kamera bo'yicha yozuv yo'q.";
  } catch (e) {}
}
async function act(a, c) {
  if (a === "verdict") { S.sel = c.id; go("verdict"); return; }
  if (a === "live") { openLive(c); return; }
  if (a === "wall") { S.picked.add(c.id); S.wallMode = "sel"; drawNav(); go("wall"); return; }
  if (a === "toggle") {
    try {
      const r = await api(`/api/v1/admin/cameras/${c.id}/enabled`,
        {method: "POST", body: {enabled: !c.enabled}});
      S.byId.set(c.id, r);
      S.cams = S.cams.map((x) => x.id === c.id ? r : x);
      toast(c.name, r.enabled ? "Yoqildi" : "O'chirib qo'yildi — oqim va surat to'xtatiladi");
      pushEv("amal", `<b>${esc(c.name)}</b> ${r.enabled ? "yoqildi" : "o'chirib qo'yildi"}`, c.id);
      closeLive(); drawDiag();
    } catch (e) { toast("Xato", e.message, "bad"); }
    return;
  }
  if (a === "del") {
    if (!confirm(`«${c.name}» butunlay o'chirilsinmi?\nBu amalni qaytarib bo'lmaydi.`)) return;
    try {
      await api(`/api/v1/admin/cameras/${c.id}`, {method: "DELETE"});
      toast("O'chirildi", c.name);
      pushEv("amal", `<b>${esc(c.name)}</b> o'chirildi`);
      S.curId = null; closeLive(); go("cams"); loadCams();
    } catch (e) { toast("Xato", e.message, "bad"); }
    return;
  }
  if (a === "stale") {
    window.open(`/api/v1/cameras/${c.id}/snapshot?stale=1&t=${Date.now()}`, "_blank");
    return;
  }
  try {
    if (a === "probe") {
      toast("Probe yuborildi", "OPTIONS → DESCRIBE → SETUP …");
      probeResult = await api("/api/v1/admin/probe", {method: "POST", body: {
        ip: c.ip, port: c.port || 554, username: c.username || "",
        rtsp_path: c.rtsp_path || "/", camera_id: c.id}});
      probeResult.camera = c.id;
      S.note[c.id] = probeResult.ok ? `Probe o'tdi: ${probeResult.message}`
        : `Probe to'xtadi (${probeResult.stage}): ${probeResult.message}`;
      pushEv(probeResult.ok ? "amal" : "xato",
        `<b>${esc(c.name)}</b> probe · ${esc(probeResult.message)}`, c.id);
      toast(c.name, S.note[c.id], probeResult.ok ? "" : "bad");
      loadCams();
    } else if (a === "kf") {
      const r = await api(`/api/v1/admin/cameras/${c.id}/keyframe`, {method: "POST"});
      toast("Keyframe", r.sent ? "Kamera qabul qildi" : "Qo'llamaydi yoki 2 s ichida takror",
        r.sent ? "" : "mid");
      pushEv("amal", `<b>${esc(c.name)}</b> keyframe · ${r.sent ? "yuborildi" : "rad"}`, c.id);
    } else if (a === "snap") {
      const res = await fetch(`/api/v1/cameras/${c.id}/snapshot?t=${Date.now()}`,
        {cache: "no-store"});
      if (res.ok) {
        toast("Surat yangilandi", "Disk zaxirasiga yozildi");
        const img = $("#dsnap");
        if (img) { img.style.display = ""; img.src =
          `/api/v1/cameras/${c.id}/snapshot?t=${Date.now()}`; }
      } else toast("Surat olinmadi", res.status === 404
        ? "Kamera offline yoki manba javob bermadi" : res.status + "-xato", "bad");
    }
  } catch (e) { toast("Xato", e.message, "bad"); }
}

/* ═════════ resurs ═════════
   Sarf kamera soniga emas, ko'rilayotgan oqimlarga bog'liq — "managed"
   tomoshabinlarga qarab o'sishi kerak, kameralarga qarab emas.        */
const hEg = [], hIn = [];
const HIST = {man: [], rd: [], tr: [], p95: []};
const hpush = (arr, v) => { arr.push(v); if (arr.length > 40) arr.shift(); };
/* Donut: bo'laklar [{v, color}], markazda raqam va izoh. */
function donut(parts, center, sub) {
  const total = parts.reduce((a, p) => a + p.v, 0) || 1;
  const r = 15.9155, C = 100;
  let off = 0;
  const segs = parts.filter((p) => p.v > 0).map((p) => {
    const len = p.v / total * C;
    const s = `<circle r="${r}" cx="18" cy="18" fill="none" stroke="${p.color}" stroke-width="4" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-off}"/>`;
    off += len; return s;
  }).join("");
  return `<div class="donut"><svg viewBox="0 0 36 36"><circle r="${r}" cx="18" cy="18" fill="none" stroke="var(--line-2)" stroke-width="4"/>${segs}</svg>
    <div class="c"><b>${center}</b><span>${sub}</span></div></div>`;
}
function spark(arr, col) {
  if (arr.length < 2) return "";
  const w = 100, h = 26, mx = Math.max(...arr, 1);
  const p = arr.map((v, i) =>
    `${i / (arr.length - 1) * w},${h - v / mx * (h - 3) - 1.5}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline points="${p}" fill="none" stroke="${col}" stroke-width="1.4"
      vector-effect="non-scaling-stroke"/></svg>`;
}
function drawSys() {
  if (S.page !== "res") return;
  const h = S.health;
  if (!h) { $("#sstats").innerHTML = `<div class="dstat"><span class="note">yuklanmoqda…</span></div>`; return; }
  const st = S.status || {};
  const ing = Object.entries(S.rates)
    .filter(([slug]) => !slug.endsWith("_h264"))     // o'girish ichki aylanish
    .reduce((s, [, v]) => s + v, 0);
  const transcodes = Object.keys(S.rt).filter((s) => s.endsWith("_h264")).length;
  const p95 = apiP95();
  hpush(hEg, h.egress_mbps); hpush(hIn, ing); hpush(HIST.man, h.managed); hpush(HIST.rd, h.readers);
  hpush(HIST.tr, transcodes); hpush(HIST.p95, p95 || 0);
  const cap = h.egress_capacity_mbps || 1000;
  const pct = h.egress_mbps / cap * 100;
  const foreign = Array.isArray(h.mediamtx_foreign) ? h.mediamtx_foreign.length : (h.mediamtx_foreign ? 1 : 0);
  $("#resratio").textContent = `${h.managed} yo'l · ${h.readers} tomoshabin · ${S.cams.length} kamera bazada`;
  $("#resupd").textContent = `So'nggi yangilanish: ${clock()}`;
  const ds = (icon, lbl, val, note, cls, hist, col) => `<div class="dstat"><div class="top">${ic(icon)}<span class="val ${cls}">${val}</span>${
      hist && hist.length > 1 ? spark(hist, col || "var(--mint)") : ""}</div><span class="lbl">${lbl}</span><span class="note">${note}</span></div>`;
  $("#sstats").innerHTML =
    ds("topo", "managed yo'l", h.managed, "kamera soniga emas, tomoshabinga bog'liq", "mint", HIST.man) +
    ds("users", "tomoshabin", h.readers, `faol sessiya · ${h.streams} oqim`, "", HIST.rd, "#93C5FD") +
    ds("up", "egress", `${h.egress_mbps.toFixed(0)}<u>Mbit/s</u>`, `${cap} dan · ${pct.toFixed(0)}%`, pct > 95 ? "rose" : pct > 80 ? "gold" : "", hEg, "#93C5FD") +
    ds("down", "kirish", `${ing.toFixed(0)}<u>Mbit/s</u>`, "kameralardan · runtime farqidan", "", hIn, "#93C5FD") +
    ds("zap", "o'girish", transcodes, "FFmpeg jarayoni · 8 NVENC limiti", transcodes ? "gold" : "mint", HIST.tr, "#FCD34D") +
    ds("globe", "begona yo'l", foreign, "mediamtx_foreign · 0 bo'lishi shart", foreign ? "rose" : "mint") +
    ds("therm", "issiq yo'l", `${h.warm}<u>/256</u>`, "sub · 10 daqiqa saqlanadi", "") +
    ds("pulse", "p95 API", p95 === null ? "—" : `${p95}<u>ms</u>`, "shu brauzerning so'rovlari", p95 > 1000 ? "gold" : "", HIST.p95, "#FCA5A5");

  const kv = (rows) => rows.map(([k, pill, v, pk]) =>
    `<div class="r"><span class="kk">${k}</span>${pill ? `<span class="okp ${pk || ""}">${pill}</span>` : ""}<span class="v">${v}</span></div>`).join("");
  const sw = (st.health || h.health || {});
  const sn = h.snapshots || {};
  const swSlow = sw.duration_ms > 45000;
  $("#sjobs").innerHTML = kv([
    ["health · 60 s", swSlow ? "SEKIN" : "OK", `${sw.checked || 0} manzil · ${sw.online || 0} tirik · ${((sw.duration_ms || 0) / 1000).toFixed(1)} s`, swSlow ? "warn" : ""],
    ["snapshot tsikli", sn.total ? "OK" : "—", sn.total ? `${sn.total} ta · ${((sn.duration_ms || 0) / 1000).toFixed(1)} s` : "hali yo'q", sn.total ? "" : "idle"],
    ["MediaMTX", h.mediamtx ? "OK" : "YO'Q", h.mediamtx ? "tirik" : "yiqilgan", h.mediamtx ? "" : "bad"],
    ["SSE obunachi", "OK", h.sse_subscribers],
    ["egress tarixi", "", spark(hEg, "var(--blue)") || "—"],
  ]);
  const jobsOk = h.mediamtx && !swSlow;
  $("#sjobs-st").innerHTML = `<i class="dot" style="background:${jobsOk ? "var(--d-on)" : "var(--d-stall)"}"></i>${jobsOk ? "Barchasi OK" : "Diqqat kerak"}`;
  $("#sjobs-st").className = "r " + (jobsOk ? "ok" : "warn");

  // MediaMTX tugunlari: kameralar holati donut + tugunlar
  const cnt = (s) => S.cams.filter((c) => c.state === s).length;
  const nodes = S.nodes || [];
  const nodesOk = nodes.every((n) => n.status === "online");
  $("#snodes").innerHTML = `<div class="donut-wrap">${donut([
      {v: cnt("online"), color: "var(--d-on)"}, {v: cnt("stalled"), color: "var(--d-stall)"},
      {v: cnt("offline"), color: "var(--d-off)"}, {v: cnt("unknown") + cnt("disabled"), color: "var(--d-unk)"}],
      S.cams.length, "kamera")}
    <div class="dleg"><div><i style="background:var(--d-on)"></i>Onlayn<b>${cnt("online")}</b></div>
      <div><i style="background:var(--d-stall)"></i>Muzlagan<b>${cnt("stalled")}</b></div>
      <div><i style="background:var(--d-off)"></i>Offline<b>${cnt("offline")}</b></div>
      <div><i style="background:var(--d-unk)"></i>Noma'lum<b>${cnt("unknown") + cnt("disabled")}</b></div></div></div>
    ${nodes.map((n) => `<div class="sect">${esc(n.name)}<span class="tag ${n.status === "online" ? "good" : n.status === "degraded" ? "mid" : "bad"}">${esc(n.status)}</span></div>
      <div class="meta">${esc(n.api_base || "127.0.0.1:9997")}${n.pending_paths ? ` · ${n.pending_paths} ortiqcha yo'l` : ""}</div>`).join("")}
    <div class="mini3"><div><span>RTSP yo'llar</span><b>${h.managed}</b></div><div><span>Faol oqimlar</span><b>${h.streams}</b></div>
      <div><span>Mbit/s egress</span><b>${h.egress_mbps.toFixed(0)}</b></div></div>`;
  $("#snodes-st").innerHTML = `<i class="dot" style="background:${nodesOk ? "var(--d-on)" : "var(--d-stall)"}"></i>${nodesOk ? "Onlayn" : "Degradatsiya"}`;
  $("#snodes-st").className = "r " + (nodesOk ? "ok" : "warn");

  // Disk: data/ tarkibi
  const d = st.disk || {};
  const snapMb = +d.snapshots_mb || 0, dbMb = +d.db_mb || 0, logMb = +d.log_mb || 0, totMb = snapMb + dbMb + logMb;
  const mb = (v) => v >= 1024 ? (v / 1024).toFixed(1) + " GB" : v.toFixed(1) + " MB";
  $("#sdisk").innerHTML = `<div class="donut-wrap">${donut([
      {v: snapMb, color: "var(--d-on)"}, {v: dbMb, color: "var(--blue)"}, {v: logMb, color: "var(--d-stall)"}],
      mb(totMb).split(" ")[0], mb(totMb).split(" ")[1] + " · data/")}
    <div class="dleg"><div><i style="background:var(--d-on)"></i>Suratlar<b>${mb(snapMb)}</b></div>
      <div><i style="background:var(--blue)"></i>cameras.db<b>${mb(dbMb)}</b></div>
      <div><i style="background:var(--d-stall)"></i>Jurnal<b>${mb(logMb)}</b></div>
      <div><i style="background:var(--d-unk)"></i>Hodisalar<b>30 kun</b></div></div></div>
    <div class="sect">Jildlar hajmi</div>
    <div class="kv">${kv([
      ["suratlar", "", `${d.snapshots_files ?? "—"} fayl · ${mb(snapMb)}`],
      ["cameras.db", "", mb(dbMb)],
      ["jurnal", "", `${mb(logMb)} · aylanma 5 MB×3`],
    ])}</div>`;
  $("#sdisk-st").innerHTML = `<i class="dot" style="background:var(--d-on)"></i>Hisoblandi`;

  // Registratorlarga ulanishlar
  const g = {};
  S.cams.forEach((c) => {
    if (!c.ip || c.state === "offline") return;
    const n = camRt(c).sessions;
    if (n) g[c.ip] = (g[c.ip] || 0) + n;
  });
  const conn = Object.entries(g).sort((a, b) => b[1] - a[1]);
  const crowded = conn.filter(([, n]) => n > 6).length;
  const totalSess = conn.reduce((a, [, n]) => a + n, 0);
  $("#sconn").innerHTML = `<div class="mini3"><div><span>Unikal IP</span><b>${conn.length}</b></div>
      <div><span>Jami sessiya</span><b>${totalSess}</b></div>
      <div><span>DVR chegarasi</span><b class="${crowded ? "bad" : "ok"}">${crowded}</b></div></div>
    <div class="sect">Faol ulanishlar</div>
    ${conn.length ? `<div class="kv">${kv(conn.slice(0, 8).map(([ip, n]) => [`<span class="mono">${esc(ip)}</span>`, "",
      `${n} sessiya <i class="dot sm" style="background:${n > 6 ? "var(--d-stall)" : "var(--d-on)"};margin-left:8px"></i>`]))}</div>`
      : `<div class="meta" style="padding:6px 0">Faol ulanish yo'q — hozir hech kim ko'rmayapti.</div>`}`;
  $("#sconn-st").innerHTML = `<i class="dot" style="background:${crowded ? "var(--d-stall)" : "var(--d-on)"}"></i>${crowded ? "Limitga yaqin" : "Barqaror"}`;
  $("#sconn-st").className = "r " + (crowded ? "warn" : "ok");

  // Muzlagan oqimlar
  const stalled = st.stalled || [];
  $("#sstall").innerHTML = stalled.length
    ? `<div class="kv">${stalled.map((s) => `<div class="r"><i class="dot" style="background:var(--d-stall)"></i><span class="kk mono">${esc(s)}</span><span class="v">bayt kelmayapti</span></div>`).join("")}</div>
       <div class="tip warn" style="margin-top:12px">${ic("warn")}<span><b>Reconciler</b> 30 soniyada bir tekshiradi; tiklanmasa «Xato qayerda?» sahifasida yo'lni qayta ulang.</span></div>`
    : `<div class="empty-ok"><span class="tile-i idle">${ic("snow", "lg")}</span><b>Muzlagan oqim yo'q.</b><p>Barcha oqimlar barqaror ishlayapti.</p></div>
       <div class="tip ok">${ic("info")}<span><b>Yaxshi holat.</b> Reconciler oxirgi 30 soniyada muzlagan oqimlar aniqlamadi.</span></div>`;
  $("#sstall-st").innerHTML = `<i class="dot" style="background:${stalled.length ? "var(--d-stall)" : "var(--d-on)"}"></i>${stalled.length ? stalled.length + " muzlagan" : "Muammo yo'q"}`;
  $("#sstall-st").className = "r " + (stalled.length ? "warn" : "ok");
}

/* ═════════ hodisalar (SSE) ═════════ */
$$("#efilt .pill[data-e]").forEach((b) => (b.onclick = () => {
  S.evFilt = b.dataset.e;
  $$("#efilt .pill[data-e]").forEach((x) => x.classList.toggle("sel", x === b));
  renderFeed();
}));
$("#epause").onclick = () => {
  S.evPause = !S.evPause;
  $("#epause").innerHTML = S.evPause ? `${ic("play", "sm")}<span>Davom etish</span>` : `${ic("pause", "sm")}<span>Pauzaga qo'yish</span>`;
  $("#evlive").className = "live" + (S.evPause ? " paused" : "");
  $("#evlive").innerHTML = `<i class="dot sm ${S.evPause ? "" : "s-online"}"></i>${S.evPause ? "Pauzada" : "Jonli oqim"}`;
};
$("#eclear").onclick = () => { S.evlog = []; S.evN = 0; $("#ne").textContent = ""; renderFeed(); };

function pushEv(kind, body, id) {
  if (S.evPause && (kind === "state" || kind === "snapshot")) return;
  S.evlog.unshift({t: clock(), kind, body, id});
  if (S.evlog.length > 300) S.evlog.pop();
  $("#ne").textContent = ++S.evN;
  if (S.page === "ev") renderFeed(true);
}
const EV_ICON = {state: ["pulse", ""], snapshot: ["image", "warn"], amal: ["check", "ok"], xato: ["alert", "bad"]};
function renderFeed(anim) {
  if (S.page !== "ev") return;
  const l = S.evlog.filter((e) => S.evFilt === "all" || e.kind === S.evFilt).slice(0, 120);
  $("#evn").textContent = `${S.evlog.length} ta hodisa`;
  $("#feed").innerHTML = `<div class="ev h"><span>Vaqt</span><span>Turi</span><span>Kamera / manba</span><span>Tavsif</span><span></span></div>` +
    (l.length ? l.map((e, i) => {
      const cam = e.id != null ? S.byId.get(e.id) : null;
      const body = cam ? e.body.replace(/^<b>.*?<\/b>\s*/, "") : e.body;
      const [icon, k] = EV_ICON[e.kind] || ["more", "idle"];
      return `<button class="ev${anim && !i ? " new" : ""}" ${e.id != null ? `data-id="${e.id}"` : ""}>
      <span class="ev-t">${e.t}</span>
      <span class="ev-k ${e.kind}"><span class="tile-i ${k}">${ic(icon, "sm")}</span><span class="tg">${e.kind}</span></span>
      <span class="ev-c">${cam ? `${ic("cam")}${esc(cam.name)}<span>${esc(cam.ip || "")}</span>` : `<span>tizim</span>`}</span>
      <span class="ev-b">${body}</span>${ic("chev-r", "sm chev")}</button>`;
    }).join("") : `<div class="empty" style="border:none">Bu turdagi hodisa hali yo'q.</div>`);
  $$("#feed .ev[data-id]").forEach((d) => (d.onclick = () => openDiag(+d.dataset.id)));
  const c = (k) => S.evlog.filter((e) => e.kind === k).length;
  const stat = (icon, k, lbl, val, note) => `<div class="stat ico tint-${k}"><span class="tile-i ${k}">${ic(icon, "lg")}</span>
    <div class="cnt"><span class="lbl">${lbl}</span><span class="val">${val}</span><span class="note">${note}</span></div></div>`;
  $("#evstats").innerHTML =
    stat("file", "blue", "Jami hodisalar", S.evlog.length, "shu sessiyada") +
    stat("image", "ok", "Snapshotlar", c("snapshot"), "surat yangilandi") +
    stat("alert", "bad", "Xatolar", c("xato"), "amal bajarilmadi") +
    stat("pulse", "warn", "Holat o'zgarishi", c("state"), "online / stalled / offline");
}

function startSSE() {
  let es;
  try { es = new EventSource("/api/v1/events"); } catch (e) { return; }
  es.addEventListener("state", (e) => {
    let d; try { d = JSON.parse(e.data); } catch (err) { return; }
    const cam = S.byId.get(d.id);
    const nom = cam ? cam.name : d.external_id || "#" + d.id;
    if (cam) cam.state = d.state;
    pushEv("state", `<b>${esc(nom)}</b> → ${esc(d.state)}`, d.id);
    if (d.state === "offline") toast(nom, "Ulanish uzildi — katak tozalandi", "bad");
    if (d.state === "stalled") toast(nom, "Oqim to'xtadi — bayt kelmayapti", "mid");
    // Poster darhol yo'qoladi — surat so'rovini kutmaymiz (INTEGRATION.md).
    if (S.page === "wall") drawWall(false);
    if (S.page === "verdict") drawVerdict();
    if (S.page === "topo") drawTopo();
    else if (S.page === "cams") drawCams();
    if (S.curId === d.id && S.page === "diag") drawDiag();
    drawNav();
  });
  es.addEventListener("snapshot", (e) => {
    let d; try { d = JSON.parse(e.data); } catch (err) { return; }
    const cam = S.byId.get(d.id);
    if (cam) cam.snapshot_at = d.at;
    pushEv("snapshot", `<b>${esc(cam ? cam.name : "#" + d.id)}</b> yangilandi`, d.id);
  });
  es.onerror = () => { es.close(); setTimeout(startSSE, 10000); };
}

/* ═════════ buyruq paneli ═════════ */
const PAGES = [["verdict", "Xato qayerda?"], ["speed", "Ochilish tezligi"],
  ["topo", "Topologiya"], ["cams", "Kameralar"], ["res", "Resurs"],
  ["out", "Uzilishlar"], ["wall", "Devor"], ["ev", "Hodisalar"],
  ["scan", "Qurilma qo'shish"]];
let palI = 0, palR = [];
function openPal() { $("#pal").classList.add("on"); $("#palq").value = ""; $("#palq").focus(); palFill(); }
function closePal() { $("#pal").classList.remove("on"); }
$("#palopen").onclick = openPal;
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPal(); return; }
  if (e.key === "Escape") closePal();
  if (!$("#pal").classList.contains("on")) return;
  if (e.key === "ArrowDown") { e.preventDefault(); palI = Math.min(palI + 1, palR.length - 1); palMark(); }
  if (e.key === "ArrowUp") { e.preventDefault(); palI = Math.max(palI - 1, 0); palMark(); }
  if (e.key === "Enter" && palR[palI]) { palR[palI].go(); closePal(); }
});
$("#palq").oninput = () => { palI = 0; palFill(); };
function palFill() {
  const q = $("#palq").value.toLowerCase();
  palR = [...S.cams.filter((c) => !q || (c.name || "").toLowerCase().includes(q) ||
      (c.ip || "").includes(q) || (c.external_id || "").includes(q) ||
      (c.region || "").toLowerCase().includes(q))
    .map((c) => ({h: `<i class="dot" style="background:${DOT[c.state]}"></i>${esc(c.name)}`,
      s: `${esc(c.region)} · ${esc(c.ip || "")}`, go: () => openDiag(c.id)})),
    ...PAGES.filter(([, n]) => !q || n.toLowerCase().includes(q))
      .map(([p, n]) => ({h: esc(n), s: "sahifa", go: () => go(p)}))].slice(0, 9);
  $("#pall").innerHTML = palR.length ? palR.map((r, i) =>
    `<div class="pal-i ${i === palI ? "cur" : ""}" data-i="${i}">${r.h}<span class="h">${r.s}</span></div>`
  ).join("") : `<div class="pal-i" style="color:var(--faint)">Hech narsa topilmadi</div>`;
  $$(".pal-i[data-i]").forEach((d) => (d.onclick = () => { palR[+d.dataset.i].go(); closePal(); }));
}
function palMark() { $$(".pal-i").forEach((d, i) => d.classList.toggle("cur", i === palI)); }
$("#pal").onclick = (e) => { if (e.target.id === "pal") closePal(); };

/* ═════════ skan ═════════ */
let scanChs = new Map(), scanMeta = null, scanEs = null;
$("#sgo").onclick = async () => {
  const ip = $("#sip").value.trim();
  if (!ip) { toast("IP kerak", "Registrator manzilini kiriting", "bad"); return; }
  if (scanEs) { scanEs.close(); scanEs = null; }
  scanChs = new Map(); scanMeta = null;
  $("#sgo").disabled = true;
  $("#sprog").style.display = "block";
  $("#sprog i").style.width = "4%";
  $("#sinfo").innerHTML = "";
  $("#chg").innerHTML = `<div class="ch"><div class="ch-img"><div class="skel"></div></div>
    <div class="ch-b"><span class="n">tekshirilyapti…</span></div></div>`;
  $("#sfoot").innerHTML = "";
  try {
    const job = await api("/api/v1/devices/scan", {method: "POST", body: {
      ip, port: +$("#sport").value || 554,
      username: $("#slog").value.trim(), password: $("#spw").value, max_channels: 64}});
    // Qurilma pasporti parallel so'raladi — model/firmware kartada chiqadi.
    api(`/api/v1/devices/info?ip=${encodeURIComponent(ip)}` +
        `&username=${encodeURIComponent($("#slog").value.trim())}` +
        `&password=${encodeURIComponent($("#spw").value)}`)
      .then((info) => { if (scanMeta) drawScanInfo(info); else scanMeta = {info}; })
      .catch(() => {});
    scanEs = new EventSource(job.events);
    let seen = 0;
    scanEs.addEventListener("meta", (e) => {
      const m = JSON.parse(e.data);
      const pending = scanMeta && scanMeta.info;
      scanMeta = m;
      drawScanInfo(pending || null);
    });
    scanEs.addEventListener("channel", (e) => {
      const ch = JSON.parse(e.data);
      scanChs.set(ch.channel, ch);
      seen++;
      $("#sprog i").style.width = Math.min(95, 8 + seen / 64 * 100) + "%";
      drawScanGrid();
    });
    const finish = (msg, bad) => {
      $("#sprog i").style.width = "100%";
      setTimeout(() => { $("#sprog").style.display = "none"; }, 600);
      $("#sgo").disabled = false;
      if (msg) toast(bad ? "Skan xatosi" : "Skan tugadi", msg, bad ? "bad" : "");
      if (scanEs) { scanEs.close(); scanEs = null; }
    };
    scanEs.addEventListener("done", (e) => {
      const d = JSON.parse(e.data);
      const el = $("#chn");
      if (el) el.textContent = `${d.live_channels} ta faol (${d.device})`;
      finish(`${d.live_channels} ta faol kanal topildi`);
    });
    scanEs.addEventListener("error", (e) => {
      if (e.data) { try { finish(JSON.parse(e.data).message, true); return; } catch (err) {} }
      finish("Ulanish uzildi", true);
    });
  } catch (e) {
    $("#sgo").disabled = false;
    $("#sprog").style.display = "none";
    $("#chg").innerHTML = "";
    toast("Skan boshlanmadi", e.message, "bad");
  }
};
function drawScanInfo(info) {
  const m = scanMeta || {};
  $("#sinfo").innerHTML = `<div class="card"><h3>Qurilma</h3><dl class="kv">
    <dt>Ishlab chiqaruvchi</dt><dd>${esc((info && info.manufacturer) || m.vendor_name || "—")}</dd>
    <dt>Model</dt><dd>${esc((info && info.model) || "—")}</dd>
    <dt>Firmware</dt><dd>${esc((info && info.firmware) || "—")}</dd>
    <dt>Seriya</dt><dd>${esc((info && info.serial) || "—")}</dd>
    <dt>Shablon</dt><dd>${esc(m.vendor || "—")}</dd>
    <dt>Kanallar</dt><dd id="chn">tekshirilyapti…</dd></dl></div>`;
}
function drawScanGrid() {
  const chs = [...scanChs.values()].sort((a, b) => a.channel - b.channel);
  $("#chg").innerHTML = chs.map((ch) => `<div class="ch ${ch.ok ? "" : "dead"} ${
      ch._pick ? "pick" : ""}" data-ch="${ch.channel}">
    <div class="ch-img">${ch.ok && ch.snapshot_url
      ? `<img src="${esc(ch.snapshot_url)}" loading="lazy"
           onerror="this.replaceWith('kadr yo\\'q')">`
      : `<span>${ch.ok ? "kadr yo'q" : "signal yo'q"}</span>`}</div>
    <div class="ch-b"><span class="cbx">✓</span><span class="n">${ch.channel}-kanal</span>
      <span class="c">${esc(ch.codec || "—")}${ch.resolution ? " · " + esc(ch.resolution) : ""}</span>
    </div></div>`).join("");
  $$("#chg .ch:not(.dead)").forEach((el) => (el.onclick = () => {
    const ch = scanChs.get(+el.dataset.ch);
    ch._pick = !ch._pick;
    drawScanGrid();
  }));
  scanTally();
}
function scanTally() {
  const picked = [...scanChs.values()].filter((c) => c._pick);
  const live = [...scanChs.values()].filter((c) => c.ok).length;
  $("#sfoot").innerHTML = picked.length
    ? `<div class="card" style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
      <span><b>${picked.length} ta kanal</b> tanlandi</span>
      <span style="color:var(--faint);font-size:12.5px">Saqlangach har biri o'z
        <span class="mono">id</span> sini oladi — asosiy tizim shu id bilan murojaat qiladi.</span>
      <button class="btn" style="margin-left:auto" id="sadd">Qo'shish</button></div>`
    : `<div class="empty">Kadrlarga qarab kerakli kanallarni tanlang${
        live ? ` (${live} ta faol)` : ""}.</div>`;
  if (picked.length) $("#sadd").onclick = () => scanAdd(picked);
}
async function scanAdd(picked) {
  const region = $("#sreg").value.trim();
  if (!region) { toast("Hudud kerak", "Kamera qaysi hududga tegishli?", "bad");
    $("#sreg").focus(); return; }
  const prefix = $("#spre").value.trim() || region;
  $("#sadd").disabled = true;
  let ok = 0;
  for (const ch of picked) {
    try {
      await api("/api/v1/admin/cameras", {method: "POST", body: {
        name: `${prefix} ${ch.channel}-kanal`, region,
        source_type: "rtsp", ip: $("#sip").value.trim(),
        port: +$("#sport").value || 554,
        username: $("#slog").value.trim(), password: $("#spw").value || null,
        vendor: (scanMeta && scanMeta.vendor) || "boshqa",
        rtsp_path: ch.rtsp_path || "/stream1"}});
      ok++;
      ch._pick = false;
    } catch (e) {
      pushEv("xato", `<b>${ch.channel}-kanal</b> saqlanmadi · ${esc(e.message)}`);
    }
  }
  toast("Qo'shildi", `${ok}/${picked.length} kanal saqlandi`, ok === picked.length ? "" : "mid");
  pushEv("amal", `skan · ${ok} ta kamera qo'shildi`);
  drawScanGrid();
  loadCams();
}
