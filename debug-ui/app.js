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
  const res = await fetch(path, options);
  if (res.status === 401) { showGate(); throw new Error("Kirish kerak"); }
  if (!res.ok) {
    let detail = res.status + "-xato";
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

/* Brauzer H.265 ni o'zi o'qiy oladimi — olsa server o'girmaydi. */
const HEVC_OK = (() => {
  try {
    const caps = RTCRtpReceiver.getCapabilities("video");
    if (caps && caps.codecs.some((c) => /H265|hevc/i.test(c.mimeType))) return true;
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
  loadCams(); pollRuntime(); loadStatus(); startSSE();
  setInterval(loadCams, 15000);
  setInterval(pollRuntime, 3000);
  setInterval(loadStatus, 30000);
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
    drawCams(); drawNav();
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

function createPlayer(video, msgEl) {
  const p = {video, msgEl, hls: null, pc: null, token: 0, mode: ""};

  p.stop = () => {
    p.token++;
    if (p.hls) { p.hls.destroy(); p.hls = null; }
    if (p.pc) { p.pc.close(); p.pc = null; }
    video.pause();
    video.removeAttribute("src");
    video.srcObject = null;
    video.load();
  };

  p.open = (cam, quality) => {
    p.stop();
    const my = ++p.token;
    const stale = () => p.token !== my;
    msgEl.textContent = "ulanmoqda…";
    api(`/api/v1/cameras/${cam.id}/stream?hevc=${HEVC_OK ? 1 : 0}` +
        (quality ? `&quality=${quality}` : ""))
      .then((urls) => {
        if (stale()) return;
        p.mode = urls.mode;
        const onFail = urls.mode === "sub"
          ? () => { if (!stale()) p.open(cam, ""); }        // sub yiqilsa asosiy
          : () => { if (!stale()) msgEl.textContent = FAIL_MSG; };
        attach(urls, stale, onFail);
      })
      .catch((e) => { if (!stale()) msgEl.textContent = e.message; });

    function attach(urls, staleFn, onFail) {
      video.addEventListener("playing", () => { if (!staleFn()) msgEl.textContent = ""; },
        {once: true});
      if (urls.webrtc_url) {
        playWebRtc(urls.webrtc_url, staleFn).catch(() => {
          if (staleFn()) return;
          playHls(urls.stream_url, staleFn, onFail);
        });
        return;
      }
      if (!urls.stream_url) { msgEl.textContent = "manzil yo'q"; return; }
      playHls(urls.stream_url, staleFn, onFail);
    }

    async function playWebRtc(whepUrl, staleFn) {
      const pc = new RTCPeerConnection({iceServers: []});
      p.pc = pc;
      pc.addTransceiver("video", {direction: "recvonly"});
      pc.ontrack = (e) => {
        if (staleFn()) return;
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
      if (!res.ok) { pc.close(); throw new Error("WHEP " + res.status); }
      const answer = await res.text();
      if (staleFn()) { pc.close(); return; }
      await pc.setRemoteDescription({type: "answer", sdp: answer});
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          if (video.srcObject) resolve();
          else { pc.close(); reject(new Error("WebRTC jim")); }
        }, 6000);
        pc.addEventListener("connectionstatechange", () => {
          if (pc.connectionState === "connected") { clearTimeout(timer); resolve(); }
          if (pc.connectionState === "failed") { clearTimeout(timer); pc.close();
            reject(new Error("WebRTC uzildi")); }
        });
      });
    }

    function playHls(url, staleFn, onFail) {
      if (!url) { msgEl.textContent = FAIL_MSG; return; }
      const isHls = url.includes(".m3u8");
      if (isHls && window.Hls && Hls.isSupported()) {
        const hls = new Hls({lowLatencyMode: true, maxBufferLength: 6,
          backBufferLength: 6, liveSyncDurationCount: 1,
          manifestLoadingTimeOut: 25000});
        p.hls = hls;
        hls.loadSource(url);
        hls.attachMedia(video);
        video.play().catch(() => {});
        hls.on(Hls.Events.ERROR, (_, d) => {
          if (staleFn() || !d.fatal) return;
          hls.destroy(); p.hls = null;
          if (onFail) onFail(); else msgEl.textContent = FAIL_MSG;
        });
      } else if (isHls && video.canPlayType("application/vnd.apple.mpegurl")) {
        video.src = url;
        video.onerror = () => { if (!staleFn()) msgEl.textContent = FAIL_MSG; };
        video.play().catch(() => {});
      } else {
        video.src = url;
        video.onerror = () => { if (!staleFn()) msgEl.textContent = FAIL_MSG; };
        video.play().catch(() => {});
      }
    }
  };
  return p;
}

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
  S.curId = id;
  go("diag");
  drawDiag();
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
  loadUptime(c);
}

/* Ish vaqti statistikasi — 3 soniyalik poll'da qayta so'ralmasin deb
   keshda turadi (60 s). */
const uptimeCache = {};
const fmtDur = (s) => s < 60 ? Math.round(s) + " s"
  : s < 3600 ? Math.round(s / 60) + " daqiqa"
  : s < 86400 ? (s / 3600).toFixed(1) + " soat" : (s / 86400).toFixed(1) + " kun";
async function loadUptime(c) {
  const hit = uptimeCache[c.id];
  if (hit && performance.now() - hit.t < 60000) return;
  try {
    const d = await api(`/api/v1/admin/cameras/${c.id}/uptime?hours=168`);
    uptimeCache[c.id] = {t: performance.now(), data: d};
    if (S.page === "diag" && S.curId === c.id) drawDiagCards();
  } catch (e) {}
}
function uptimeCard(c) {
  const hit = uptimeCache[c.id];
  if (!hit) return `<div class="card"><h3>Ish vaqti (7 kun)</h3>
    <div style="color:var(--faint);font-size:13px">yuklanmoqda…</div></div>`;
  const d = hit.data;
  const tl = `<div class="tl" title="7 kunlik chiziq: yashil — ishlagan, qizil — o'chiq">${
    d.segments.map((sg) =>
      `<i class="${sg.state}" style="width:${
        Math.max(0.4, sg.seconds / (d.hours * 36))}%"></i>`).join("")}</div>`;
  return `<div class="card"><h3>Ish vaqti (7 kun)</h3><dl class="kv">
    <dt>Uptime</dt><dd><b>${d.uptime_pct}%</b></dd>
    <dt>Uzilishlar</dt><dd>${d.outages ? d.outages + " marta" : "yo'q"}</dd>
    <dt>Jami o'chiq</dt><dd>${d.offline_seconds ? fmtDur(d.offline_seconds) : "—"}</dd>
    <dt>Oxirgi uzilish</dt><dd>${d.last_offline_at
      ? esc(d.last_offline_at.slice(5, 16)) : "—"}</dd></dl>${tl}</div>`;
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
    uptimeCard(c) +
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
const PAGES = [["home", "Holat"], ["cams", "Kameralar"], ["wall", "Devor"],
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
