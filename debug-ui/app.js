"use strict";
/* Nigoh — servis konsoli (v2). Xarita yo'q: xarita, rollar va dashboard
   asosiy tizimda. Bu panel bitta savolga javob beradi: "backend'da
   xatomi yoki kamerada?" — jadval, devor, signal zanjiri, tizim
   ko'rsatkichlari, SSE hodisalari va qurilma skani.

   Hamma ma'lumot haqiqiy API'dan:
     /api/v1/admin/cameras   ro'yxat (to'liq maydonlar)
     /api/v1/cameras/status  snapshot_at va yangi holat
     /api/v1/admin/runtime   slug -> baytlar/tomoshabinlar (tezlik farqdan)
     /api/v1/events          SSE — holat o'zgarishlari
     /health, /admin/status  tizim sahifasi                              */

/* ═════════ yordamchi ═════════ */
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const age = (s) => s == null || s < 0 ? "yo'q"
  : s < 60 ? Math.round(s) + " s"
  : s < 3600 ? Math.round(s / 60) + " daq" : Math.round(s / 3600) + " soat";
const clock = () => new Date().toTimeString().slice(0, 8);
const LBL = {online:"Ishlayapti", stalled:"To'xtagan", offline:"O'chgan",
             unknown:"Tekshirilmagan", disabled:"O'chirilgan"};
/* Muammo-yo'naltirilgan ko'rinish: e'tibor talab qiladigan holatlar va
   ularning jadvaldagi tartibi — yomoni tepada. */
const PROB = new Set(["offline", "stalled", "unknown"]);
const RANK = {offline:0, stalled:1, unknown:2, disabled:3, online:4};

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

/* ═════════ yuqori global chiziq ═════════

   Sahifadan qat'i nazar ko'rinadi. Hamma raqam haqiqiy manbadan:
   kirish — /admin/runtime baytlari farqidan, uzilish/o'chiq/mavjudlik —
   /admin/uptime dan, p95 esa shu brauzerning o'z so'rovlaridan. Soxta
   ko'rsatkich yo'q: o'lchanmaydigan narsa umuman chiqmaydi.          */

const API_LAT = [];                      // oxirgi so'rovlar kechikishi (ms)
function noteApi(ms) {
  API_LAT.push(ms);
  if (API_LAT.length > 200) API_LAT.shift();
}
function apiP95() {
  if (!API_LAT.length) return null;
  const a = [...API_LAT].sort((x, y) => x - y);
  return Math.round(a[Math.min(a.length - 1, Math.round((a.length - 1) * 0.95))]);
}

async function loadFleetStats() {
  try {
    // node kesimi eng kichik javob (bir necha qator), lekin park bo'yicha
    // yig'indini beradi — shu yetadi.
    const r = await api("/api/v1/admin/uptime?hours=24&group_by=node");
    const g = r.groups || [];
    S.fleet = {
      outages: g.reduce((a, x) => a + x.outages, 0),
      offline: g.reduce((a, x) => a + x.offline_seconds, 0),
      cameras: g.reduce((a, x) => a + x.cameras, 0),
    };
    const span = Math.max(1, S.fleet.cameras * 86400);
    S.fleet.uptime = Math.round(1000 * (span - S.fleet.offline) / span) / 10;
  } catch (e) { S.fleet = null; }
  drawTopbar();
}

function drawTopbar() {
  const cams = S.cams || [];
  if (!cams.length) return;
  const cnt = (st) => cams.filter((c) => c.state === st).length;
  const on = cnt("online"), prob = cams.filter((c) => PROB.has(c.state)).length;
  const pctOn = cams.length ? Math.round(1000 * on / cams.length) / 10 : 0;

  $("#tbdot").className = "dot s-" + (prob ? (cnt("offline") ? "offline" : "stalled")
                                           : "online");
  $("#tbtitle").textContent = prob
    ? `${prob} kamera muammoli · ${pctOn}% flot onlayn`
    : `Hammasi joyida · ${cams.length} kamera`;
  $("#tbbreak").textContent =
    `${on} online · ${cnt("stalled")} stalled · ${cnt("offline")} offline`
    + (cams.filter((c) => !c.region).length
        ? ` · ${cams.filter((c) => !c.region).length} hududsiz` : "");

  const inMbps = cams.reduce((a, c) => a + camRt(c).inMbps, 0);
  const f = S.fleet, p95 = apiP95();
  const cell = (l, v) => `<div class="tb-s"><div class="l">${l}</div>
    <div class="v">${v}</div></div>`;
  $("#tbstats").innerHTML =
    cell("Kirish", `${inMbps.toFixed(1)}<u> Mb/s</u>`) +
    cell("Chiqish", S.health
      ? `${Math.round(S.health.egress_mbps)}<u> Mb/s</u>` : "—") +
    cell("Uzilish (24s)", f ? f.outages : "—") +
    cell("O'chiq (24s)", f ? durHM(f.offline) : "—") +
    cell("Mavjudlik", f ? `${f.uptime}<u>%</u>` : "—") +
    cell("p95 API", p95 === null ? "—" : `${p95}<u> ms</u>`);
}

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
  filt: "all", sortK: "state", sortD: 1, picked: new Set(),
  wallN: 9, wallMode: "all",
  evlog: [], evFilt: "all", evPause: false, evN: 0,
  gBy: "region", gHours: 24, groups: null,      // guruhlar sahifasi
  tHours: 24, stat: null, worst: null,          // tahlil sahifasi
  diagDay: 0, hist: null,                       // kamera tahlili
  fleet: null,                                  // yuqori chiziq agregati
  curId: null, page: "home",
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
  };
}
function snapAge(cam) {
  if (!cam.snapshot_at) return -1;
  const t = Date.parse(cam.snapshot_at);
  return isNaN(t) ? -1 : Math.max(0, (Date.now() - t) / 1000);
}

/* ═════════ kirish ═════════ */
function showGate() { $("#gate").classList.remove("hidden"); $("#app").classList.remove("on"); }

$("#glogin").onclick = async () => {
  $("#gerr").textContent = "";
  try {
    await api("/api/v1/auth/login", {method: "POST",
      body: {username: $("#u").value.trim(), password: $("#p").value}});
    enter();
  } catch (e) { $("#gerr").textContent = e.message; }
};
$("#p").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#glogin").click(); });
$("#out").onclick = async () => {
  try { await api("/api/v1/auth/logout", {method: "POST"}); } catch (e) {}
  location.reload();
};

let entered = false;
function enter() {
  if (entered) return;
  entered = true;
  $("#gate").classList.add("hidden");
  $("#app").classList.add("on");
  loadCams(); pollRuntime(); loadStatus(); loadFleetStats(); startSSE();
  setInterval(loadCams, 15000);
  setInterval(pollRuntime, 3000);
  setInterval(loadStatus, 30000);
  // Uzilishlar agregati sekin o'zgaradi — daqiqada bir marta yetadi.
  setInterval(loadFleetStats, 60000);
  setInterval(() => { if (S.page === "sys") drawSys(); }, 3000);
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
    drawCams(); drawNav(); drawTopbar();
    if (S.page === "home") drawHome();
    if (S.page === "wall") drawWall(false);
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
    if (S.page === "home") drawHome();
    if (S.page === "wall") updateWallFoot();
    if (S.page === "diag" && S.curId != null) drawDiagCards();
    drawTopbar();
  } catch (e) {}
}

async function loadStatus() {
  if (!entered) return;
  try {
    S.status = await api("/api/v1/admin/status");
    S.health = await api("/health");
    S.nodes = (await api("/api/v1/admin/nodes")).nodes;
    // Bosh sahifa uchun so'nggi uzilishlar — "online" shovqini kerak emas.
    try {
      S.recentEv = ((await api("/api/v1/admin/events?limit=60")).events || [])
        .filter((e) => e.kind !== "online");
    } catch (e) {}
    $("#footinfo").textContent =
      `${S.nodes.length} tugun · egress ${Math.round(S.health.egress_mbps)} Mbit/s`;
    if (S.page === "sys") drawSys();
    if (S.page === "home") drawHome();
  } catch (e) {}
}

/* ═════════ navigatsiya ═════════ */
function go(p) {
  S.page = p;
  $$(".nav-item").forEach((b) => b.classList.toggle("sel", b.dataset.p === p));
  $$(".page").forEach((s) => s.classList.toggle("on", s.id === "p-" + p));
  if (p === "wall") drawWall(true); else stopWall();
  if (p !== "diag") closeLive();          // sahifadan chiqilsa video to'xtaydi
  if (p === "home") drawHome();
  if (p === "sys") { drawSys(); loadStatus(); }
  if (p === "ev") renderFeed();
  if (p === "groups") { drawGroups(); loadGroups(); }
  if (p === "stat") { drawStat(); loadStat(); }
}
$$(".nav-item").forEach((b) => (b.onclick = () => go(b.dataset.p)));
$("#dback").onclick = () => go("cams");

function drawNav() {
  $("#nc").textContent = S.cams.length;
  const bad = S.cams.filter((c) => c.state === "offline" || c.state === "stalled").length;
  $("#nw").textContent = bad ? bad + "!" : S.cams.filter((c) => c.state === "online").length;
  $("#nw").className = "ct" + (bad ? " alert" : "");
  const prob = S.cams.filter((c) => PROB.has(c.state)).length;
  $("#nh").textContent = prob ? prob + "!" : "✓";
  $("#nh").className = "ct" + (prob ? " alert" : "");
}

/* ═════════ holat (bosh sahifa) ═════════ */
function drawHome() {
  if (S.page !== "home") return;
  const h = S.health, st = S.status || {};
  const on = S.cams.filter((c) => c.state === "online").length;
  const probs = S.cams.filter((c) => PROB.has(c.state))
    .sort((a, b) => (RANK[a.state] ?? 9) - (RANK[b.state] ?? 9));
  const totalIn = S.cams.reduce((s, c) => s + camRt(c).inMbps, 0);

  $("#hsum").textContent = probs.length
    ? `${probs.length} ta kamera e'tibor talab qiladi`
    : "Hammasi joyida";

  const tile = (lbl, val, sub) => `<div class="stat"><div class="lbl">${lbl}</div>
    <div class="val">${val}</div><div class="sub">${sub}</div></div>`;
  $("#hstats").innerHTML =
    tile("Kameralar", `${on}<u> / ${S.cams.length}</u>`, "ishlayapti") +
    tile("Muammoli", probs.length || "0", probs.length ? "quyida ro'yxati" : "yo'q") +
    tile("MediaMTX", h && h.mediamtx ? "tirik" : "yiqilgan",
         `${(S.nodes || []).length} tugun`) +
    tile("Kirish", `${totalIn.toFixed(1)}<u> Mbit/s</u>`, "kameralardan") +
    tile("Chiqish", h ? `${Math.round(h.egress_mbps)}<u> Mbit/s</u>` : "—",
         h ? `${h.readers} tomoshabin` : "—");

  $("#hprob").innerHTML = probs.length
    ? `<div class="card" style="margin-top:14px"><h3>E'tibor talab qiladi</h3>${
        probs.slice(0, 25).map((c) => `<div class="prob-row" data-id="${c.id}">
          <img src="/api/v1/cameras/${c.id}/snapshot?stale=1" loading="lazy"
            onerror="this.style.visibility='hidden'">
          <i class="dot s-${c.state}"></i><b>${esc(c.name)}</b>
          <span class="meta">${esc(c.region)}${c.ip ? " · " + esc(c.ip) : ""}</span>
          <span class="meta" style="margin-left:auto">${LBL[c.state]}${
            c.state === "offline" && c.last_seen
              ? " · oxirgi: " + esc(String(c.last_seen).slice(5, 16).replace("T", " "))
              : ""}</span></div>`).join("")}</div>`
    : `<div class="allok">✓ Barcha kameralar ishlayapti</div>`;
  $$("#hprob .prob-row").forEach((r) => (r.onclick = () => openDiag(+r.dataset.id)));

  const evs = (S.recentEv || []).slice(0, 12);
  $("#hev").innerHTML = evs.length ? evs.map((e) => `<div class="hist-r">
      <span class="hist-t">${esc((e.ts || "").slice(5, 16))}</span>
      <i class="dot s-${e.kind === "offline" ? "offline"
        : e.kind === "stalled" ? "stalled" : "unknown"}"></i>
      <span>${esc(e.kind)}</span>
      <span style="color:var(--faint);margin-left:6px">${esc(e.slug || e.detail || "")}</span>
    </div>`).join("")
    : `<div style="color:var(--faint);font-size:13px">Uzilish qayd etilmagan.</div>`;

  const sw = (st.health || (h && h.health) || {});
  const sn = (h && h.snapshots) || {};
  $("#hjobs").innerHTML =
    `<dt>Health sweep</dt><dd>${sw.checked || 0} manzil · ${sw.online || 0} tirik</dd>
    <dt>Snapshot tsikli</dt><dd>${sn.total ? sn.total + " ta" : "hali yo'q"}</dd>
    <dt>SSE obunachi</dt><dd>${h ? h.sse_subscribers : "—"}</dd>`;
}

/* ═════════ kameralar jadvali ═════════ */
$$("#cfilt .chip").forEach((b) => (b.onclick = () => {
  S.filt = b.dataset.f;
  $$("#cfilt .chip").forEach((x) => x.classList.toggle("sel", x === b));
  drawCams();
}));
$("#csearch").oninput = () => drawCams();
$$("#p-cams thead th[data-s]").forEach((th) => (th.onclick = () => {
  const k = th.dataset.s;
  S.sortD = S.sortK === k ? -S.sortD : 1; S.sortK = k; drawCams();
}));

function tableRows() {
  const q = $("#csearch").value.toLowerCase();
  const key = (c) => {
    if (S.sortK === "state") return RANK[c.state] ?? 9;   // muammolilar tepada
    if (S.sortK === "inMbps") return camRt(c).inMbps;
    if (S.sortK === "readers") return camRt(c).readers;
    if (S.sortK === "snapAge") return snapAge(c);
    return c[S.sortK] ?? "";
  };
  const match = (c) => S.filt === "all" ? true
    : S.filt === "prob" ? PROB.has(c.state) : c.state === S.filt;
  return S.cams.filter((c) =>
      match(c) &&
      (!q || (c.name || "").toLowerCase().includes(q) ||
        (c.ip || "").includes(q) || (c.external_id || "").includes(q)))
    .sort((a, b) => {
      const x = key(a), y = key(b);
      const d = (typeof x === "number" ? x - y
        : String(x).localeCompare(String(y))) * S.sortD;
      return d || String(a.name || "").localeCompare(String(b.name || ""));
    });
}

function drawCams() {
  const rs = tableRows();
  $("#ctb").innerHTML = rs.map((c) => {
    const rt = camRt(c), a = snapAge(c);
    return `<tr data-id="${c.id}" class="${S.picked.has(c.id) ? "pick" : ""}">
    <td><span class="cbx">✓</span></td>
    <td><span class="st"><i class="dot s-${c.state}"></i>${LBL[c.state] || c.state}</span></td>
    <td class="name">${esc(c.name)}<div class="meta" style="font-size:11px">${esc(c.external_id || "")}</div></td>
    <td>${esc(c.region)}</td><td class="meta">${esc(c.ip || "—")}</td>
    <td class="meta">${c.codec ? esc(c.codec) + (c.sub_codec ? " · " + esc(c.sub_codec) : "") : "—"}</td>
    <td class="meta">${rt.inMbps ? rt.inMbps.toFixed(1) + " Mb/s" : "—"}</td>
    <td class="meta">${rt.readers || "—"}</td>
    <td class="meta">${c.state === "offline"
      ? '<span class="tag bad">berilmaydi</span>' : esc(age(a))}</td>
  </tr>`;}).join("");
  $$("#ctb tr").forEach((r) => {
    const id = +r.dataset.id;
    r.onclick = (e) => {
      if (e.target.closest(".cbx")) {
        S.picked.has(id) ? S.picked.delete(id) : S.picked.add(id);
        drawCams();
      } else openDiag(id);
    };
  });
  $("#cempty").innerHTML = rs.length ? "" : `<div class="empty">
    Bu filtrga mos kamera yo'q.<br>Filtrni kengaytiring yoki qidiruvni tozalang.</div>`;
  $("#cbulk").innerHTML = S.picked.size ? `<div class="bulk">
    <b>${S.picked.size} ta</b> tanlandi
    <span style="color:var(--faint);font-size:12.5px">Devorda sub oqim ochiladi</span>
    <button class="btn" style="margin-left:auto" id="bwall">Devorda ochish</button>
    <button class="btn ghost" id="bclr">Bekor</button></div>` : "";
  if (S.picked.size) {
    $("#bwall").onclick = () => { S.wallMode = "sel"; go("wall"); };
    $("#bclr").onclick = () => { S.picked.clear(); drawCams(); };
  }
  const cnt = (s) => S.cams.filter((c) => c.state === s).length;
  const probN = S.cams.filter((c) => PROB.has(c.state)).length;
  $$("#cfilt .chip").forEach((b) => {
    const f = b.dataset.f;
    b.querySelector(".n").textContent =
      f === "all" ? S.cams.length : f === "prob" ? probN : cnt(f);
  });
  const totalIn = S.cams.reduce((s, c) => s + camRt(c).inMbps, 0);
  $("#csum").textContent = `${S.cams.length} ta · ${cnt("online")} tasi ishlayapti · ` +
    (probN ? `${probN} ta muammoli · ` : "") +
    `${totalIn.toFixed(1)} Mbit/s kirish`;
  drawNav();
}
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && !/INPUT|TEXTAREA/.test(e.target.tagName)) {
    e.preventDefault(); go("cams"); $("#csearch").focus();
  }
});

/* ═════════ video pleyer (devor uchun) ═════════ */
const FAIL_MSG = "oqim ochilmadi";

/* Watchdog: oqim "ulangan" bo'lib turib qotib qolishi eng ko'p uchraydigan
   nosozlik, va uni na connectionState, na hls.js xatosi ko'rsatadi. Shuning
   uchun harakat o'lchanadi — WebRTC'da dekodlangan kadrlar, HLS'da
   currentTime. Uch tsikl (6 s) qimirlamasa oqim o'lik deb hisoblanadi. */
const WATCH_MS = 2000;
const WATCH_DEAD = 3;
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
  api(`/api/v1/cameras/${c.id}/stream?hevc=${HEVC_OK ? 1 : 0}`)
    .then((u) => { if (u.stream_url) fetch(u.stream_url, {cache: "no-store"})
      .catch(() => {}); })
    .catch(() => {});
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
      if (!tStream) return;
      const now = performance.now();
      api("/api/v1/metrics/open", {method: "POST", body: {
        camera_id: cam.id, mode: p.mode || "", transport: transport || "hls",
        stream_ms: Math.round(tStream - t0),
        signal_ms: tSignal ? Math.round(tSignal - tStream) : 0,
        frame_ms: Math.round(now - (tSignal || tStream)),
        total_ms: Math.round(now - t0),
      }}).catch(() => {});
    };

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
       oqim qotgan. Boshlanish paytida (paused / readyState past) hisob
       yuritilmaydi — aks holda har ochilish soxta uzilish bo'lardi. */
    function armHlsWatch(staleFn) {
      let prev = -1, still = 0;
      p.stopWatch();
      p.watch = setInterval(() => {
        if (staleFn()) { p.stopWatch(); return; }
        if (video.paused || video.readyState < 2) return;
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
        const hls = new Hls({lowLatencyMode: false, maxBufferLength: 12,
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
        hls.on(Hls.Events.ERROR, (_, d) =>
          console.log(`[hls] XATO: ${d.details} fatal=${d.fatal}`));
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
        let netFails = 0, mediaFails = 0, authReloads = 0;
        // Segment buferga tushdi — demak oqim tiklandi, ruxsat hisobini
        // nolga qaytaramiz (uzoq tomoshada chegara bekorga tugamasin).
        hls.on(Hls.Events.FRAG_BUFFERED, () => { authReloads = 0; });
        hls.on(Hls.Events.ERROR, (_, d) => {
          if (staleFn()) return;
          // Bufer to'xtashi fatal deb belgilanmaydi, lekin ekranni aynan
          // shu qotiradi — yuklashni turtib qo'yamiz.
          if (d.details === Hls.ErrorDetails.BUFFER_STALLED_ERROR) {
            hls.startLoad();
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
          if ((code === 401 || code === 403) && ++authReloads <= 5) {
            console.log(`[hls] ${code} — ruxsat sessiyasi eskirgan, master `
                        + `qayta yuklanmoqda (${authReloads}/5)`);
            hls.loadSource(url);
            hls.startLoad();
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


/* ═════════ guruhlar va tahlil (uzilishlar agregati) ═════════

   Ikkalasi ham /admin/uptime va /admin/outages/hourly dan oziqlanadi —
   hisob serverda, events jadvalidan. 5000 kamerani brauzerga tortib
   guruhlashning ma'nosi yo'q: guruh javobi 2-11 KB, kamera kesimidagi
   to'liq ro'yxat esa ~900 KB.                                        */

const HOURS_LBL = {24: "24 soat", 168: "7 kun", 720: "30 kun"};

/* Sekundlarni odam o'qiydigan davomiylikka: 0 bo'lsa chiziqcha. */
const durHM = (sec) => {
  if (!sec) return "—";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h}s ${m}d` : `${m}d`;
};

/* Uptime foizining rangi: 99% dan yuqori — normal, 95% gacha — e'tibor. */
const upColor = (p) => p >= 99 ? "var(--ok)" : p >= 95 ? "var(--warn)" : "var(--fail)";

async function loadGroups() {
  try {
    const r = await api(`/api/v1/admin/uptime?hours=${S.gHours}&group_by=${S.gBy}`);
    S.groups = r.groups || [];
  } catch (e) { S.groups = []; toast("Guruhlar olinmadi", e.message, "bad"); }
  drawGroups();
}

function drawGroups() {
  if (S.page !== "groups") return;
  const gs = S.groups;
  if (gs === null) { $("#gsum").textContent = "yuklanmoqda…"; return; }
  const outages = gs.reduce((a, g) => a + g.outages, 0);
  $("#gsum").textContent =
    `${gs.length} ta guruh · ${outages} uzilish · ${HOURS_LBL[S.gHours]}`;
  $("#ng").textContent = outages || "✓";
  $("#ng").className = "ct" + (outages ? " alert" : "");

  $("#gtb").innerHTML = gs.map((g) => {
    const n = Math.max(1, g.cameras);
    const w = (v) => (100 * v / n).toFixed(2) + "%";
    // Hududsiz kameralar alohida guruh — ular xaritada ham, hisobotda
    // ham yo'qoladi, shuning uchun qizil bilan belgilanadi.
    const orphan = g.key === "belgilanmagan";
    return `<tr data-k="${esc(g.key)}">
      <td class="name" style="color:${orphan ? "var(--fail)" : "var(--text)"}">${esc(g.key)}</td>
      <td class="meta">${g.cameras}</td>
      <td><div class="bar">
        <i style="width:${w(g.online)};background:var(--ok)"></i>
        <i style="width:${w(g.offline)};background:var(--fail)"></i>
        <i style="width:${w(g.unknown + g.disabled)};background:var(--idle)"></i>
      </div></td>
      <td class="meta" style="color:${upColor(g.uptime_pct)}">${g.uptime_pct}%</td>
      <td class="meta" style="color:${g.outages ? "var(--warn)" : "var(--faint)"}">${g.outages || "—"}</td>
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
      $$("#cfilt .chip").forEach((b) => b.classList.toggle("sel", b.dataset.f === "prob"));
      go("cams");
      drawCams();
    };
  });
  $("#gempty").innerHTML = gs.length ? "" :
    `<div class="empty">Bu davrda ma'lumot yo'q.<br>Hodisalar 30 kun saqlanadi.</div>`;
}

async function loadStat() {
  // Zona mijozdan boradi: hodisalar bazada UTC'da, "cho'qqi 08:00 da"
  // degan xulosa esa faqat mahalliy vaqtda ma'noga ega.
  const tz = -new Date().getTimezoneOffset();
  try {
    const [hist, worst] = await Promise.all([
      api(`/api/v1/admin/outages/hourly?hours=${S.tHours}&tz_offset_minutes=${tz}`),
      api(`/api/v1/admin/uptime?hours=${S.tHours}&limit=25`),
    ]);
    S.stat = hist;
    S.worst = worst.cameras || [];
  } catch (e) { S.stat = null; toast("Tahlil olinmadi", e.message, "bad"); }
  drawStat();
}

function drawStat() {
  if (S.page !== "stat") return;
  const st = S.stat;
  if (!st) { $("#tsum").textContent = "ma'lumot yo'q"; return; }
  $("#tsum").textContent = `${st.total} uzilish · ${HOURS_LBL[S.tHours]}`;
  $("#nt").textContent = st.total || "✓";
  $("#nt").className = "ct" + (st.total ? " alert" : "");

  const max = Math.max(1, ...st.hourly);
  const peak = st.peak || {from_hour: 0, to_hour: 0, outages: 0};
  // Cho'qqi oyna sutka aylanasidan o'tishi mumkin (23:00–02:00).
  const inPeak = (h) => {
    const span = (peak.to_hour - peak.from_hour + 24) % 24 || 3;
    return (h - peak.from_hour + 24) % 24 < span;
  };
  $("#thist").innerHTML = st.hourly.map((v, h) => `<i class="hbar"
    style="height:${Math.max(2, Math.round(v / max * 100))}%;
           background:${inPeak(h) ? "var(--fail)" : v > max * 0.6 ? "var(--warn)" : "var(--signal)"}"
    title="${pad2(h)}:00 — ${v} uzilish"></i>`).join("");
  $("#thistx").innerHTML = st.hourly.map((_, h) =>
    `<span>${h % 3 === 0 ? pad2(h) : ""}</span>`).join("");
  $("#tpeak").textContent = peak.outages
    ? `cho'qqi ${pad2(peak.from_hour)}:00–${pad2(peak.to_hour)}:00 · ${peak.outages} uzilish`
    : "cho'qqi yo'q";

  const rows = S.worst || [];
  $("#ttb").innerHTML = rows.map((c) => `<tr data-id="${c.id}">
    <td class="name">${esc(c.name)}</td>
    <td class="meta">${esc(c.region || "—")}</td>
    <td><span class="st"><i class="dot s-${c.state}"></i>${LBL[c.state] || c.state}</span></td>
    <td class="meta" style="color:${upColor(c.uptime_pct)}">${c.uptime_pct}%</td>
    <td class="meta" style="color:${c.outages ? "var(--warn)" : "var(--faint)"}">${c.outages || "—"}</td>
    <td class="meta">${durHM(c.offline_seconds)}</td>
    <td class="meta">${esc((c.last_offline_at || "").slice(5, 16).replace("T", " ") || "—")}</td>
  </tr>`).join("");
  $$("#ttb tr").forEach((tr) => (tr.onclick = () => openDiag(+tr.dataset.id)));
  $("#tempty").innerHTML = rows.length ? "" :
    `<div class="empty">Bu davrda uzilish qayd etilmagan.</div>`;
}

const pad2 = (n) => (n < 10 ? "0" : "") + n;

$$("#gby .chip").forEach((b) => (b.onclick = () => {
  S.gBy = b.dataset.g;
  $$("#gby .chip").forEach((x) => x.classList.toggle("sel", x === b));
  S.groups = null; drawGroups(); loadGroups();
}));
$$("#ghours .chip").forEach((b) => (b.onclick = () => {
  S.gHours = +b.dataset.h;
  $$("#ghours .chip").forEach((x) => x.classList.toggle("sel", x === b));
  S.groups = null; drawGroups(); loadGroups();
}));
$$("#thours .chip").forEach((b) => (b.onclick = () => {
  S.tHours = +b.dataset.h;
  $$("#thours .chip").forEach((x) => x.classList.toggle("sel", x === b));
  loadStat();
}));

/* ═════════ devor ═════════ */
let wallPlayers = [];
$$("[data-w]").forEach((b) => (b.onclick = () => {
  S.wallN = +b.dataset.w;
  $$("[data-w]").forEach((x) => x.classList.toggle("sel", x === b));
  drawWall(true);
}));
$("#wprob").onclick = () => { S.wallMode = S.wallMode === "prob" ? "all" : "prob"; drawWall(true); };
$("#wsel").onclick = () => { S.wallMode = S.wallMode === "sel" ? "all" : "sel"; drawWall(true); };

function wallCams() {
  let l = S.cams.filter((c) => c.enabled);
  if (S.wallMode === "prob") l = l.filter((c) => c.state !== "online");
  if (S.wallMode === "sel") l = l.filter((c) => S.picked.has(c.id));
  return l.slice(0, S.wallN);
}
function stopWall() {
  wallPlayers.forEach((p) => p.stop());
  wallPlayers = [];
}
function drawWall(restart) {
  if (S.page !== "wall") return;
  $("#wprob").classList.toggle("sel", S.wallMode === "prob");
  $("#wsel").classList.toggle("sel", S.wallMode === "sel");
  stopWall();
  const l = wallCams(), cols = Math.ceil(Math.sqrt(Math.min(S.wallN, Math.max(l.length, 1))));
  $("#wall").style.gridTemplateColumns = `repeat(${cols},1fr)`;
  $("#wall").innerHTML = l.map((c) => `<div class="tile" data-id="${c.id}">
    ${c.state === "offline" || c.state === "unknown"
      ? `<div class="tile-off ${c.state === "unknown" ? "idle" : ""}"><span>${
          c.state === "offline" ? "O'CHGAN" : "TEKSHIRILMAGAN"}</span></div>`
      : `<video muted playsinline poster="/api/v1/cameras/${c.id}/snapshot"></video>
         <div class="tile-msg"></div><div class="tile-play">▶</div>`}
    <div class="tile-h"><i class="dot s-${c.state}"></i>${esc(c.name)}</div>
    <div class="tile-f"><span>${esc(c.region)}</span><span>${esc(c.sub_codec || c.codec || "—")}</span>
      <span class="r">—</span></div></div>`).join("");
  // Video BOSILGANDA ochiladi — devor ochilishi bilan hamma oqim birdan
  // tortilmasin (kamera va tarmoqqa ortiqcha yuk bo'lardi).
  $$("#wall .tile").forEach((t) => {
    const id = +t.dataset.id;
    t.querySelector(".tile-h").onclick = (e) => { e.stopPropagation(); openDiag(id); };
    const video = t.querySelector("video");
    if (!video) { t.onclick = () => openDiag(id); return; }
    let player = null;
    t.onclick = () => {
      const overlay = t.querySelector(".tile-play");
      if (!player) {
        player = createPlayer(video, t.querySelector(".tile-msg"));
        player.camId = id;
        wallPlayers.push(player);
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
    };
  });
  updateWallFoot();
  $("#wempty").innerHTML = l.length ? "" : `<div class="empty">
    Ko'rsatiladigan kamera yo'q.<br>Filtrni o'chiring yoki jadvaldan tanlang.</div>`;
}
function updateWallFoot() {
  let total = 0;
  $$("#wall .tile").forEach((t) => {
    const c = S.byId.get(+t.dataset.id);
    if (!c) return;
    const mb = camRt(c).inMbps;
    total += mb;
    t.querySelector(".tile-f .r").textContent = mb ? mb.toFixed(1) + " Mb/s" : "—";
  });
  const eg = S.health ? ` · egress ${Math.round(S.health.egress_mbps)}/${
    S.health.egress_capacity_mbps} Mbit/s` : "";
  $("#wsum").textContent = `${$$("#wall .tile").length} katak · kirish ${
    total.toFixed(1)} Mbit/s · sub oqim${eg}`;
}

/* ═════════ diagnostika ═════════ */
const ICONS = {
  cam: '<path d="M2 5h11v9H2z"/><path d="M13 8l4-2v7l-4-2"/>',
  rtsp: '<path d="M3 10h3l2-5 3 9 2-4h3"/>',
  mtx: '<path d="M3 4h13v11H3z"/><path d="M3 8h13"/><circle cx="6" cy="6" r=".7"/>',
  key: '<circle cx="7" cy="10" r="3.5"/><path d="M10 9l6-3M14 7l1 2M16 6l1 2"/>',
  br: '<rect x="2" y="4" width="15" height="11" rx="1.5"/><path d="M2 8h15"/>',
};

function stages(c) {
  const rt = camRt(c), a = snapAge(c);
  const F = (t, v, s, i) => ({t, v, s, i});
  if (c.state === "unknown") return [
    F("Kamera", "tekshirilmadi", "idle", "cam"), F("RTSP", "—", "idle", "rtsp"),
    F("MediaMTX", "yo'l yo'q", "idle", "mtx"), F("Chipta", "—", "idle", "key"),
    F("Surat", "—", "idle", "br")];
  if (c.state === "offline" || c.state === "disabled") return [
    F("Kamera", c.state === "disabled" ? "o'chirilgan" : "javob yo'q", "fail", "cam"),
    F("RTSP", "ulanish yo'q", "fail", "rtsp"),
    F("MediaMTX", rt.ready ? "yo'l qotgan" : "yo'l yo'q", "idle", "mtx"),
    F("Chipta", "berilmaydi", "idle", "key"),
    F("Surat", "404 · to'silgan", "idle", "br")];
  if (c.state === "stalled") return [
    F("Kamera", c.ip, "ok", "cam"), F("RTSP", "ulangan", "ok", "rtsp"),
    F("MediaMTX", "bayt kelmayapti", "warn", "mtx"),
    F("Chipta", rt.readers + " tomoshabin", "ok", "key"),
    F("Surat", a >= 0 ? age(a) + " oldin" : "yo'q", a >= 0 && a < 120 ? "ok" : "warn", "br")];
  const snapBad = a < 0 || a > 600;
  return [
    F("Kamera", c.ip || "tayyor oqim", "ok", "cam"),
    F("RTSP", c.codec ? c.codec + (c.resolution ? " · " + c.resolution : "") : "ulangan", "ok", "rtsp"),
    F("MediaMTX", rt.ready ? (rt.inMbps ? rt.inMbps.toFixed(1) + " Mb/s" : "tayyor")
                           : "talab kutilmoqda", rt.ready ? "ok" : "idle", "mtx"),
    F("Chipta", rt.readers + " tomoshabin", "ok", "key"),
    F("Surat", a >= 0 ? age(a) + " oldin" : "hali yo'q", snapBad ? "warn" : "ok", "br")];
}

function verdict(c, probe) {
  if (probe) {
    if (probe.ok) return ["ok", "✓", "Probe muvaffaqiyatli", esc(probe.message)];
    return ["bad", "!", "Probe to'xtadi: " + esc(probe.stage), esc(probe.message)];
  }
  const rt = camRt(c), a = snapAge(c);
  if (c.state === "unknown") return ["mid", "?", "Hali tekshirilmagan",
    "Kamera qo'shilgan, lekin birinchi tekshiruv o'tmagan. Bir daqiqada holat aniqlanadi — " +
    "yoki \"Qayta tekshirish\" tugmasini bosing."];
  if (c.state === "disabled") return ["mid", "—", "Admin o'chirib qo'ygan",
    "Kamera ataylab o'chirilgan — oqim ham, surat ham berilmaydi."];
  if (c.state === "offline") return ["bad", "!", "Kamera tarmoqdan javob bermayapti",
    "TCP tekshiruv o'tmadi. Kabel/kommutatorni ko'ring; sabab aniq bo'lmasa " +
    "\"Qayta tekshirish\" bosqichma-bosqich aytadi (tarmoq / parol / yo'l). " +
    "Surat ataylab to'silgan — eski kadr jonli bo'lib ko'rinmasin."];
  if (c.state === "stalled") return ["mid", "~", "Yo'l tayyor, lekin bayt kelmayapti",
    "MediaMTX ulanishni ushlab turibdi, ma'lumot oqimi to'xtagan. Odatda kamera qayta " +
    "yuklanayotganda yoki tarmoq uzilganda bo'ladi — reconciler o'zi tiklaydi, " +
    "surat esa HTTP orqali kelishi mumkin."];
  if (a >= 0 && a > 600 && c.ip) return ["mid", "~", "Video ishlayapti, surat eskirgan",
    "Oqim normal, lekin oxirgi surat " + age(a) + " oldin olingan — snapshot manbai " +
    "javob bermayotgan bo'lishi mumkin. \"Suratni yangilash\"ni sinang."];
  return ["ok", "✓", "Zanjir to'liq",
    "Kameradan iste'molchigacha uzilish ko'rinmaydi." +
    (rt.warm ? " Yo'l issiq to'plamda — qayta ochilish bir soniyagacha." : "")];
}

let probeResult = null;
function openDiag(id) {
  const c = S.byId.get(id);
  if (!c) return;
  if (S.curId !== id) { probeResult = null; closeLive(); }
  if (S.curId !== id) S.diagDay = 0;   // boshqa kamera — bugundan boshlaymiz
  S.curId = id;
  go("diag");
  drawDiag();
  loadDiagHistory(c);
  warmStream(c);          // play bosilguncha oqim tayyor bo'lib tursin
}

/* ═════════ diag: jonli ko'rish (kerak paytda, bir bosishda) ═════════ */
let diagPlayer = null, diagQ = "";
function openLive(c) {
  $("#dlive").style.display = "";
  const video = $("#dvideo");
  video.poster = `/api/v1/cameras/${c.id}/snapshot`;
  if (!diagPlayer) diagPlayer = createPlayer(video, $("#dlivemsg"));
  diagPlayer.open(c, diagQ);
}
function closeLive() {
  if (diagPlayer) diagPlayer.stop();
  const box = $("#dlive");
  if (box) { box.style.display = "none"; $("#dlivemsg").textContent = ""; }
}
function setLiveQ(q) {
  diagQ = q;
  $("#dlq").classList.toggle("sel", !q);
  $("#dlqsub").classList.toggle("sel", q === "sub");
  const c = S.byId.get(S.curId);
  if (c && $("#dlive").style.display !== "none") openLive(c);
}
$("#dlstop").onclick = closeLive;
$("#dlq").onclick = () => setLiveQ("");
$("#dlqsub").onclick = () => setLiveQ("sub");
function drawDiag() {
  const c = S.byId.get(S.curId);
  if (!c) return;
  $("#chainwrap").style.display = "";
  $("#dname").textContent = c.name;
  $("#dsub").innerHTML = `<span class="st"><i class="dot s-${c.state}"></i>${LBL[c.state]}</span>
    &nbsp;·&nbsp; ${esc(c.external_id || "tashqi id yo'q")} &nbsp;·&nbsp; ${esc(c.region)}
    &nbsp;·&nbsp; ${c.node_id === 1 ? "lokal tugun" : "tugun #" + c.node_id}`;
  const canLive = c.state !== "offline" && c.state !== "disabled";
  $("#dacts").innerHTML = `
    ${canLive ? '<button class="btn" data-a="live">▶ Jonli ko\'rish</button>' : ""}
    <button class="btn ghost" data-a="probe">Qayta tekshirish</button>
    <button class="btn ghost" data-a="kf">Keyframe so'rash</button>
    <button class="btn ghost" data-a="snap">Suratni yangilash</button>
    <button class="btn ghost" data-a="stale">Oxirgi kadr</button>
    <button class="btn ghost" data-a="wall">Devorda ochish</button>
    <button class="btn ghost" data-a="toggle">${c.enabled ? "O'chirib qo'yish" : "Yoqish"}</button>
    <button class="btn ghost danger" data-a="del">O'chirish</button>`;
  $$("#dacts [data-a]").forEach((b) => (b.onclick = () => act(b.dataset.a, c)));

  const st = stages(c);
  $("#chain").innerHTML = st.map((s, i) => {
    const link = i < st.length - 1 ? `<div class="link ${
      s.s === "ok" && st[i + 1].s !== "idle" ? "live" : s.s === "fail" ? "cut" : ""}"></div>` : "";
    return `<div class="stage ${s.s}"><div class="node">
      <svg viewBox="0 0 19 19">${ICONS[s.i]}</svg></div>
      <div class="stage-t">${s.t}</div><div class="stage-v">${esc(s.v)}</div></div>${link}`;
  }).join("");
  const [k, ic, ttl, txt] = verdict(c, probeResult);
  $("#verdict").className = "verdict " + k;
  $("#verdict").innerHTML =
    `<div class="verdict-i">${ic}</div><div><b>${esc(ttl)}</b><p>${txt}</p></div>`;
  drawDiagCards();
  loadHistory(c);
}

/* Ish vaqti statistikasi — 3 soniyalik poll'da qayta so'ralmasin deb
   keshda turadi (60 s). */
const fmtDur = (s) => s < 60 ? Math.round(s) + " s"
  : s < 3600 ? Math.round(s / 60) + " daqiqa"
  : s < 86400 ? (s / 3600).toFixed(1) + " soat" : (s / 86400).toFixed(1) + " kun";

/* ═════════ kamera tahlili: uzilish tarixi ═════════

   Bitta so'rov — /admin/cameras/{id}/history — sahifadagi hamma narsani
   beradi: KPI, soatlik profil, 30 kunlik kalendar va uzilishlar jurnali.
   Bo'lak-bo'lak so'ralsa ular bir-biriga mos kelmay qolardi, chunki har
   oraliq "hozir" ga bog'langan: kalendar bir narsani, jurnal boshqa
   narsani ko'rsatardi.                                                */

const dd = (n) => (n < 10 ? "0" : "") + n;
/* Server UTC beradi, operator mahalliy vaqtni ko'radi. */
const localHM = (iso) => { const d = new Date(iso); return dd(d.getHours()) + ":" + dd(d.getMinutes()); };
const localDM = (iso) => { const d = new Date(iso); return dd(d.getDate()) + "." + dd(d.getMonth() + 1); };

async function loadDiagHistory(c) {
  const tz = -new Date().getTimezoneOffset();
  S.hist = null;
  drawDiagHistory();
  try {
    S.hist = await api(`/api/v1/admin/cameras/${c.id}/history`
      + `?days=30&day=${S.diagDay}&tz_offset_minutes=${tz}`);
  } catch (e) { S.hist = null; }
  if (S.page === "diag" && S.curId === c.id) drawDiagHistory();
}

function drawDiagHistory() {
  const h = S.hist;
  const show = (id, on) => { const el = $(id); if (el) el.style.display = on ? "" : "none"; };
  if (!h) {
    $("#dkpi").innerHTML = "";
    ["#dprofwrap", "#dcalwrap", "#dojwrap"].forEach((id) => show(id, false));
    return;
  }
  ["#dprofwrap", "#dcalwrap", "#dojwrap"].forEach((id) => show(id, true));
  const s = h.summary;

  /* ── KPI ── */
  const tile = (lbl, val, sub, color) => `<div class="k">
    <div class="k-l">${lbl}</div>
    <div class="k-v" ${color ? `style="color:${color}"` : ""}>${val}</div>
    <div class="k-s">${sub}</div></div>`;
  const peakLbl = s.outages_period
    ? `${dd(h.peak.from_hour)}:00–${dd(h.peak.to_hour)}:00` : "—";
  $("#dkpi").innerHTML =
    tile("Mavjudlik · " + esc(h.selected_date.slice(5)), s.uptime_pct_day + "<u>%</u>",
         `30 kun: ${s.uptime_pct_period}%`, upColor(s.uptime_pct_day)) +
    tile("Uzilish (kun)", s.outages_day || "0", `30 kun: ${s.outages_period}`,
         s.outages_day ? "var(--warn)" : null) +
    tile("O'chiq vaqt", durHM(s.offline_seconds_day),
         `30 kun: ${durHM(s.offline_seconds_period)}`,
         s.offline_seconds_day ? "var(--fail)" : null) +
    tile("Pik oyna", peakLbl,
         h.peak.offline_seconds ? `shu oynada ${durHM(h.peak.offline_seconds)}` : "uzilish yo'q") +
    tile("MTTR", durHM(s.mttr_seconds), "o'rtacha tiklanish") +
    tile("MTBF", durHM(s.mtbf_seconds), "uzilishlar orasi") +
    tile("Eng uzun uzilish", durHM(s.longest_outage_seconds),
         s.longest_outage_at ? localDM(s.longest_outage_at) + " · "
           + localHM(s.longest_outage_at) : "—") +
    tile("Tomoshabin", camRt(S.byId.get(S.curId) || {}).readers || "0",
         "ayni damda");

  /* ── soatlik profil ── */
  $("#dprofday").textContent = h.selected_date;
  const hs = h.hourly_offline_seconds, max = Math.max(1, ...hs);
  const span = (h.peak.to_hour - h.peak.from_hour + 24) % 24 || 3;
  const inPeak = (i) => hs.some((v) => v > 0) &&
    (i - h.peak.from_hour + 24) % 24 < span;
  $("#dhours").innerHTML = hs.map((v, i) => `<i class="hbar"
    style="height:${v ? Math.max(4, Math.round(v / max * 100)) : 2}%;
      background:${!v ? "var(--line)" : inPeak(i) ? "var(--fail)"
        : v > max * 0.6 ? "var(--warn)" : "var(--signal)"}"
    title="${dd(i)}:00 — ${v ? durHM(v) + " o'chiq" : "uzilishsiz"}"></i>`).join("");
  $("#dhoursx").innerHTML = hs.map((_, i) =>
    `<span>${i % 3 === 0 ? dd(i) : ""}</span>`).join("");
  const quiet = hs.indexOf(Math.min(...hs));
  $("#dproffoot").innerHTML = h.outages.length
    ? `eng ko'p uzilish <b style="color:var(--fail)">${peakLbl}</b>
       · eng tinch <b>${dd(quiet)}:00</b>
       · kunlik o'chiq <b>${durHM(s.offline_seconds_day)}</b>
       · uzilishlar <b>${s.outages_day} ta</b>`
    : `bu kunda uzilish qayd etilmagan`;
  $("#dpeak").textContent = h.peak.offline_seconds
    ? `pik ${peakLbl} · ${durHM(h.peak.offline_seconds)}` : "";

  /* ── 30 kunlik kalendar ── */
  $("#dcal").innerHTML = h.daily.map((d) => `<button class="cal-c${
      d.days_back === h.day ? " sel" : ""}" data-d="${d.days_back}">
    <div class="cal-d">${esc(d.date.slice(5).replace("-", "."))}</div>
    <div class="cal-v" style="color:${d.offline_seconds ? upColor(d.uptime_pct) : "var(--faint)"}">${
      d.offline_seconds ? durHM(d.offline_seconds) : "0"}</div>
    <div class="cal-s">${d.outages ? d.outages + " uzilish" : "toza"}</div>
  </button>`).join("");
  $$("#dcal .cal-c").forEach((b) => (b.onclick = () => {
    S.diagDay = +b.dataset.d;
    const cam = S.byId.get(S.curId);
    if (cam) loadDiagHistory(cam);
  }));
  const worst = h.daily.reduce((a, d) => d.offline_seconds > a.offline_seconds ? d : a,
                               h.daily[0]);
  $("#dcalfoot").innerHTML =
    `30 kunlik o'chiq vaqt <b>${durHM(s.offline_seconds_period)}</b>
     · uzilish <b>${s.outages_period} ta</b>
     · eng yomon kun <b>${esc(worst.date.slice(5))}</b> · ${durHM(worst.offline_seconds)}
     · mavjudlik (30k) <b style="color:${upColor(s.uptime_pct_period)}">${s.uptime_pct_period}%</b>`;

  /* ── daqiqalik chiziq ──
     Soatlik ustunlar "qachon" ni aytadi, bu chiziq "qanday" ni: uzilish
     bir marta uzoq bo'lganmi yoki kun bo'yi uzuq-yuluqmi. */
  const cellSec = (h.strip_minutes || 15) * 60;
  $("#dstrip").innerHTML = h.strip.map((v, i) => {
    if (i >= h.strip_elapsed) return `<i class="sc future"></i>`;   // hali kelmagan
    const ratio = v / cellSec;
    const cls = !v ? "ok" : ratio >= 0.5 ? "bad" : "warn";
    return `<i class="sc ${cls}" title="${dd(Math.floor(i * (h.strip_minutes || 15) / 60))}:${
      dd((i * (h.strip_minutes || 15)) % 60)} — ${v ? durHM(v) + " o'chiq" : "uzilishsiz"}"></i>`;
  }).join("");

  /* ── uzilishlar jurnali ── */
  $("#dojday").textContent = `${h.selected_date} · ${h.outages.length} uzilish`;
  $("#doj").innerHTML = h.outages.map((o) => `<tr>
    <td class="meta">${localHM(o.from)}</td>
    <td class="meta">${o.recovered ? localHM(o.to) : "—"}</td>
    <td class="meta" style="color:var(--warn)">${durHM(o.seconds)}</td>
    <td>${o.recovered ? `<span class="tag good">tiklandi</span>`
      : `<span class="tag bad">davom etyapti</span>`}</td>
  </tr>`).join("");
  $("#dojempty").innerHTML = h.outages.length ? "" :
    `<div class="empty">Bu kunda uzilish yo'q.</div>`;

  /* ── harakatlar tahlili ──
     Uzilishlar jurnali "tarmoq nima qildi" ni aytadi, bu esa "tizim
     nima qildi" ni: oqim muzladimi, MediaMTX qayta ko'tarildimi.
     Ikkovini yonma-yon qo'yish sababni topishni tezlashtiradi. */
  const acts = h.actions || [];
  const KIND = {online: "good", offline: "bad", stalled: "mid",
                resumed: "good", mediamtx: "mid"};
  $("#dactmeta").textContent = `${h.selected_date} · ${acts.length} yozuv`;
  $("#dact").innerHTML = acts.map((a) => `<tr>
    <td class="meta">${localHM(a.ts)}</td>
    <td><span class="tag ${KIND[a.kind] || ""}">${esc(a.kind)}</span></td>
    <td class="meta">${a.path === "asosiy" ? "—" : esc(a.path)}</td>
    <td class="meta">${esc(a.detail || "—")}</td>
  </tr>`).join("");
  $("#dactempty").innerHTML = acts.length ? "" :
    `<div class="empty">Bu kunda yozuv yo'q.</div>`;
}

function drawDiagCards() {
  const c = S.byId.get(S.curId);
  if (!c || S.page !== "diag") return;
  const rt = camRt(c), a = snapAge(c);
  const gb = rt.bytes > 1e9 ? (rt.bytes / 1e9).toFixed(1) + " GB"
    : rt.bytes ? Math.round(rt.bytes / 1e6) + " MB" : "—";
  const card = (h, kv, extra = "") => `<div class="card"><h3>${h}</h3><dl class="kv">${
    kv.map(([x, y]) => `<dt>${x}</dt><dd>${y}</dd>`).join("")}</dl>${extra}</div>`;
  const snapUrl = `/api/v1/cameras/${c.id}/snapshot`;
  const showSnap = c.state !== "offline" && c.state !== "disabled";
  $("#dcards").innerHTML =
    card("Ulanish", [
      ["IP", esc(c.ip || "—")], ["Port", c.port || "—"],
      ["Ishlab chiqaruvchi", esc(c.vendor || "—")],
      ["RTSP yo'l", esc(c.rtsp_path || "—")],
      ["Model", esc(c.model || "—")], ["Firmware", esc(c.firmware || "—")]]) +
    card("Kodeklar", [
      ["Asosiy", esc(c.codec || "—")], ["Sub", esc(c.sub_codec || "topilmadi")],
      ["Sub yo'l", esc(c.sub_path || "—")],
      ["O'girish", c.transcode ? "H.265 → H.264" : "kerak emas"],
      ["O'lcham", esc(c.resolution || "—")]]) +
    card("MediaMTX", [
      ["Yo'l", rt.ready ? "tayyor" : "kutmoqda"],
      ["Kirish", rt.inMbps ? rt.inMbps.toFixed(1) + " Mbit/s" : "—"],
      ["Tomoshabin", rt.readers], ["Baytlar", gb],
      ["Issiq to'plam", rt.warm ? "ha" : "yo'q"],
      ["Slug", esc(c.slug || "—")]]) +
    card("Surat", [
      ["Yoshi", c.state === "offline" ? "berilmaydi (404)" : esc(age(a))],
      ["Oxirgi", esc(c.snapshot_at || "—")]],
      showSnap ? `<img class="snap-img" id="dsnap" alt=""
        src="${snapUrl}?t=${Date.now()}" onerror="this.style.display='none'">` : "") +
    `<div class="card" style="grid-column:1/-1"><h3>Holat tarixi (jurnal)</h3>
      <div id="dhist" style="font-size:12.5px;color:var(--faint)">yuklanmoqda…</div></div>`;
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
      <span class="hist-t">${esc((e.ts || "").slice(5, 16))}</span>
      <i class="dot s-${e.kind === "online" ? "online" : e.kind === "offline"
        ? "offline" : e.kind === "stalled" ? "stalled" : "unknown"}"></i>
      <span>${esc(e.kind)}</span>
      <span style="color:var(--faint);margin-left:6px">${esc(e.detail || "")}</span>
    </div>`).join("") : "Bu kamera bo'yicha yozuv yo'q.";
  } catch (e) {}
}
async function act(a, c) {
  if (a === "live") { openLive(c); return; }
  if (a === "wall") { S.picked = new Set([c.id]); S.wallMode = "sel"; go("wall"); return; }
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
      pushEv(probeResult.ok ? "amal" : "xato",
        `<b>${esc(c.name)}</b> probe · ${esc(probeResult.message)}`, c.id);
      drawDiag();
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

/* ═════════ tizim ═════════ */
const hEg = [], hIn = [];
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
  if (!S.health) return;
  const h = S.health, st = S.status || {};
  const ing = Object.entries(S.rates)
    .filter(([slug]) => !slug.endsWith("_h264"))     // o'girish ichki aylanish
    .reduce((s, [, v]) => s + v, 0);
  hEg.push(h.egress_mbps); hIn.push(ing);
  if (hEg.length > 40) { hEg.shift(); hIn.shift(); }
  const cap = h.egress_capacity_mbps || 1000;
  const pct = h.egress_mbps / cap * 100;
  const cls = pct > 95 ? "crit" : pct > 80 ? "warn" : "";
  const on = S.cams.filter((c) => c.state === "online").length;
  const fan = ing > 0.1 ? h.egress_mbps / ing : 0;
  $("#sstats").innerHTML = `
    <div class="stat"><div class="lbl">Kirish</div>
      <div class="val">${ing.toFixed(0)}<u> Mbit/s</u></div>
      <div class="sub">kameralardan</div>${spark(hIn, "var(--warm)")}</div>
    <div class="stat"><div class="lbl">Chiqish</div>
      <div class="val">${h.egress_mbps.toFixed(0)}<u> Mbit/s</u></div>
      <div class="sub">${cap} dan · ${pct.toFixed(0)}%</div>
      <div class="bar solo ${cls}"><i style="width:${Math.min(100, pct)}%"></i></div></div>
    <div class="stat"><div class="lbl">Fanout</div>
      <div class="val">${fan ? fan.toFixed(1) : "—"}<u>${fan ? "×" : ""}</u></div>
      <div class="sub">${fan > 3 ? "chegara chiqishda" : "chegara kirishda"}</div></div>
    <div class="stat"><div class="lbl">Faol oqimlar</div><div class="val">${h.streams}</div>
      <div class="sub">${h.readers} tomoshabin</div></div>
    <div class="stat"><div class="lbl">Issiq yo'llar</div>
      <div class="val">${h.warm}<u> / 256</u></div>
      <div class="sub">sub · 10 daq</div></div>
    <div class="stat"><div class="lbl">Kameralar</div>
      <div class="val">${on}<u> / ${S.cams.length}</u></div>
      <div class="sub">ishlayapti · SSE ${h.sse_subscribers}</div></div>`;
  const sw = (st.health || h.health || {});
  const sn = h.snapshots || {};
  $("#sjobs").innerHTML =
    `<dt>Health sweep</dt><dd>${sw.checked || 0} manzil · ${
      ((sw.duration_ms || 0) / 1000).toFixed(1)} s</dd>
    <dt>Snapshot tsikli</dt><dd>${sn.total ? `${sn.total} ta · ${
      ((sn.duration_ms || 0) / 1000).toFixed(1)} s` : "hali yo'q"}</dd>
    <dt>MediaMTX</dt><dd>${h.mediamtx
      ? '<span class="tag good">tirik</span>' : '<span class="tag bad">yiqilgan</span>'}</dd>`;
  $("#snodes").innerHTML = (S.nodes || []).map((n) =>
    `<dt>${esc(n.name)}</dt><dd><span class="tag ${n.status === "online" ? "good"
      : n.status === "degraded" ? "mid" : "bad"}">${n.status}</span> ${
      n.ready || 0} oqim · ${n.cameras || 0} kamera</dd>`).join("") || "<dt>—</dt><dd>—</dd>";
  const d = st.disk || {};
  $("#sdisk").innerHTML =
    `<dt>Suratlar</dt><dd>${d.snapshots_mb ?? "—"} MB · ${d.snapshots_files ?? "—"} fayl</dd>
    <dt>Baza</dt><dd>${d.db_mb ?? "—"} MB</dd>
    <dt>Log</dt><dd>${d.log_mb ?? "—"} MB · aylanma 5 MB×3</dd>`;
  const g = {};
  S.cams.forEach((c) => {
    if (!c.ip || c.state === "offline") return;
    const rt = camRt(c);
    const n = (rt.ready ? 1 : 0) + (S.rt[c.slug + "_sub"] ? 1 : 0);
    if (n) g[c.ip] = (g[c.ip] || 0) + n;
  });
  $("#sconn").innerHTML = Object.entries(g).sort((a, b) => b[1] - a[1]).slice(0, 12)
    .map(([ip, n]) => `<dt>${esc(ip)}</dt><dd>${n} ulanish ${
      n > 6 ? '<span class="tag mid">DVR chegarasi</span>' : ""}</dd>`).join("")
    || "<dt>—</dt><dd>faol ulanish yo'q</dd>";
  $("#sstall").innerHTML = (st.stalled || []).length
    ? st.stalled.map((s) => `<div class="hist-r"><i class="dot s-stalled"></i>
        <span class="mono" style="font-size:12px">${esc(s)}</span></div>`).join("")
    : `<div style="color:var(--faint);font-size:13px">Muzlagan oqim yo'q.</div>`;
}

/* ═════════ hodisalar (SSE) ═════════ */
$$("#efilt .chip[data-e]").forEach((b) => (b.onclick = () => {
  S.evFilt = b.dataset.e;
  $$("#efilt .chip[data-e]").forEach((x) => x.classList.toggle("sel", x === b));
  renderFeed();
}));
$("#epause").onclick = () => {
  S.evPause = !S.evPause;
  $("#epause").classList.toggle("sel", S.evPause);
  $("#epause").textContent = S.evPause ? "Davom etish" : "Pauza";
};
$("#eclear").onclick = () => { S.evlog = []; S.evN = 0; $("#ne").textContent = 0; renderFeed(); };

function pushEv(kind, body, id) {
  if (S.evPause && (kind === "state" || kind === "snapshot")) return;
  S.evlog.unshift({t: clock(), kind, body, id});
  if (S.evlog.length > 300) S.evlog.pop();
  $("#ne").textContent = ++S.evN;
  if (S.page === "ev") renderFeed(true);
}
function renderFeed(anim) {
  const l = S.evlog.filter((e) => S.evFilt === "all" || e.kind === S.evFilt).slice(0, 80);
  $("#feed").innerHTML = l.length ? l.map((e, i) => `<div class="ev${anim && !i ? " new" : ""}"
    ${e.id ? `data-id="${e.id}"` : ""}>
    <span class="ev-t">${e.t}</span><span class="ev-k ${e.kind}">${e.kind}</span>
    <span class="ev-b">${e.body}</span></div>`).join("")
    : `<div class="empty" style="border:none">Bu turdagi hodisa hali yo'q.</div>`;
  $$("#feed .ev[data-id]").forEach((d) => (d.onclick = () => openDiag(+d.dataset.id)));
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
const PAGES = [["home", "Holat"], ["cams", "Kameralar"],
  ["groups", "Guruhlar"], ["stat", "Tahlil"], ["wall", "Devor"],
  ["sys", "Tizim"], ["ev", "Hodisalar"], ["scan", "Qurilma qo'shish"]];
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
    .map((c) => ({h: `<i class="dot s-${c.state}"></i>${esc(c.name)}`,
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
