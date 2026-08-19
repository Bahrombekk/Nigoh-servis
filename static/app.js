"use strict";

const state = {
  cameras: [],
  byId: new Map(),
  vendors: [],
  admin: null,
  tab: "map",
  filter: "all",
  q: "",
  selectedId: null,
  listOpen: true,
  openRegions: {},
  pinned: [],                 // "Devorga qo'shish" bilan tanlanganlar
  wallSize: 3,
  wallRegion: "",             // devorda faqat shu hudud ("" — hammasi)
  wallFit: "contain",         // contain — butun kadr, cover — katakni to'ldirish
  wallPage: 0,
  wallAuto: false,            // sahifalarni avtomatik aylantirish
  wallHidden: new Set(),      // devordan vaqtincha olib tashlanganlar
  openTimes: [],              // shu seansda o'lchangan ochilish vaqtlari (ms)
  openByCam: new Map(),       // kamera → oxirgi ochilish vaqti (ms)
  events: [],                 // shu seans hodisalari (oqim ochildi va h.k.)
  stats: null,                // /api/stats/dashboard javobi — tarixiy grafiklar
  editingId: null,
  sourceType: "rtsp",
  picking: null,
  pickMarker: null,
  adminQuery: "",
  adminOffset: 0,
  adminTotal: 0,
  adminCameras: [],
  adminFilters: { status: "", region: "", codec: "", mode: "" },
  adminSort: { key: "", dir: 1 }
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));

/* Brauzer H.265 (HEVC) ni o'zi o'qiy oladimi? Olsa — server oqimni
   o'girmaydi, xom holda beradi va GPU umuman ishlatilmaydi. */
const HEVC_OK = (() => {
  try {
    const caps = RTCRtpReceiver.getCapabilities("video");
    if (caps && caps.codecs.some((c) => /H265|hevc/i.test(c.mimeType))) return true;
  } catch (e) { /* WebRTC yo'q */ }
  const type = 'video/mp4; codecs="hvc1.1.6.L93.B0"';
  try {
    if (window.MediaSource && MediaSource.isTypeSupported(type)) return true;
  } catch (e) { /* eskirgan brauzer */ }
  return document.createElement("video").canPlayType(type) === "probably";
})();

/* ---------- API ---------- */
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body ? { "Content-Type": "application/json" } : {},
    ...options
  });
  if (res.status === 401) {
    setAdmin(null);
    openModal("login-modal");
    throw new Error("Sessiya tugadi — qaytadan kiring");
  }
  if (!res.ok) {
    let detail = "Xatolik yuz berdi";
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

/* ---------- Mavzu ---------- */
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("nigoh-theme", theme);
  $("theme-lt").classList.toggle("on", theme === "light");
  $("theme-dk").classList.toggle("on", theme === "dark");
  setTiles();
  // Hudud pardasi va chegara rangi ham mavzuga moslashadi.
  if (uzMask) uzMask.setStyle(uzMaskStyle());
  if (uzBorder) uzBorder.setStyle(uzBorderStyle());
}
$("theme-lt").addEventListener("click", () => setTheme("light"));
$("theme-dk").addEventListener("click", () => setTheme("dark"));

/* ---------- Xarita ---------- */
// maxZoom shu yerda shart: markercluster xaritadan so'raydi, tile-qatlam
// esa keyinroq (mavzu tanlangach) qo'shiladi.
const map = L.map("map", { zoomControl: false, maxZoom: 19 }).setView([41.35, 64.6], 6);
let tiles = null;
function setTiles() {
  if (tiles) map.removeLayer(tiles);
  const dark = document.documentElement.dataset.theme === "dark";
  tiles = L.tileLayer(dark
    ? "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
    : "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
    maxZoom: 19
  }).addTo(map);
}

/* O'zbekiston hududini ajratib ko'rsatish: chegara urg'u rangida chiziladi,
   tashqi hududlar esa yarim shaffof parda bilan xiralashtiriladi. */
let uzMask = null;
let uzBorder = null;

function uzMaskStyle() {
  const dark = document.documentElement.dataset.theme === "dark";
  return { fillColor: dark ? "#02050b" : "#5b6b85",
           fillOpacity: dark ? 0.55 : 0.22 };
}
function uzBorderStyle() {
  const accent = getComputedStyle(document.documentElement)
    .getPropertyValue("--accent").trim() || "#1668d6";
  return { color: accent, weight: 1.8, opacity: 0.85 };
}

async function loadUzBoundary() {
  try {
    // Aniq chegara (geoBoundaries ADM0) — loyihaning o'zida saqlanadi,
    // internetga bog'liq emas. Anklavlar ham alohida poligon sifatida bor.
    const res = await fetch("/static/uz.geojson");
    const gj = await res.json();
    const geom = gj.features[0].geometry;
    const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.coordinates;
    const rings = polys.map((p) => p[0].map(([lng, lat]) => [lat, lng]));

    // Butun dunyoni qoplaydigan tashqi halqa + O'zbekiston "teshik" sifatida.
    const world = [[-89.9, -179.9], [-89.9, 179.9], [89.9, 179.9], [89.9, -179.9]];
    uzMask = L.polygon([world, ...rings], Object.assign({
      stroke: false, fillRule: "evenodd", interactive: false
    }, uzMaskStyle())).addTo(map);
    uzBorder = L.polygon(rings, Object.assign({
      fill: false, interactive: false
    }, uzBorderStyle())).addTo(map);
  } catch (e) { /* chegara fayli yuklanmasa — xarita oddiy qoladi */ }
}
loadUzBoundary();

const cluster = L.markerClusterGroup({
  maxClusterRadius: 60,
  showCoverageOnHover: false,
  animate: false,               // kamera ko'p bo'lganda brauzerni bo'g'masin
  chunkedLoading: true,
  chunkInterval: 120,
  disableClusteringAtZoom: 17,
  iconCreateFunction: (c) => {
    const n = c.getChildCount();
    const down = c.getAllChildMarkers().some((m) => m.options.camDown);
    const size = n > 999 ? 54 : n > 99 ? 46 : n > 9 ? 40 : 32;
    return L.divIcon({
      html: '<div class="mk-cluster' + (down ? " down" : "") + '" style="width:' + size +
            'px;height:' + size + 'px;font-size:' + (size > 40 ? 15 : 13) + 'px">' +
            (n > 999 ? (n / 1000).toFixed(1) + "k" : n) + "</div>",
      className: "", iconSize: [size, size], iconAnchor: [size / 2, size / 2]
    });
  }
});
map.addLayer(cluster);

function camIcon(cam) {
  const off = cam.online === false;
  const sel = cam.id === state.selectedId;
  return L.divIcon({
    className: "",
    html: '<div class="mk' + (off ? " off" : "") + (sel ? " sel" : "") +
          '"><span class="r"></span><span class="c"></span></div>',
    iconSize: [24, 24], iconAnchor: [12, 12]
  });
}

const markersById = new Map();

function visibleCams() {
  const q = state.q.trim().toLowerCase();
  return state.cameras.filter((c) => {
    if (state.filter === "online" && c.online === false) return false;
    if (state.filter === "offline" && c.online !== false) return false;
    if (!q) return true;
    return (c.name + " " + c.region).toLowerCase().includes(q);
  });
}

function rebuildMarkers() {
  const cams = visibleCams();
  markersById.clear();
  const markers = cams.map((cam) => {
    const m = L.marker([cam.lat, cam.lng], {
      icon: camIcon(cam), title: cam.name, camDown: cam.online === false
    });
    m.on("click", () => selectCamera(cam.id, false));
    m.on("mouseover", () => prewarm(cam));
    markersById.set(cam.id, m);
    return m;
  });
  cluster.clearLayers();
  cluster.addLayers(markers);
}

/* Holat yangilanganda markerlar qayta chizilmaydi — faqat rangi
   o'zgarganlarning belgisi almashadi. */
function refreshMarkerIcons() {
  let dirty = false;
  markersById.forEach((m, id) => {
    const cam = state.byId.get(id);
    if (!cam) return;
    const down = cam.online === false;
    if (m.options.camDown !== down) { m.options.camDown = down; dirty = true; }
    m.setIcon(camIcon(cam));
  });
  if (dirty && cluster.refreshClusters) cluster.refreshClusters();
}

/* ---------- Ma'lumot yuklash va yangilash ---------- */
async function loadCameras() {
  const res = await api("/api/cameras");
  applyCameras(res);
  rebuildMarkers();
}

function applyCameras(res) {
  // Uzildi/ulandi hodisalarini server o'zi yozib boradi (core/stats.py) —
  // dashboard ularni /api/stats/dashboard dan oladi, bu yerda takrorlamaymiz.
  state.cameras = res.cameras;
  state.byId = new Map(res.cameras.map((c) => [c.id, c]));
  // Tanlangan kamera o'chirilgan bo'lsa panel yopiladi.
  if (state.selectedId && !state.byId.has(state.selectedId)) closeSel();
  renderList();
  renderStrip();
  renderDash();
  updateSelHead();
}

async function refreshStatus() {
  let res;
  try { res = await api("/api/cameras"); } catch (e) { return; }
  const changed = res.cameras.length !== state.cameras.length ||
                  res.cameras.some((c) => !state.byId.has(c.id));
  applyCameras(res);
  if (changed) rebuildMarkers(); else refreshMarkerIcons();
  // Boshqaruv jadvali ochiq bo'lsa, undagi Holat ustuni ham yangilanadi.
  if (state.tab === "admin" && state.admin) {
    loadAdminCameras(state.adminOffset).catch(() => {});
  }
}
setInterval(refreshStatus, 60000);

/* ---------- Chap ro'yxat ---------- */

/* Ma'lumot o'zgarmagan bo'lsa ro'yxat qayta chizilmaydi — 60 soniyalik
   yangilanish foydalanuvchi qarab turgan ro'yxatni "sakratmaydi". */
let lastListSig = "";

function renderList(force) {
  const cams = visibleCams();
  const sig = [state.filter, state.q, state.selectedId,
    state.cameras.map((c) => c.id + (c.online === false ? "d" : c.online ? "u" : "?")).join("")
  ].join("|");
  if (!force && sig === lastListSig) return;
  lastListSig = sig;

  $("list-count").textContent = cams.length + " / " + state.cameras.length;

  // Filtr tugmalarida jonli hisob ko'rinadi.
  const onCount = state.cameras.filter((c) => c.online === true).length;
  const offCount = state.cameras.filter((c) => c.online === false).length;
  const fLabels = { all: "Hammasi " + state.cameras.length,
                    online: "Onlayn " + onCount, offline: "Uzilgan " + offCount };
  document.querySelectorAll("#filters button").forEach((b) => {
    b.textContent = fLabels[b.dataset.filter];
  });

  const regions = [...new Set(cams.map((c) => c.region))];
  const body = $("list-body");
  body.innerHTML = regions.length ? "" :
    '<div class="empty">Kamera topilmadi.</div>';

  regions.forEach((region) => {
    const list = cams.filter((c) => c.region === region);
    const down = list.filter((c) => c.online === false).length;
    const known = list.some((c) => c.online === true);
    const grp = document.createElement("div");
    grp.className = "grp" + (state.openRegions[region] ? " open" : "");

    const headRow = document.createElement("div");
    headRow.className = "grp-row";
    headRow.innerHTML =
      '<button class="grp-head">' +
        '<span class="caret">&#9654;</span>' +
        '<span class="st' + (down ? " down" : known ? "" : " unk") + '"></span>' +
        '<span class="rg">' + esc(region) + "</span>" +
        '<span class="bdg' + (down ? " down" : "") + '">' +
          (down ? list.length + " &middot; " + down + "&darr;" : list.length) + "</span>" +
      "</button>" +
      '<button class="grp-fly" title="Xaritada ko\'rsatish">&#9678;</button>';
    headRow.querySelector(".grp-head").addEventListener("click", () => {
      state.openRegions[region] = !state.openRegions[region];
      grp.classList.toggle("open", state.openRegions[region]);
    });
    headRow.querySelector(".grp-fly").addEventListener("click", () => flyToRegion(region));
    grp.appendChild(headRow);

    const wrap = document.createElement("div");
    wrap.className = "grp-cams";
    list.forEach((cam) => {
      const row = document.createElement("button");
      row.className = "cam-row" + (cam.online === false ? " down" : "") +
                      (cam.id === state.selectedId ? " sel" : "");
      row.innerHTML =
        '<span class="dot"></span>' +
        '<span class="nm">' + esc(cam.name) + "</span>" +
        '<span class="cdx">' + esc(cam.codec || "") + "</span>";
      row.addEventListener("click", () => { hideCamTip(); selectCamera(cam.id, true); });
      row.addEventListener("mouseenter", (e) => { prewarm(cam); showCamTip(cam, row); });
      row.addEventListener("mouseleave", hideCamTip);
      wrap.appendChild(row);
    });
    grp.appendChild(wrap);
    body.appendChild(grp);
  });

  renderFootStats();
}

/* Hududdagi barcha kameralar sig'adigan qilib xaritani yaqinlashtiradi. */
function flyToRegion(region) {
  const pts = state.cameras.filter((c) => c.region === region)
    .map((c) => [c.lat, c.lng]);
  if (!pts.length) return;
  if (pts.length === 1) map.flyTo(pts[0], 13, { duration: 0.6 });
  else map.flyToBounds(L.latLngBounds(pts).pad(0.3), { duration: 0.6 });
}

/* Hammasini ochish/yopish */
$("list-exp").addEventListener("click", () => {
  const regions = [...new Set(state.cameras.map((c) => c.region))];
  const anyClosed = regions.some((r) => !state.openRegions[r]);
  regions.forEach((r) => { state.openRegions[r] = anyClosed; });
  renderList(true);
});

/* ---------- Kamera surat-ko'rinishi (hover tooltip) ---------- */
function showCamTip(cam, row) {
  const tip = $("cam-tip");
  const img = tip.querySelector("img");
  img.hidden = false;
  img.onerror = () => { img.hidden = true; };
  // 8 soniyalik server keshi bilan mos — bir xil manzil qayta so'ralmaydi.
  img.src = "/api/cameras/" + cam.id + "/snapshot?t=" + Math.floor(Date.now() / 8000);
  tip.querySelector(".cap").textContent = cam.online === false
    ? "O'chiq · oxirgi onlayn: " + fmtLastSeen(cam.last_seen)
    : [cam.codec, cam.always_on ? "doim tayyor" : "jonli"].filter(Boolean).join(" · ");
  const r = row.getBoundingClientRect();
  tip.style.left = (r.right + 10) + "px";
  tip.style.top = Math.max(80, Math.min(r.top - 40, innerHeight - 200)) + "px";
  tip.style.display = "block";
}
function hideCamTip() { $("cam-tip").style.display = "none"; }
$("list-body").addEventListener("scroll", hideCamTip);

function renderFootStats() {
  const t = state.openTimes;
  $("stat-open").innerHTML = t.length
    ? (t.reduce((s, v) => s + v, 0) / t.length / 1000).toFixed(2).replace(".", ",") + "s"
    : "&mdash;";
  // Faqat chindan o'ynayotgan oqimlar sanaladi.
  const live = [selPlayer, ...wallPlayers]
    .filter((p) => p && p.video && !p.video.paused).length;
  $("stat-live").textContent = live + "/" + state.cameras.length;
}

function setListOpen(open) {
  state.listOpen = open;
  $("list-panel").hidden = !open;
  $("strip").classList.toggle("shift", open);
}
$("list-hide").addEventListener("click", () => setListOpen(false));
$("toggle-list").addEventListener("click", () => setListOpen(!state.listOpen));

function setFilter(f) {
  state.filter = f;
  document.querySelectorAll("#filters button").forEach((x) =>
    x.classList.toggle("on", x.dataset.filter === f));
  renderList();
  rebuildMarkers();
}
document.querySelectorAll("#filters button").forEach((b) =>
  b.addEventListener("click", () => setFilter(b.dataset.filter)));

/* Pastki chiplar ham filtr sifatida ishlaydi. */
function chipFilter(f) {
  if (state.tab !== "map") showTab("map");
  setFilter(f);
  setListOpen(true);
}
$("chip-on").addEventListener("click", () => chipFilter("online"));
$("chip-off").addEventListener("click", () => chipFilter("offline"));
$("chip-reg").addEventListener("click", () => chipFilter("all"));

/* ---------- Qidiruv ---------- */
let qTimer = null;
$("q-input").addEventListener("input", (e) => {
  state.q = e.target.value;
  clearTimeout(qTimer);
  qTimer = setTimeout(() => { renderList(); rebuildMarkers(); }, 300);
});
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    $("q-input").focus();
  }
});

/* ---------- Pastki chiziqcha ---------- */
function renderStrip() {
  const on = state.cameras.filter((c) => c.online === true).length;
  const off = state.cameras.filter((c) => c.online === false).length;
  $("strip-on").textContent = on;
  $("strip-off").textContent = off;
  $("strip-reg").textContent = new Set(state.cameras.map((c) => c.region)).size;
}

/* ---------- Video pleyer (WebRTC -> HLS) ----------
   Har bir pleyer o'z holatini olib yuradi — devorda bir nechta birga ishlaydi. */

const FAIL_MSG = "Oqim ochilmadi — MediaMTX ishlayaptimi va kamera ulanganmi tekshiring";

function createPlayer(video, msgEl) {
  const p = { video, msgEl, hls: null, pc: null, token: 0, onOpen: null };

  p.stop = () => {
    p.token++;
    if (p.hls) { p.hls.destroy(); p.hls = null; }
    if (p.pc) { p.pc.close(); p.pc = null; }
    video.pause();
    video.removeAttribute("src");
    video.srcObject = null;
    video.load();
  };

  p.open = (cam, useHevc, quality) => {
    p.stop();
    const my = ++p.token;
    const stale = () => p.token !== my;
    const t0 = performance.now();
    msgEl.textContent = "Ulanmoqda…";
    if (!video.poster) video.poster = "/api/cameras/" + cam.id + "/snapshot";

    const opened = () => {
      if (stale()) return;
      msgEl.textContent = "";
      const ms = performance.now() - t0;
      state.openTimes.push(ms);
      if (state.openTimes.length > 50) state.openTimes.shift();
      state.openByCam.set(cam.id, ms);   // dashboard: kamera kesimida oxirgi o'lchov
      renderFootStats();
      renderDashMetrics();
      if (p.onOpen) p.onOpen(ms, p.mode);
    };
    video.addEventListener("playing", opened, { once: true });

    api("/api/cameras/" + cam.id + "/stream?hevc=" + (useHevc ? 1 : 0) +
        (quality ? "&quality=" + quality : ""))
      .then((urls) => {
        if (stale()) return;
        p.mode = urls.mode;
        // Sub oqim ishlamasa — asosiyga; xom H.265 amalda o'qilmasa —
        // bir marta o'girilganiga qaytamiz.
        const onFail = urls.mode === "sub"
          ? () => { if (!stale()) p.open(cam, useHevc); }
          : urls.mode === "raw"
            ? () => { if (!stale()) p.open(cam, false); }
            : () => { if (!stale()) msgEl.textContent = FAIL_MSG; };
        attach(urls, stale, onFail);
      })
      .catch((e) => { if (!stale()) msgEl.textContent = e.message; });

    function attach(urls, staleFn, onFail) {
      if (urls.webrtc_url) {
        playWebRtc(urls.webrtc_url, staleFn).catch(() => {
          if (staleFn()) return;
          msgEl.textContent = "Zaxira yo'l orqali ulanmoqda…";
          playHls(urls.stream_url, staleFn, onFail);
        });
        return;
      }
      if (!urls.stream_url) { msgEl.textContent = "Oqim manzili sozlanmagan"; return; }
      playHls(urls.stream_url, staleFn, onFail);
    }

    async function playWebRtc(whepUrl, staleFn) {
      const pc = new RTCPeerConnection({ iceServers: [] });
      p.pc = pc;
      pc.addTransceiver("video", { direction: "recvonly" });
      pc.ontrack = (e) => {
        if (staleFn()) return;
        video.srcObject = e.streams[0];
        video.play().catch(() => {});
      };
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await new Promise((resolve) => {          // ICE (ko'pi bilan 900 ms)
        if (pc.iceGatheringState === "complete") return resolve();
        const done = () => { pc.removeEventListener("icegatheringstatechange", check); resolve(); };
        const check = () => { if (pc.iceGatheringState === "complete") done(); };
        pc.addEventListener("icegatheringstatechange", check);
        setTimeout(done, 900);
      });
      const res = await fetch(whepUrl, {
        method: "POST", headers: { "Content-Type": "application/sdp" },
        body: pc.localDescription.sdp
      });
      if (!res.ok) { pc.close(); throw new Error("WHEP " + res.status); }
      const answer = await res.text();
      if (staleFn()) { pc.close(); return; }
      await pc.setRemoteDescription({ type: "answer", sdp: answer });
      await new Promise((resolve, reject) => {  // 6 s da tasvir kelmasa — HLS
        const timer = setTimeout(() => {
          if (video.srcObject) resolve();
          else { pc.close(); reject(new Error("WebRTC jim")); }
        }, 6000);
        pc.addEventListener("connectionstatechange", () => {
          if (pc.connectionState === "connected") { clearTimeout(timer); resolve(); }
          if (pc.connectionState === "failed") { clearTimeout(timer); pc.close(); reject(new Error("WebRTC uzildi")); }
        });
      });
    }

    function playHls(url, staleFn, onFail) {
      if (!url) { msgEl.textContent = FAIL_MSG; return; }
      const isHls = url.includes(".m3u8");
      if (isHls && window.Hls && Hls.isSupported()) {
        const hls = new Hls({
          lowLatencyMode: true, maxBufferLength: 6, backBufferLength: 6,
          liveSyncDurationCount: 1,
          manifestLoadingTimeOut: 25000     // sovuq start 5 soniyagacha cho'ziladi
        });
        p.hls = hls;
        hls.loadSource(url);
        hls.attachMedia(video);
        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          if (!staleFn()) video.play().catch(() => {});
        });
        hls.on(Hls.Events.ERROR, (_, d) => {
          if (!d.fatal || staleFn()) return;
          // Sub oqimda har qanday jiddiy xato — asosiyga qaytish sababi
          // (sub yo'l NVR'da o'chirilgan bo'lishi mumkin).
          if (onFail && (d.type === Hls.ErrorTypes.MEDIA_ERROR || p.mode === "sub")) {
            hls.destroy(); onFail(); return;
          }
          msgEl.textContent = FAIL_MSG;
        });
      } else if (isHls && video.canPlayType("application/vnd.apple.mpegurl")) {
        video.src = url;
        video.addEventListener("loadedmetadata", () => {
          if (!staleFn()) video.play().catch(() => {});
        }, { once: true });
        video.onerror = () => { if (!staleFn()) msgEl.textContent = FAIL_MSG; };
      } else {
        video.src = url;
        video.onerror = () => { if (!staleFn()) msgEl.textContent = FAIL_MSG; };
        video.play().catch(() => {});
      }
    }
  };
  return p;
}

/* Kamerani oldindan uyg'otish — sichqoncha kelganda oqim va surat tayyorlanadi. */
const warmed = new Map();
function prewarm(cam) {
  const last = warmed.get(cam.id) || 0;
  if (Date.now() - last < 30000) return;      // MediaMTX 60 s ushlab turadi
  warmed.set(cam.id, Date.now());
  fetch("/api/cameras/" + cam.id + "/snapshot", { cache: "no-store" }).catch(() => {});
  api("/api/cameras/" + cam.id + "/stream?hevc=" + (HEVC_OK ? 1 : 0))
    .then((urls) => {
      if (!urls.stream_url) return;
      fetch(urls.stream_url, { cache: "no-store", mode: "no-cors" }).catch(() => {});
    })
    .catch(() => {});
}

/* ---------- Tanlangan kamera paneli ---------- */
let selPlayer = null;

function fmtLastSeen(iso) {
  if (!iso) return "ma'lum emas";
  const t = new Date(iso);
  if (isNaN(t)) return "ma'lum emas";
  const diff = (Date.now() - t.getTime()) / 1000;
  if (diff < 90) return "hozirgina";
  if (diff < 3600) return Math.round(diff / 60) + " daqiqa oldin";
  if (diff < 86400) return Math.round(diff / 3600) + " soat oldin";
  const p = (n) => String(n).padStart(2, "0");
  return p(t.getDate()) + "." + p(t.getMonth() + 1) + "." + t.getFullYear() +
         " " + p(t.getHours()) + ":" + p(t.getMinutes());
}

function selectCamera(id, fly) {
  state.selectedId = id;
  const cam = state.byId.get(id);
  if (!cam) return;
  // Boshqa tab'dan kelinsa avval xaritaga o'tamiz — showTab o'zi qayta chaqiradi.
  if (state.tab !== "map") { showTab("map"); return; }
  if (fly !== false) map.flyTo([cam.lat, cam.lng], Math.max(map.getZoom(), 13), { duration: 0.6 });

  $("sel-panel").hidden = false;
  updateSelHead();
  renderList();
  refreshMarkerIcons();

  const video = $("sel-video");
  video.poster = "";
  video.poster = "/api/cameras/" + cam.id + "/snapshot?" + Date.now();
  if (!selPlayer) selPlayer = createPlayer(video, $("sel-msg"));
  selPlayer.onOpen = (ms, mode) => {
    $("sel-f-open").textContent = (ms / 1000).toFixed(2).replace(".", ",") + " s";
    const modeText = { raw: "xom H.265", direct: "to'g'ridan-to'g'ri",
                       transcode: "H.264 ga o'girilgan", manual: "tashqi oqim" }[mode] || mode;
    $("sel-f-mode").textContent = cam.always_on ? "doim tayyor" : modeText;
    addEvent(cam.name + " — oqim ochildi (" + (ms / 1000).toFixed(1) + " s)", "ok");
  };
  $("sel-f-open").innerHTML = "&mdash;";
  selPlayer.open(cam, HEVC_OK);
  // O'chiq kamera — kutish o'rniga darhol sabab ko'rsatiladi (oqim baribir
  // sinab ko'riladi: tekshiruv 60 soniya eskirgan bo'lishi mumkin).
  if (cam.online === false) {
    $("sel-msg").textContent =
      "Kamera o'chiq · oxirgi onlayn: " + fmtLastSeen(cam.last_seen);
  }
  renderFootStats();
}

function updateSelHead() {
  const cam = state.byId.get(state.selectedId);
  if (!cam) { $("sel-panel").hidden = true; return; }
  const down = cam.online === false;
  $("sel-name").textContent = cam.name;
  $("sel-sub").textContent = cam.region + " · " +
    (down ? "uzilgan · oxirgi onlayn: " + fmtLastSeen(cam.last_seen) : "jonli oqim");
  $("sel-dot").classList.toggle("down", down);
  $("sel-badge").classList.toggle("down", down);
  $("sel-badge-tx").textContent = down ? "OFFLINE" : "LIVE";
  $("sel-f-codec").textContent = cam.codec || "—";
  $("sel-f-mode").textContent = cam.always_on ? "doim tayyor" : "so'rov bo'yicha";
  const p = (n) => String(n).padStart(2, "0");
  const now = new Date();
  $("sel-stamp").textContent = p(now.getDate()) + "-" + p(now.getMonth() + 1) + "-" +
    now.getFullYear() + " " + p(now.getHours()) + ":" + p(now.getMinutes());
}

function closeSel() {
  state.selectedId = null;
  $("sel-panel").hidden = true;
  if (selPlayer) selPlayer.stop();
  renderList();
  refreshMarkerIcons();
  renderFootStats();
}
$("sel-close").addEventListener("click", closeSel);

$("sel-full").addEventListener("click", () => {
  const v = $("sel-video");
  (v.requestFullscreen || v.webkitEnterFullscreen || function(){}).call(v);
});

$("sel-wall").addEventListener("click", () => {
  const id = state.selectedId;
  if (id && !state.pinned.includes(id)) state.pinned.push(id);
  showTab("wall");
});

/* ---------- Video devor ---------- */
let wallPlayers = [];
let wallAutoTimer = null;

/* Devor sozlamalari brauzerda saqlanadi — qayta ochilganda tiklanadi. */
function saveWallPrefs() {
  try {
    localStorage.setItem("nigoh-wall", JSON.stringify({
      size: state.wallSize, fit: state.wallFit,
      region: state.wallRegion, auto: state.wallAuto
    }));
  } catch (e) {}
}
function loadWallPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem("nigoh-wall") || "{}");
    if ([2, 3, 4].includes(p.size)) state.wallSize = p.size;
    if (p.fit === "cover" || p.fit === "contain") state.wallFit = p.fit;
    if (typeof p.region === "string") state.wallRegion = p.region;
    state.wallAuto = !!p.auto;
  } catch (e) {}
  document.querySelectorAll("#wall-sizes button").forEach((b) =>
    b.classList.toggle("on", Number(b.dataset.wsize) === state.wallSize));
}
loadWallPrefs();

/* Devorga tushadigan kameralar: biriktirilganlar oldinda, keyin qolganlar. */
function wallCams() {
  const seen = new Set();
  const out = [];
  const fits = (cam) =>
    !state.wallHidden.has(cam.id) &&
    (!state.wallRegion || cam.region === state.wallRegion);
  state.pinned.forEach((id) => {
    const cam = state.byId.get(id);
    if (cam && !seen.has(id) && fits(cam)) { seen.add(id); out.push(cam); }
  });
  state.cameras.forEach((cam) => {
    if (!seen.has(cam.id) && cam.online !== false && fits(cam)) {
      seen.add(cam.id); out.push(cam);
    }
  });
  return out;
}

function fillWallRegions() {
  const sel = $("wall-region");
  const regions = [...new Set(state.cameras.map((c) => c.region))].sort();
  const cur = state.wallRegion;
  sel.innerHTML = '<option value="">Barcha hududlar</option>' +
    regions.map((r) => '<option value="' + esc(r) + '"' +
      (r === cur ? " selected" : "") + ">" + esc(r) + "</option>").join("");
  if (cur && !regions.includes(cur)) { state.wallRegion = ""; sel.value = ""; }
}

function buildWall() {
  stopWall();
  fillWallRegions();
  const all = wallCams();
  const slots = state.wallSize * state.wallSize;
  const pages = Math.max(1, Math.ceil(all.length / slots));
  state.wallPage = Math.min(state.wallPage, pages - 1);
  const cams = all.slice(state.wallPage * slots, state.wallPage * slots + slots);

  const grid = $("wall-grid");
  grid.classList.toggle("cover", state.wallFit === "cover");
  // Setka kamera soniga moslashadi: 2 ta kamera 2×2 katakka qisilmaydi,
  // butun ekranni to'ldiradi. Tanlangan o'lcham — yuqori chegara.
  const n = Math.max(1, cams.length);
  const cols = Math.min(state.wallSize, Math.ceil(Math.sqrt(n)));
  const rows = Math.min(state.wallSize, Math.ceil(n / cols));
  grid.style.gridTemplateColumns = "repeat(" + cols + ",1fr)";
  grid.style.gridTemplateRows = "repeat(" + rows + ",1fr)";

  $("wall-label").textContent = state.wallSize + "×" + state.wallSize +
    " · " + all.length + " kamera" +
    (state.wallRegion ? " · " + state.wallRegion : "") +
    (state.pinned.length ? " · " + state.pinned.length + " biriktirilgan" : "");
  $("wall-page").textContent = pages > 1 ? (state.wallPage + 1) + " / " + pages : "";
  $("wall-prev").disabled = state.wallPage === 0;
  $("wall-next").disabled = state.wallPage >= pages - 1;
  $("wall-pager").style.display = pages > 1 ? "" : "none";
  $("wall-fit").textContent = state.wallFit === "cover" ? "Kadr: to'liq" : "Kadr: butun";
  $("wall-auto").classList.toggle("soft", state.wallAuto);

  grid.innerHTML = cams.length ? "" :
    '<div class="empty" style="grid-column:1/-1">Ko‘rsatiladigan kamera yo‘q.</div>';

  cams.forEach((cam) => {
    const down = cam.online === false;
    const pinned = state.pinned.includes(cam.id);
    const tile = document.createElement("div");
    tile.className = "tile" + (down ? " down" : "") +
                     (cam.id === state.selectedId ? " sel" : "");
    tile.innerHTML =
      '<video muted playsinline poster="/api/cameras/' + cam.id + '/snapshot"></video>' +
      '<div class="t-msg"></div>' +
      '<div class="t-head"><i></i><span class="nm">' + esc(cam.name) + "</span>" +
        '<span class="st">' + (down ? "OFFLINE" : "LIVE") + "</span></div>" +
      '<div class="t-btns">' +
        '<button data-w="pin" class="' + (pinned ? "on" : "") +
          '" title="Devorga biriktirish">&#9733;</button>' +
        '<button data-w="shot" title="Suratini yuklab olish">&#8681;</button>' +
        '<button data-w="full" title="To\'liq ekran">&#10530;</button>' +
        '<button data-w="x" title="Devordan olish">&times;</button>' +
      "</div>" +
      '<div class="t-foot"><span>' + esc(cam.region) + "</span><span>" +
        esc(cam.codec || "") + '</span><span style="margin-left:auto"></span></div>';

    const on = (act, fn) => tile.querySelector('[data-w="' + act + '"]')
      .addEventListener("click", (e) => { e.stopPropagation(); fn(); });
    on("pin", () => {
      state.pinned = pinned ? state.pinned.filter((x) => x !== cam.id)
                            : [cam.id, ...state.pinned];
      buildWall();
    });
    on("shot", async () => {
      try {
        const blob = await (await fetch("/api/cameras/" + cam.id + "/snapshot")).blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = cam.name.replace(/[^\w\-]+/g, "_") + ".jpg";
        a.click();
        URL.revokeObjectURL(a.href);
      } catch (e) { toast("Surat olinmadi", true); }
    });
    // Plitkaning o'zi to'liq ekranga chiqadi — nomi, LIVE belgisi va
    // pastki ma'lumotlar saqlanib qoladi. Qayta bosish/ESC — chiqish.
    const goFull = () => {
      if (document.fullscreenElement) { document.exitFullscreen(); return; }
      const v = tile.querySelector("video");
      if (tile.requestFullscreen) tile.requestFullscreen();
      else if (v.webkitEnterFullscreen) v.webkitEnterFullscreen();
    };
    on("full", goFull);
    on("x", () => {
      state.wallHidden.add(cam.id);
      state.pinned = state.pinned.filter((x) => x !== cam.id);
      buildWall();
    });
    tile.addEventListener("click", () => selectCamera(cam.id, true));
    tile.addEventListener("dblclick", goFull);
    grid.appendChild(tile);

    if (!down) {
      const player = createPlayer(tile.querySelector("video"), tile.querySelector(".t-msg"));
      const msEl = tile.querySelector(".t-foot span:last-child");
      player.onOpen = (ms) => { msEl.textContent = (ms / 1000).toFixed(2) + "s"; };
      // Setkada past sifatli 2-oqim (sub) — 16 plitka tarmoqni bo'g'masin.
      // Sub bo'lmasa server asosiysini beradi; to'liq ekranda asosiyga o'tiladi.
      player.open(cam, HEVC_OK, "sub");
      tile.addEventListener("fullscreenchange", () => {
        player.open(cam, HEVC_OK, document.fullscreenElement === tile ? "" : "sub");
      });
      wallPlayers.push(player);
    } else {
      tile.querySelector(".t-msg").textContent = "ulanish yo'q";
    }
  });
  renderFootStats();
  syncWallAuto();
}

function stopWall() {
  wallPlayers.forEach((p) => p.stop());
  wallPlayers = [];
  clearInterval(wallAutoTimer);
  wallAutoTimer = null;
  renderFootStats();
}

/* Avto-aylanish: bir necha sahifa bo'lsa, har 12 soniyada keyingisiga o'tadi. */
function syncWallAuto() {
  clearInterval(wallAutoTimer);
  wallAutoTimer = null;
  if (!state.wallAuto || state.tab !== "wall") return;
  wallAutoTimer = setInterval(() => {
    const pages = Math.max(1, Math.ceil(wallCams().length /
      (state.wallSize * state.wallSize)));
    if (pages < 2) return;
    state.wallPage = (state.wallPage + 1) % pages;
    buildWall();
  }, 12000);
}

document.querySelectorAll("#wall-sizes button").forEach((b) =>
  b.addEventListener("click", () => {
    state.wallSize = Number(b.dataset.wsize);
    state.wallPage = 0;
    document.querySelectorAll("#wall-sizes button").forEach((x) =>
      x.classList.toggle("on", x === b));
    saveWallPrefs();
    buildWall();
  }));
$("wall-region").addEventListener("change", (e) => {
  state.wallRegion = e.target.value;
  state.wallPage = 0;
  saveWallPrefs();
  buildWall();
});
$("wall-fit").addEventListener("click", () => {
  state.wallFit = state.wallFit === "cover" ? "contain" : "cover";
  saveWallPrefs();
  buildWall();
});
$("wall-auto").addEventListener("click", () => {
  state.wallAuto = !state.wallAuto;
  saveWallPrefs();
  buildWall();
});
$("wall-prev").addEventListener("click", () => {
  state.wallPage = Math.max(0, state.wallPage - 1);
  buildWall();
});
$("wall-next").addEventListener("click", () => {
  state.wallPage++;
  buildWall();
});

/* ---------- Dashboard ---------- */
function addEvent(text, kind) {
  state.events.unshift({ t: Date.now(), text, kind });
  if (state.events.length > 50) state.events.pop();
  renderEvents();
}

function renderDashMetrics() {
  const total = state.cameras.length;
  const on = state.cameras.filter((c) => c.online === true).length;
  const off = state.cameras.filter((c) => c.online === false).length;
  $("m-total").textContent = total;
  $("m-total-note").textContent = new Set(state.cameras.map((c) => c.region)).size + " hududda";
  $("m-online").textContent = total ? Math.round((on / total) * 100) + "%" : "—";
  $("m-online-note").textContent = on + " ta javob beryapti";
  const t = state.openTimes;
  $("m-open").textContent = t.length
    ? (t.reduce((s, v) => s + v, 0) / t.length / 1000).toFixed(2).replace(".", ",")
    : "—";
  $("m-open-note").textContent = t.length
    ? "shu seansda " + t.length + " o'lchov" : "hali oqim ochilmadi";
  $("m-down").textContent = off;
  $("m-down-note").textContent = off ? "tekshirish talab qiladi"
    : state.stats ? "bugun " + state.stats.events_today + " ta uzilish"
    : "hammasi joyida";
  renderDonut();
  renderSpark();
}

/* Holat taqsimoti — donut. Markazda bosh ko'rsatkich: onlayn foizi. */
function renderDonut() {
  const on = state.cameras.filter((c) => c.online === true).length;
  const off = state.cameras.filter((c) => c.online === false).length;
  const unk = state.cameras.length - on - off;
  const total = state.cameras.length || 1;
  const parts = [
    { label: "Onlayn", n: on, color: "var(--ok)" },
    { label: "Uzilgan", n: off, color: "var(--danger)" },
    { label: "Noma'lum", n: unk, color: "var(--faint)" },
  ].filter((p) => p.n > 0);

  const R = 46, C = 2 * Math.PI * R;
  const gap = parts.length > 1 ? 3 : 0;   // segmentlar orasidagi "havo"
  let acc = 0;
  const segs = parts.map((p) => {
    const frac = p.n / total;
    const len = Math.max(C * frac - gap, 0.5);
    const s = '<circle cx="60" cy="60" r="' + R + '" fill="none" pathLength="' + C.toFixed(2) +
      '" style="stroke:' + p.color + ';stroke-width:13" stroke-dasharray="' +
      len.toFixed(2) + " " + C.toFixed(2) + '" stroke-dashoffset="' + (-acc - gap / 2).toFixed(2) +
      '" transform="rotate(-90 60 60)"><title>' + p.label + ": " + p.n + " ta</title></circle>";
    acc += C * frac;
    return s;
  }).join("");
  const pct = Math.round((on / total) * 100);
  $("donut").innerHTML = segs +
    '<text x="60" y="58" text-anchor="middle" style="font:700 23px var(--font),sans-serif;fill:var(--text)">' + pct + "%</text>" +
    '<text x="60" y="76" text-anchor="middle" style="font:700 8.5px var(--font),sans-serif;letter-spacing:.1em;fill:var(--faint)">ONLAYN</text>';
  $("donut-legend").innerHTML = parts.map((p) =>
    '<div class="dl"><i style="background:' + p.color + '"></i>' + p.label +
    " <b>" + p.n + " ta</b></div>").join("");
}

/* Ochilish vaqtlari sparkline'i — seansdagi so'nggi 24 o'lchov. */
function renderSpark() {
  const el = $("m-open-spark");
  const data = state.openTimes.slice(-24);
  if (data.length < 2) { el.innerHTML = ""; return; }
  const W = 200, H = 30, P = 4;
  const max = Math.max(...data), min = Math.min(...data);
  const x = (i) => P + (W - 2 * P) * i / (data.length - 1);
  const y = (v) => max === min ? H / 2 : H - P - (H - 2 * P) * (v - min) / (max - min);
  const pts = data.map((v, i) => x(i).toFixed(1) + "," + y(v).toFixed(1)).join(" ");
  const lx = x(data.length - 1).toFixed(1), ly = y(data[data.length - 1]).toFixed(1);
  el.innerHTML =
    '<polygon points="' + P + "," + (H - P) + " " + pts + " " + lx + "," + (H - P) +
      '" style="fill:var(--accent);opacity:.1"/>' +
    '<polyline points="' + pts + '" vector-effect="non-scaling-stroke" ' +
      'style="fill:none;stroke:var(--accent);stroke-width:2;stroke-linejoin:round;stroke-linecap:round"/>' +
    '<circle cx="' + lx + '" cy="' + ly + '" r="3.5" style="fill:var(--accent);stroke:var(--surface-2);stroke-width:2"/>';
}

function renderDash() {
  renderDashMetrics();
  renderRegions();
  renderTech();
  renderSlow();
  renderEvents();
  renderTimeline();
  renderDailyCharts();
  renderHourly();
  loadStats();
}

/* Tarixiy statistika serverdan olinadi — kelgach grafiklar qayta chiziladi. */
let statsLoading = false;
async function loadStats() {
  if (statsLoading) return;
  statsLoading = true;
  try {
    state.stats = await api("/api/stats/dashboard");
    if (state.tab === "dash") {
      renderDashMetrics();
      renderRegions();
      renderEvents();
      renderTimeline();
      renderDailyCharts();
      renderHourly();
    }
  } catch (e) { /* endpoint bo'lmasa — jonli qism ishlayveradi */ }
  statsLoading = false;
}

/* Hududlar jadvali: joriy holat + 24 soatlik o'rtacha + bugungi uzilishlar. */
function renderRegions() {
  const regions = [...new Set(state.cameras.map((c) => c.region))].sort();
  const rstats = new Map(
    ((state.stats && state.stats.regions) || []).map((r) => [r.region, r]));
  const head = '<div class="rrow head"><span class="rg"></span>' +
    '<span class="bar-h">Hozir</span><span class="lb">Onlayn</span>' +
    '<span class="lb2" title="24 soatlik o\'rtacha onlayn">24 soat</span>' +
    '<span class="lb2" title="Bugungi uzilish hodisalari">Uzilish</span></div>';
  $("region-rows").innerHTML = head + regions.map((region) => {
    const list = state.cameras.filter((c) => c.region === region);
    const up = list.filter((c) => c.online !== false).length;
    const pct = list.length ? Math.round((up / list.length) * 100) : 0;
    const color = pct === 100 ? "var(--ok)" : pct >= 60 ? "var(--accent)" : "var(--danger)";
    const st = rstats.get(region);
    const up24 = st && st.uptime24 != null ? Math.round(st.uptime24) + "%" : "—";
    const ev = st ? st.events_today : null;
    return '<div class="rrow click" data-region="' + esc(region) + '"><span class="rg">' + esc(region) + "</span>" +
      '<div class="bar"><i style="width:' + pct + "%;background:" + color + '"></i></div>' +
      '<span class="lb">' + up + "/" + list.length + " · " + pct + "%</span>" +
      '<span class="lb2">' + up24 + "</span>" +
      '<span class="lb2' + (ev ? " bad" : "") + '">' +
        (ev == null ? "—" : ev ? ev + "&darr;" : "0") + "</span></div>";
  }).join("");
  // Hudud qatori bosilsa — xaritaga o'tib, o'sha hudud kameralari ko'rsatiladi.
  document.querySelectorAll("#region-rows .rrow.click").forEach((row) =>
    row.addEventListener("click", () => {
      const region = row.dataset.region;
      showTab("map");
      $("q-input").value = region;
      state.q = region;
      renderList();
      const pts = state.cameras.filter((c) => c.region === region && c.lat != null);
      if (pts.length) {
        const b = L.latLngBounds(pts.map((c) => [c.lat, c.lng]));
        map.fitBounds(b.pad(0.35));
      }
    }));
}

/* Texnik kesim: kodeklar, o'girish va rejimlar taqsimoti. */
function renderTech() {
  const total = state.cameras.length || 1;
  // Bitta o'lchov (ulush) — bitta rang: qatorlar yorliq bilan farqlanadi.
  const groups = [
    ["H.265 xom (o'girishsiz)", state.cameras.filter((c) => /h265|hevc/i.test(c.codec || "") && !c.transcode).length, "var(--accent)"],
    ["H.265 → H.264 o'girish", state.cameras.filter((c) => c.transcode).length, "var(--accent)"],
    ["H.264 to'g'ridan-to'g'ri", state.cameras.filter((c) => /h264|avc/i.test(c.codec || "") && !c.transcode).length, "var(--accent)"],
    ["Doim tayyor rejimda", state.cameras.filter((c) => c.always_on).length, "var(--accent)"],
  ];
  $("tech-rows").innerHTML = groups.map(([label, n, color]) => {
    const pct = Math.round((n / total) * 100);
    return '<div class="rrow"><span class="rg wide">' + label + "</span>" +
      '<div class="bar"><i style="width:' + pct + "%;background:" + color + '"></i></div>' +
      '<span class="lb">' + n + " ta · " + pct + "%</span></div>";
  }).join("");
}

/* Shu seansda o'lchangan oqim ochilish vaqtlari — sekinlari yuqorida. */
function renderSlow() {
  const rows = [...state.openByCam.entries()]
    .map(([id, ms]) => ({ cam: state.byId.get(id), ms }))
    .filter((r) => r.cam)
    .sort((a, b) => b.ms - a.ms)
    .slice(0, 8);
  const max = rows.length ? rows[0].ms : 1;
  $("slow-rows").innerHTML = rows.length ? rows.map((r) => {
    const sec = r.ms / 1000;
    const color = sec <= 2 ? "var(--ok)" : sec <= 5 ? "var(--warn)" : "var(--danger)";
    return '<div class="rrow"><span class="rg wide">' + esc(r.cam.name) + "</span>" +
      '<div class="bar"><i style="width:' + Math.max(6, Math.round((r.ms / max) * 100)) +
      "%;background:" + color + '"></i></div>' +
      '<span class="lb">' + sec.toFixed(2) + " s</span></div>";
  }).join("") : '<div class="empty">Hali oqim ochilmadi — kamera oching, o\'lchov shu yerda ko\'rinadi.</div>';
}

/* ---------- Grafiklar (SVG, kutubxonasiz) ----------
   Ranglar CSS o'zgaruvchilaridan olinadi — mavzu almashsa moslashadi. */

const chTip = $("chart-tip");
function chTipShow(value, label, cx, cy) {
  chTip.querySelector(".v").textContent = value;
  chTip.querySelector(".l").textContent = label;
  chTip.style.display = "block";
  const r = chTip.getBoundingClientRect();
  let x = cx + 14, y = cy - r.height - 12;
  if (x + r.width > innerWidth - 8) x = cx - r.width - 14;
  if (y < 8) y = cy + 16;
  chTip.style.left = x + "px";
  chTip.style.top = y + "px";
}
function chTipHide() { chTip.style.display = "none"; }

/* Ustuncha balandligi uchun "chiroyli" yuqori chegara: 4, 5, 10, 20, 50… */
function niceMax(v) {
  if (v <= 4) return 4;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 5, 10]) if (v <= m * p) return m * p;
  return 10 * p;
}

/* Usti 4px yumaloq, asosi tekis ustuncha (dataviz spetsifikatsiyasi). */
function colPath(x, w, yTop, yBase) {
  const r = Math.min(4, w / 2, Math.max(0, yBase - yTop));
  return "M" + x.toFixed(1) + "," + yBase.toFixed(1) +
    " L" + x.toFixed(1) + "," + (yTop + r).toFixed(1) +
    " Q" + x.toFixed(1) + "," + yTop.toFixed(1) + " " + (x + r).toFixed(1) + "," + yTop.toFixed(1) +
    " L" + (x + w - r).toFixed(1) + "," + yTop.toFixed(1) +
    " Q" + (x + w).toFixed(1) + "," + yTop.toFixed(1) + " " + (x + w).toFixed(1) + "," + (yTop + r).toFixed(1) +
    " L" + (x + w).toFixed(1) + "," + yBase.toFixed(1) + " Z";
}

/* 24 soatlik onlayn darajasi — maydonli chiziq, kursorda qiymat ko'rinadi. */
function renderTimeline() {
  const svg = $("ch-timeline"), empty = $("ch-timeline-empty");
  const data = ((state.stats && state.stats.timeline) || [])
    .filter((p) => p.total > 0)
    .map((p) => ({ t: Date.parse(p.ts), online: p.online, total: p.total }));
  if (data.length < 2) {
    svg.innerHTML = "";
    empty.textContent = "Tarix yig'ilmoqda — grafik dastlabki o'lchovlar to'plangach (~10 daqiqa) chiziladi.";
    empty.style.display = "flex";
    return;
  }
  empty.style.display = "none";
  const W = Math.max(320, Math.round(svg.clientWidth) || 640), H = 210;
  const L = 40, R = 18, T = 14, B = 26;
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  const t0 = data[0].t, t1 = data[data.length - 1].t;
  const x = (t) => L + (W - L - R) * (t - t0) / Math.max(1, t1 - t0);
  const pctOf = (p) => (p.online / p.total) * 100;
  const y = (v) => T + (H - T - B) * (1 - v / 100);
  let out = "";
  [0, 25, 50, 75, 100].forEach((v) => {
    out += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y(v).toFixed(1) +
           '" y2="' + y(v).toFixed(1) + '" stroke="var(--line-2)"/>';
    if (v % 50 === 0) out += '<text class="ch-tick" x="' + (L - 8) + '" y="' +
      (y(v) + 3.5).toFixed(1) + '" text-anchor="end">' + v + "%</text>";
  });
  // Vaqt belgilari qadami oraliqqa moslashadi: tarix hali qisqa bo'lsa
  // (server yangi ishga tushgan) 5-15 daqiqalik, to'liq sutkada 4 soatlik.
  const MIN = 60000;
  const step = [5 * MIN, 15 * MIN, 30 * MIN, 60 * MIN, 2 * 60 * MIN,
                4 * 60 * MIN, 6 * 60 * MIN]
    .find((s) => (t1 - t0) / s <= 6) || 6 * 60 * MIN;
  const pd2 = (n) => String(n).padStart(2, "0");
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
    const d = new Date(t);
    out += '<text class="ch-tick" x="' + x(t).toFixed(1) + '" y="' + (H - 8) +
      '" text-anchor="middle">' + pd2(d.getHours()) + ":" + pd2(d.getMinutes()) +
      "</text>";
  }
  const pts = data.map((p) => x(p.t).toFixed(1) + "," + y(pctOf(p)).toFixed(1)).join(" ");
  out += '<polygon points="' + x(t0).toFixed(1) + "," + y(0).toFixed(1) + " " + pts +
    " " + x(t1).toFixed(1) + "," + y(0).toFixed(1) + '" fill="var(--accent)" opacity=".1"/>';
  out += '<polyline points="' + pts + '" fill="none" stroke="var(--accent)" ' +
    'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';
  const last = data[data.length - 1];
  out += '<circle cx="' + x(last.t).toFixed(1) + '" cy="' + y(pctOf(last)).toFixed(1) +
    '" r="4" fill="var(--accent)" stroke="var(--surface-2)" stroke-width="2"/>';
  out += '<line class="ch-cx" y1="' + T + '" y2="' + y(0).toFixed(1) +
    '" stroke="var(--faint)" style="display:none"/>';
  out += '<circle class="ch-dot" r="4" fill="var(--accent)" stroke="var(--surface-2)" ' +
    'stroke-width="2" style="display:none"/>';
  out += '<rect class="ch-hit" x="' + L + '" y="' + T + '" width="' + (W - L - R) +
    '" height="' + (H - T - B) + '" fill="transparent"/>';
  svg.innerHTML = out;

  // Kursor eng yaqin o'lchovga "yopishadi" — 2px chiziqni mo'ljallash shart emas.
  const cx = svg.querySelector(".ch-cx"), dot = svg.querySelector(".ch-dot"),
        hit = svg.querySelector(".ch-hit");
  hit.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect();
    const t = t0 + ((e.clientX - r.left) * (W / r.width) - L) / (W - L - R) * (t1 - t0);
    let best = 0;
    for (let i = 1; i < data.length; i++)
      if (Math.abs(data[i].t - t) < Math.abs(data[best].t - t)) best = i;
    const p = data[best], bx = x(p.t).toFixed(1);
    cx.setAttribute("x1", bx); cx.setAttribute("x2", bx); cx.style.display = "";
    dot.setAttribute("cx", bx); dot.setAttribute("cy", y(pctOf(p)).toFixed(1));
    dot.style.display = "";
    const d = new Date(p.t), pd = (n) => String(n).padStart(2, "0");
    chTipShow(p.online + "/" + p.total + " onlayn · " + Math.round(pctOf(p)) + "%",
              pd(d.getHours()) + ":" + pd(d.getMinutes()), e.clientX, e.clientY);
  });
  hit.addEventListener("pointerleave", () => {
    cx.style.display = "none"; dot.style.display = "none"; chTipHide();
  });
}

/* Umumiy ustunli grafik: items — {label, value, cap, tipValue, tipLabel}. */
function renderColumns(svgId, emptyId, items, opts) {
  const svg = $(svgId), empty = $(emptyId);
  if (!items.some((it) => it.value != null)) {
    svg.innerHTML = "";
    empty.textContent = opts.emptyText;
    empty.style.display = "flex";
    return;
  }
  empty.style.display = "none";
  const W = Math.max(220, Math.round(svg.clientWidth) || 300), H = 170;
  const L = 30, R = 8, T = 18, B = 24;
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  const max = opts.max || niceMax(Math.max(1, ...items.map((it) => it.value || 0)));
  const y = (v) => T + (H - T - B) * (1 - v / max);
  let out = "";
  (opts.max === 100 ? [0, 50, 100] : [0, max / 2, max]).forEach((v) => {
    out += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y(v).toFixed(1) +
      '" y2="' + y(v).toFixed(1) + '" stroke="var(--line-2)"/>' +
      '<text class="ch-tick" x="' + (L - 7) + '" y="' + (y(v) + 3.5).toFixed(1) +
      '" text-anchor="end">' + Math.round(v) + (opts.unit || "") + "</text>";
  });
  const slot = (W - L - R) / items.length;
  const barW = Math.min(24, slot * 0.62);
  items.forEach((it, i) => {
    const cxm = L + slot * i + slot / 2;
    if (it.value != null && it.value > 0)
      out += '<path class="ch-col" data-i="' + i + '" d="' +
        colPath(cxm - barW / 2, barW, y(it.value), y(0)) + '" fill="var(--accent)"/>';
    if (opts.capLabels && it.value != null && it.cap)
      out += '<text class="ch-cap" x="' + cxm.toFixed(1) + '" y="' +
        (y(it.value) - 5).toFixed(1) + '" text-anchor="middle">' + esc(it.cap) + "</text>";
    if (it.label)
      out += '<text class="ch-tick" x="' + cxm.toFixed(1) + '" y="' + (H - 8) +
        '" text-anchor="middle">' + esc(it.label) + "</text>";
    out += '<rect class="ch-slot" data-i="' + i + '" x="' + (L + slot * i).toFixed(1) +
      '" y="' + T + '" width="' + slot.toFixed(1) + '" height="' + (H - T - B) +
      '" fill="transparent"/>';
  });
  svg.innerHTML = out;
  svg.querySelectorAll(".ch-slot").forEach((rect) => {
    const i = Number(rect.dataset.i);
    const bar = svg.querySelector('.ch-col[data-i="' + i + '"]');
    rect.addEventListener("pointermove", (e) => {
      if (bar) bar.style.opacity = ".78";
      chTipShow(items[i].tipValue, items[i].tipLabel, e.clientX, e.clientY);
    });
    rect.addEventListener("pointerleave", () => {
      if (bar) bar.style.opacity = "";
      chTipHide();
    });
  });
}

const UZ_MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun",
                   "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"];
const UZ_WDAYS = ["Ya", "Du", "Se", "Ch", "Pa", "Ju", "Sh"];
function fmtDayLabel(dt) { return dt.getDate() + "-" + UZ_MONTHS[dt.getMonth()]; }

/* 7 kunlik kesim: o'rtacha onlayn % va uzilishlar soni — ikkita alohida panel. */
function renderDailyCharts() {
  const daily = (state.stats && state.stats.daily) || [];
  const items = daily.map((d, i) => ({
    d,
    dt: new Date(d.date + "T00:00:00"),
    last: i === daily.length - 1,
  }));
  renderColumns("ch-daily-up", "ch-daily-up-empty", items.map((it) => ({
    label: it.last ? "Bugun" : UZ_WDAYS[it.dt.getDay()] + " " + it.dt.getDate(),
    value: it.d.uptime,
    cap: it.d.uptime == null ? "" : Math.round(it.d.uptime) + "%",
    tipValue: it.d.uptime == null ? "ma'lumot yo'q"
      : it.d.uptime.toFixed(1).replace(".", ",") + "% onlayn",
    tipLabel: fmtDayLabel(it.dt),
  })), { max: 100, unit: "%", capLabels: true,
         emptyText: "Kunlik tarix hali yig'ilmagan — server ishlagan sari to'lib boradi." });
  renderColumns("ch-daily-ev", "ch-daily-ev-empty", items.map((it) => ({
    label: it.last ? "Bugun" : UZ_WDAYS[it.dt.getDay()] + " " + it.dt.getDate(),
    // O'sha kunga surat ham, hodisa ham yo'q — "0" emas, "ma'lumot yo'q".
    value: it.d.uptime == null && !it.d.events ? null : it.d.events,
    cap: String(it.d.events),
    tipValue: it.d.events + " ta uzilish",
    tipLabel: fmtDayLabel(it.dt),
  })), { capLabels: true, emptyText: "Kunlik tarix hali yig'ilmagan." });
}

/* Bugungi uzilishlar soat kesimida — muammo qaysi payt bo'lganini ko'rsatadi. */
function renderHourly() {
  const svg = $("ch-hourly"), empty = $("ch-hourly-empty");
  const hours = (state.stats && state.stats.hourly_today) || [];
  if (!hours.some((v) => v > 0)) {
    svg.innerHTML = "";
    empty.textContent = state.stats
      ? "Bugun uzilish qayd etilmadi." : "Tarix yig'ilmoqda…";
    empty.style.display = "flex";
    return;
  }
  const pd = (n) => String(n).padStart(2, "0");
  renderColumns("ch-hourly", "ch-hourly-empty", hours.map((n, h) => ({
    label: h % 6 === 0 ? pd(h) : "",
    value: n,
    tipValue: n + " ta uzilish",
    tipLabel: pd(h) + ":00 – " + pd(h) + ":59",
  })), { emptyText: "" });
}

/* Oyna o'lchami o'zgarsa grafiklar yangi kenglikka qayta chiziladi. */
let chResizeTimer = null;
window.addEventListener("resize", () => {
  if (state.tab !== "dash") return;
  clearTimeout(chResizeTimer);
  chResizeTimer = setTimeout(() => {
    renderTimeline(); renderDailyCharts(); renderHourly();
  }, 200);
});

/* Dashboard ochiq turganda har 15 soniyada o'zi yangilanadi. */
setInterval(() => {
  if (state.tab === "dash" && !document.hidden) renderDash();
}, 15000);

/* Hodisalar lentasi: server yozgan uzilishlar (doimiy) + shu seansdagi
   mahalliy hodisalar (oqim ochildi, MediaMTX va h.k.) bitta ro'yxatda. */
function fmtEvTime(t) {
  const d = new Date(t), p = (n) => String(n).padStart(2, "0");
  const sameDay = d.toDateString() === new Date().toDateString();
  return (sameDay ? "" : p(d.getDate()) + "." + p(d.getMonth() + 1) + " ") +
         p(d.getHours()) + ":" + p(d.getMinutes());
}

function renderEvents() {
  const colors = { ok: "var(--ok)", warn: "var(--warn)", danger: "var(--danger)" };
  const server = ((state.stats && state.stats.events) || []).map((e) => ({
    t: Date.parse(e.ts),
    text: e.name + " (" + e.region + ") — " +
          (e.kind === "offline" ? "uzildi" : "qayta ulandi"),
    kind: e.kind === "offline" ? "danger" : "ok",
  }));
  const all = state.events.concat(server).sort((a, b) => b.t - a.t).slice(0, 60);
  $("events-list").innerHTML = all.length
    ? all.map((e) =>
        '<div class="erow"><span class="tm">' + fmtEvTime(e.t) + "</span>" +
        '<span class="ln" style="background:' + (colors[e.kind] || "var(--muted)") + '"></span>' +
        '<span class="tx">' + esc(e.text) + "</span></div>").join("")
    : '<div class="empty">Hodisalar hali yo‘q.</div>';
}

/* ---------- Tab'lar ---------- */
function showTab(tab) {
  if (tab === "admin" && !state.admin) {
    state.pendingTab = "admin";
    openModal("login-modal");
    setTimeout(() => $("l-pass").focus(), 60);
    return;
  }
  const prev = state.tab;
  state.tab = tab;
  document.querySelectorAll("#tabs button").forEach((b) =>
    b.classList.toggle("on", b.dataset.tab === tab));
  $("wall-view").hidden = tab !== "wall";
  $("dash-view").hidden = tab !== "dash";
  $("admin-view").hidden = tab !== "admin";

  const onMap = tab === "map";
  $("list-panel").hidden = !onMap || !state.listOpen;
  $("sel-panel").hidden = !onMap || !state.selectedId;
  $("strip").hidden = !onMap;
  $("mapctl").hidden = !onMap;

  if (prev === "wall" && tab !== "wall") stopWall();
  if (prev === "map" && tab !== "map" && selPlayer) selPlayer.stop();

  if (tab === "wall") buildWall();
  if (tab === "dash") renderDash();
  if (tab === "admin") loadAdminCameras(0);
  if (tab === "map" && state.selectedId) selectCamera(state.selectedId, false);
}
document.querySelectorAll("#tabs button").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab)));
$("wall-back").addEventListener("click", () => showTab("map"));
$("dash-back").addEventListener("click", () => showTab("map"));
$("admin-back").addEventListener("click", () => showTab("map"));
$("go-wall").addEventListener("click", () => showTab("wall"));

/* ---------- Xarita boshqaruvlari ---------- */
$("z-in").addEventListener("click", () => map.zoomIn());
$("z-out").addEventListener("click", () => map.zoomOut());
$("fit-all").addEventListener("click", () => {
  try {
    const b = cluster.getBounds();
    if (b.isValid()) { map.fitBounds(b.pad(0.2)); return; }
  } catch (e) {}
  map.setView([41.35, 64.6], 6);
});

/* ---------- Modallar ---------- */
function openModal(id) { $(id).classList.add("open"); }
function closeModal(id) {
  $(id).classList.remove("open");
  if (id === "cam-modal") stopPicking(true);
  if (id === "login-modal") state.pendingTab = null;
}
document.querySelectorAll("[data-close]").forEach((b) =>
  b.addEventListener("click", () => closeModal(b.dataset.close)));
document.querySelectorAll(".backdrop").forEach((bd) =>
  bd.addEventListener("click", (e) => { if (e.target === bd) closeModal(bd.id); }));

/* ---------- Autentifikatsiya ---------- */
function setAdmin(admin) {
  state.admin = admin;
  const av = $("avatar");
  if (admin) {
    av.textContent = admin.username.slice(0, 2).toUpperCase();
    av.title = admin.username + " — chiqish uchun bosing";
  } else {
    av.textContent = "Kirish";
    av.title = "Super-admin";
    if (state.tab === "admin") showTab("map");
    stopPicking(true);
  }
}

async function checkAuth() {
  const me = await api("/api/auth/me");
  setAdmin(me.authenticated ? { username: me.username } : null);
}

$("avatar").addEventListener("click", async () => {
  if (!state.admin) {
    $("login-err").classList.remove("show");
    $("l-pass").value = "";
    openModal("login-modal");
    setTimeout(() => $("l-pass").focus(), 60);
    return;
  }
  if (confirm("Chiqmoqchimisiz?")) {
    await api("/api/auth/logout", { method: "POST" });
    setAdmin(null);
    toast("Chiqdingiz");
  }
});

$("l-submit").addEventListener("click", doLogin);
$("l-pass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });

async function doLogin() {
  const err = $("login-err");
  err.classList.remove("show");
  try {
    const me = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: $("l-user").value.trim(), password: $("l-pass").value })
    });
    setAdmin({ username: me.username });
    closeModal("login-modal");
    toast("Xush kelibsiz, " + me.username);
    if (state.pendingTab === "admin") { state.pendingTab = null; showTab("admin"); }
  } catch (e) {
    err.textContent = e.message;
    err.classList.add("show");
  }
}

/* ---------- Boshqaruv jadvali ---------- */
const ADMIN_PAGE = 100;

async function loadAdminCameras(offset) {
  const start = offset || 0;
  const query = encodeURIComponent(state.adminQuery || "");
  const res = await api("/api/admin/cameras?q=" + query +
                        "&limit=" + ADMIN_PAGE + "&offset=" + start);
  state.adminCameras = res.cameras;
  state.adminOffset = start;
  state.adminTotal = res.total;

  $("admin-total").textContent = res.total + " ta yozuv";
  const last = Math.min(start + res.cameras.length, res.total);
  $("admin-count").textContent = res.total > ADMIN_PAGE
    ? (start + 1) + "–" + last + " / " + res.total : "";
  $("adm-prev").disabled = start === 0;
  $("adm-next").disabled = start + ADMIN_PAGE >= res.total;

  fillAdminRegions();
  renderAdminTable();
}

/* Kamera holati filtrlash/saralash uchun yagona qiymatga keltiriladi. */
function camStatus(cam) {
  if (!cam.enabled) return "disabled";
  const pub = state.byId.get(cam.id);
  if (pub && pub.online === false) return "offline";
  if (pub && pub.online === true) return "online";
  return "unknown";
}
function camCodecKind(cam) {
  if (cam.transcode) return "trans";
  if (/h265|hevc/i.test(cam.codec || "")) return "h265raw";
  if (/h264|avc/i.test(cam.codec || "")) return "h264";
  return "";
}

function fillAdminRegions() {
  const sel = $("adm-region");
  const cur = sel.value;
  const regions = [...new Set(state.adminCameras.map((c) => c.region))].sort();
  sel.innerHTML = '<option value="">Barcha hududlar</option>' +
    regions.map((r) => '<option value="' + esc(r) + '"' +
      (r === cur ? " selected" : "") + ">" + esc(r) + "</option>").join("");
}

function renderAdminTable() {
  const f = state.adminFilters;
  let rows = state.adminCameras.filter((cam) =>
    (!f.status || camStatus(cam) === f.status) &&
    (!f.region || cam.region === f.region) &&
    (!f.codec || camCodecKind(cam) === f.codec) &&
    (!f.mode || (f.mode === "always") === !!cam.always_on));

  const s = state.adminSort;
  if (s.key) {
    const val = (cam) => s.key === "status" ? camStatus(cam)
      : s.key === "codec" ? camCodecKind(cam)
      : s.key === "mode" ? (cam.always_on ? "a" : "b")
      : String(cam[s.key] || "").toLowerCase();
    rows = [...rows].sort((a, b) => s.dir * val(a).localeCompare(val(b), "uz"));
  }
  document.querySelectorAll("#admin-table th.sortable").forEach((th) => {
    th.querySelector(".arr").textContent =
      th.dataset.key === s.key ? (s.dir > 0 ? "▲" : "▼") : "";
  });

  $("adm-shown").textContent = rows.length !== state.adminCameras.length
    ? rows.length + " / " + state.adminCameras.length + " ko'rsatilyapti" : "";

  const tbody = $("admin-tbody");
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="7"><div class="empty">' +
      (state.adminCameras.length ? "Filtrlarga mos kamera topilmadi."
        : state.adminQuery ? "Qidiruvga mos kamera topilmadi."
        : "Hali kamera qo'shilmagan — «+ Kamera» dan boshlang.") +
      "</div></td></tr>";
    return;
  }
  rows.forEach((cam) => tbody.appendChild(adminRow(cam)));
}

document.querySelectorAll("#adm-status button").forEach((b) =>
  b.addEventListener("click", () => {
    state.adminFilters.status = b.dataset.st;
    document.querySelectorAll("#adm-status button").forEach((x) =>
      x.classList.toggle("on", x === b));
    renderAdminTable();
  }));
$("adm-region").addEventListener("change", (e) => {
  state.adminFilters.region = e.target.value; renderAdminTable();
});
$("adm-codec").addEventListener("change", (e) => {
  state.adminFilters.codec = e.target.value; renderAdminTable();
});
$("adm-mode").addEventListener("change", (e) => {
  state.adminFilters.mode = e.target.value; renderAdminTable();
});
document.querySelectorAll("#admin-table th.sortable").forEach((th) =>
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (state.adminSort.key === key) state.adminSort.dir *= -1;
    else state.adminSort = { key, dir: 1 };
    renderAdminTable();
  }));

function adminRow(cam) {
  const tr = document.createElement("tr");
  const pub = state.byId.get(cam.id);
  const stateInfo = !cam.enabled
    ? { tx: "O'CHIRILGAN", color: "var(--faint)" }
    : pub && pub.online === false
      ? { tx: "UZILGAN", color: "var(--danger)" }
      : pub && pub.online === true
        ? { tx: "ONLAYN", color: "var(--ok)" }
        : { tx: "—", color: "var(--muted)" };
  const addr = cam.source_type === "rtsp"
    ? cam.ip + ":" + cam.port + (cam.rtsp_path || "")
    : (cam.raw_stream_url || "—");

  tr.innerHTML =
    '<td><span class="st-chip" style="color:' + stateInfo.color + '">' +
      '<i style="background:' + stateInfo.color + '"></i>' + stateInfo.tx + "</span></td>" +
    '<td style="font-weight:600">' + esc(cam.name) + "</td>" +
    '<td style="color:var(--muted)">' + esc(cam.region) + "</td>" +
    '<td class="mono" style="font-size:11px;color:var(--muted);word-break:break-all">' + esc(addr) + "</td>" +
    "<td>" + (cam.codec
      ? '<span class="cdx-chip">' + esc(cam.codec) + (cam.transcode ? " → H264" : "") + "</span>"
      : '<span style="color:var(--faint)">—</span>') + "</td>" +
    '<td style="color:var(--muted);font-size:11.5px">' +
      (cam.always_on ? "doim tayyor" : "so'rov bo'yicha") + "</td>" +
    '<td style="text-align:right;white-space:nowrap">' +
      '<span style="display:inline-flex;gap:6px">' +
        '<button class="btn mini" data-act="edit">Tahrirlash</button>' +
        (cam.source_type === "rtsp" ? '<button class="btn mini" data-act="test">Tekshirish</button>' : "") +
        '<button class="btn mini" data-act="find">Xaritada</button>' +
        '<button class="btn mini danger" data-act="del">O‘chirish</button>' +
      "</span><div class=\"adm-probe\"></div></td>";

  const out = tr.querySelector(".adm-probe");
  tr.querySelector('[data-act="edit"]').addEventListener("click", () => openCameraForm(cam));
  tr.querySelector('[data-act="del"]').addEventListener("click", () => deleteCamera(cam));
  tr.querySelector('[data-act="find"]').addEventListener("click", () => {
    showTab("map");
    map.setView([cam.lat, cam.lng], 15);
    selectCamera(cam.id, false);
  });
  const testBtn = tr.querySelector('[data-act="test"]');
  if (testBtn) testBtn.addEventListener("click", async () => {
    testBtn.disabled = true;
    out.className = "adm-probe show wait";
    out.textContent = "Tekshirilmoqda…";
    try {
      const r = await api("/api/admin/probe", {
        method: "POST",
        body: JSON.stringify({
          ip: cam.ip, port: cam.port, username: cam.username,
          rtsp_path: cam.rtsp_path, camera_id: cam.id
        })
      });
      out.className = "adm-probe show " + (r.ok ? "ok" : "bad");
      out.textContent = r.message;
    } catch (e) {
      out.className = "adm-probe show bad";
      out.textContent = e.message;
    }
    testBtn.disabled = false;
  });
  return tr;
}

$("adm-prev").addEventListener("click", () =>
  loadAdminCameras(Math.max(0, state.adminOffset - ADMIN_PAGE)));
$("adm-next").addEventListener("click", () =>
  loadAdminCameras(state.adminOffset + ADMIN_PAGE));

let adminSearchTimer = null;
$("admin-search").addEventListener("input", (e) => {
  state.adminQuery = e.target.value;
  clearTimeout(adminSearchTimer);
  adminSearchTimer = setTimeout(() => loadAdminCameras(0), 250);
});

async function deleteCamera(cam) {
  if (!confirm('"' + cam.name + '" kamerasi butunlay o‘chirilsinmi?')) return;
  try {
    await api("/api/admin/cameras/" + cam.id, { method: "DELETE" });
    await loadAdminCameras(state.adminOffset);
    await loadCameras();
    addEvent(cam.name + " — o'chirildi", "warn");
    toast("Kamera o'chirildi");
  } catch (e) { toast(e.message, true); }
}

/* ---------- Kamera shakli ---------- */
async function loadVendors() {
  state.vendors = await api("/api/vendors");
  $("f-vendor").innerHTML = state.vendors
    .map((v) => '<option value="' + v.id + '">' + esc(v.name) + "</option>").join("");
}

function setSourceType(type) {
  state.sourceType = type;
  document.querySelectorAll(".seg button").forEach((b) =>
    b.classList.toggle("on", b.dataset.src === type));
  $("rtsp-block").hidden = type !== "rtsp";
  $("manual-block").hidden = type !== "manual";
}
document.querySelectorAll(".seg button").forEach((b) =>
  b.addEventListener("click", () => setSourceType(b.dataset.src)));

$("f-vendor").addEventListener("change", () => {
  const v = state.vendors.find((x) => x.id === $("f-vendor").value);
  if (!v) return;
  $("f-path").value = v.path;
  if (!$("f-port").value || $("f-port").value === "554") $("f-port").value = v.port;
  updatePreview();
});

["f-ip", "f-port", "f-user", "f-pass", "f-path"].forEach((id) =>
  $(id).addEventListener("input", updatePreview));

function updatePreview() {
  const ip = $("f-ip").value.trim() || "IP";
  const port = $("f-port").value || "554";
  const user = $("f-user").value.trim();
  const pass = $("f-pass").value ? "•••" : "";
  let path = $("f-path").value.trim();
  if (path && !path.startsWith("/")) path = "/" + path;
  const cred = user ? user + (pass ? ":" + pass : "") + "@" : "";
  $("f-preview").textContent = "rtsp://" + cred + ip + ":" + port + (path || "/");
}

function openCameraForm(cam) {
  // Vendor ro'yxati ishga tushishda yuklanmay qolgan bo'lsa — hozir yuklaymiz.
  if (!state.vendors.length) loadVendors().catch(() => {});
  state.editingId = cam ? cam.id : null;
  $("cam-title").textContent = cam ? "Kamerani tahrirlash" : "Yangi kamera";
  $("cam-err").classList.remove("show");
  $("f-probe").className = "probe-out";
  $("pass-hint").hidden = !cam;
  resetScan();

  setSourceType(cam ? cam.source_type : "rtsp");
  $("f-name").value = cam ? cam.name : "";
  $("f-region").value = cam ? cam.region : "";
  $("f-lat").value = cam ? cam.lat : "";
  $("f-lng").value = cam ? cam.lng : "";
  $("f-ip").value = cam ? cam.ip : "";
  $("f-port").value = cam ? cam.port : 554;
  $("f-user").value = cam ? cam.username : "";
  $("f-pass").value = "";
  $("f-path").value = cam ? cam.rtsp_path : "/stream1";
  $("f-vendor").value = cam ? cam.vendor : "boshqa";
  $("f-url").value = cam ? cam.raw_stream_url : "";
  $("f-note").value = cam ? cam.note : "";
  $("f-enabled").checked = cam ? cam.enabled : true;
  $("f-always").checked = cam ? cam.always_on : false;

  const out = $("f-probe");
  if (cam && cam.codec) {
    out.className = "probe-out show " + (cam.transcode ? "wait" : "ok");
    out.textContent = cam.transcode
      ? "Kodek " + cam.codec + " — brauzer o'qiy olmaydi, H.264 ga o'girib beriladi"
      : "Kodek " + cam.codec + " — to'g'ridan-to'g'ri uzatiladi";
  }

  updatePreview();
  openModal("cam-modal");
  setTimeout(() => $("f-name").focus(), 60);
}

$("new-cam").addEventListener("click", () => openCameraForm(null));

/* --- viloyatni koordinatadan aniqlash --- */
let regionGeo = null;
async function ensureRegionGeo() {
  if (regionGeo) return regionGeo;
  const r = await fetch("/static/uz_regions.geojson");
  if (!r.ok) throw new Error("chegara fayli yuklanmadi");
  regionGeo = await r.json();
  return regionGeo;
}

function pointInRing(lat, lng, ring) {
  // Nur usuli (ray casting); geojson koordinatasi [lng, lat] tartibida.
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    if ((yi > lat) !== (yj > lat) &&
        lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function regionAt(lat, lng) {
  if (!regionGeo) return "";
  for (const f of regionGeo.features) {
    const g = f.geometry;
    const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
    for (const poly of polys) {
      if (pointInRing(lat, lng, poly[0]) &&
          !poly.slice(1).some((hole) => pointInRing(lat, lng, hole)))
        return f.properties.name;
    }
  }
  return "";
}

function autoRegion(prefix) {
  const lat = parseFloat($(prefix + "-lat").value);
  const lng = parseFloat($(prefix + "-lng").value);
  if (Number.isNaN(lat) || Number.isNaN(lng)) return;
  const fill = () => {
    const name = regionAt(lat, lng);
    if (name) $(prefix + "-region").value = name;
  };
  if (regionGeo) fill();
  else ensureRegionGeo().then(fill).catch(() => {});
}

["f", "n"].forEach((prefix) =>
  ["-lat", "-lng"].forEach((suffix) =>
    $(prefix + suffix).addEventListener("change", () => autoRegion(prefix))));

/* --- xaritadan koordinata tanlash --- */
$("f-pick").addEventListener("click", () => {
  $("cam-modal").classList.remove("open");
  state.picking = "cam";
  document.body.classList.add("picking");
  showTab("map");
  toast("Xaritada kerakli nuqtani bosing");
});

function stopPicking(removeMarker) {
  state.picking = null;
  document.body.classList.remove("picking");
  if (removeMarker && state.pickMarker) {
    map.removeLayer(state.pickMarker);
    state.pickMarker = null;
  }
}

map.on("click", (e) => {
  if (!state.picking) return;
  const target = state.picking === "nvr" ? "nvr-modal" : "cam-modal";
  const prefix = state.picking === "nvr" ? "n" : "f";
  $(prefix + "-lat").value = e.latlng.lat.toFixed(5);
  $(prefix + "-lng").value = e.latlng.lng.toFixed(5);
  autoRegion(prefix);
  if (state.pickMarker) map.removeLayer(state.pickMarker);
  state.pickMarker = L.marker(e.latlng, {
    icon: L.divIcon({ className: "",
      html: '<div class="mk sel"><span class="r"></span><span class="c"></span></div>',
      iconSize: [24, 24], iconAnchor: [12, 12] })
  }).addTo(map);
  stopPicking(false);
  openModal(target);
});

/* --- ulanishni tekshirish --- */
$("f-test").addEventListener("click", async () => {
  const out = $("f-probe");
  const show = (kind, tx) => { out.className = "probe-out show " + kind; out.textContent = tx; };
  if (!$("f-ip").value.trim()) { show("bad", "Avval IP manzilni kiriting"); return; }
  $("f-test").disabled = true;
  show("wait", "Tekshirilmoqda… (10 soniyagacha)");
  try {
    const r = await api("/api/admin/probe", {
      method: "POST",
      body: JSON.stringify({
        ip: $("f-ip").value.trim(),
        port: Number($("f-port").value) || 554,
        username: $("f-user").value.trim(),
        password: $("f-pass").value || null,
        rtsp_path: $("f-path").value.trim() || "/",
        camera_id: state.editingId
      })
    });
    show(r.ok ? "ok" : "bad", r.message);
  } catch (e) {
    show("bad", e.message);
  }
  $("f-test").disabled = false;
});

/* --- qurilmani avtomatik aniqlash --- */
function scanPicked() {
  if (!state.scan) return null;
  return state.scan.channels.filter((c) => {
    const cb = document.querySelector('#f-channels input[data-ch="' + c.channel + '"]');
    return cb ? cb.checked : true;
  });
}

function updateSaveLabel() {
  const picked = scanPicked();
  $("f-save").textContent = picked && picked.length > 1
    ? picked.length + " ta kamerani qo'shish"
    : "Saqlash";
}

function resetScan() {
  state.scan = null;
  $("f-channels").innerHTML = "";
  $("f-scan-out").className = "probe-out";
  updateSaveLabel();
}

function renderScanChannels(res) {
  const box = $("f-channels");
  if (res.channels.length < 2) { box.innerHTML = ""; return; }
  box.innerHTML =
    '<div class="section" style="margin-top:8px">' +
    '<div class="section-title">Topilgan kanallar — qo\'shiladiganlarini belgilang</div>' +
    res.channels.map((c) =>
      '<label style="display:flex;align-items:center;gap:9px;color:var(--text);' +
      'font-size:13px;font-weight:500;cursor:pointer">' +
      '<input type="checkbox" data-ch="' + c.channel + '" checked style="width:auto;margin:0">' +
      c.channel + "-kanal " +
      '<span class="cdx-chip">' + esc(c.codec || "?") + (c.needs_transcode ? " →H264" : "") + "</span>" +
      '<span class="mono" style="color:var(--muted);font-size:11px">' + esc(c.rtsp_path) + "</span>" +
      "</label>").join("") +
    '<div class="hint">Har biri alohida kamera bo\'lib qo\'shiladi: «Nomi 1-kanal», ' +
    "«Nomi 2-kanal»… Nuqtalar tanlangan joy atrofiga tarqatiladi, keyin har birini " +
    "xaritada o'z joyiga surish mumkin.</div></div>";
  box.querySelectorAll("input[data-ch]").forEach((cb) =>
    cb.addEventListener("change", updateSaveLabel));
}

$("f-scan").addEventListener("click", async () => {
  const out = $("f-scan-out");
  const show = (kind, tx) => { out.className = "probe-out show " + kind; out.textContent = tx; };
  const ip = $("f-ip").value.trim();
  if (!ip) { show("bad", "Avval IP manzilni kiriting"); return; }
  $("f-scan").disabled = true;
  state.scan = null;
  $("f-channels").innerHTML = "";
  updateSaveLabel();
  show("wait", "Qurilma aniqlanmoqda — shablonlar va kanallar tekshirilmoqda (~10-30 s)…");
  try {
    const res = await api("/api/admin/scan", {
      method: "POST",
      body: JSON.stringify({
        ip,
        port: Number($("f-port").value) || 554,
        username: $("f-user").value.trim(),
        password: $("f-pass").value || "",
        camera_id: state.editingId
      })
    });
    if (!res.found) { show("bad", res.message); }
    else {
      state.scan = res;
      const first = res.channels[0];
      $("f-vendor").value = res.vendor;
      $("f-path").value = first.rtsp_path;
      updatePreview();
      renderScanChannels(res);
      updateSaveLabel();
      show("ok", res.device === "nvr"
        ? res.vendor_name + " registrator (NVR) — " + res.channels.length +
          " ta jonli kanal topildi"
        : res.vendor_name + " — bitta kamera · kodek " + (first.codec || "noma'lum") +
          (first.needs_transcode ? " (H.264 ga o'girib beriladi)" : ""));
    }
  } catch (e) { show("bad", e.message); }
  $("f-scan").disabled = false;
});

/* --- saqlash --- */
$("f-save").addEventListener("click", async () => {
  const err = $("cam-err");
  err.classList.remove("show");
  const lat = parseFloat($("f-lat").value);
  const lng = parseFloat($("f-lng").value);

  const body = {
    name: $("f-name").value.trim(),
    region: $("f-region").value.trim(),
    lat, lng,
    source_type: state.sourceType,
    enabled: $("f-enabled").checked,
    always_on: $("f-always").checked,
    note: $("f-note").value.trim(),
    ip: $("f-ip").value.trim(),
    port: Number($("f-port").value) || 554,
    username: $("f-user").value.trim(),
    password: $("f-pass").value || null,
    rtsp_path: $("f-path").value.trim() || "/stream1",
    vendor: $("f-vendor").value,
    stream_url: $("f-url").value.trim()
  };

  const fail = (m) => { err.textContent = m; err.classList.add("show"); };
  if (!body.name || !body.region) return fail("Nomi va hududini to'ldiring");
  if (Number.isNaN(lat) || Number.isNaN(lng)) return fail("Koordinatani xaritadan tanlang yoki qo'lda kiriting");
  if (body.source_type === "rtsp" && !body.ip) return fail("IP manzilni kiriting");
  if (body.source_type === "manual" && !body.stream_url) return fail("Oqim manzilini kiriting");

  // Skaner bir nechta kanal topgan bo'lsa — har biri alohida kamera bo'ladi.
  const picked = state.sourceType === "rtsp" ? scanPicked() : null;
  if (!state.editingId && picked && picked.length > 1) {
    $("f-save").disabled = true;
    try {
      const res = await api("/api/admin/nvr/import", {
        method: "POST",
        body: JSON.stringify({
          ip: body.ip, port: body.port,
          username: body.username, password: $("f-pass").value || "",
          vendor: state.scan.vendor,
          channels: picked.map((c) => c.channel).join(","),
          region: body.region, name_prefix: body.name,
          lat, lng, spread_m: 60, stream: "main",
          enabled: body.enabled, probe: true, dry_run: false
        })
      });
      closeModal("cam-modal");
      resetScan();
      await loadCameras();
      if (state.tab === "admin") await loadAdminCameras(0);
      addEvent(body.name + " — " + res.created + " ta kamera qo'shildi", "ok");
      toast(res.created + " ta kamera qo'shildi");
    } catch (e) { fail(e.message); }
    $("f-save").disabled = false;
    return;
  }
  if (picked && picked.length === 1 && state.scan) {
    body.rtsp_path = picked[0].rtsp_path;
    body.vendor = state.scan.vendor;
  }

  $("f-save").disabled = true;
  try {
    if (state.editingId) {
      await api("/api/admin/cameras/" + state.editingId, { method: "PUT", body: JSON.stringify(body) });
    } else {
      await api("/api/admin/cameras", { method: "POST", body: JSON.stringify(body) });
    }
    closeModal("cam-modal");
    await loadCameras();
    if (state.tab === "admin") await loadAdminCameras(state.adminOffset);
    addEvent(body.name + (state.editingId ? " — tahrirlandi" : " — qo'shildi"), "ok");
    toast(state.editingId ? "O'zgarishlar saqlandi" : "Kamera qo'shildi");
    if (body.always_on) {
      toast("«Doim tayyor» o'zgardi — «MediaMTX» tugmasini bosing");
    }
  } catch (e) {
    fail(e.message);
  }
  $("f-save").disabled = false;
});

/* ---------- NVR dan ommaviy qo'shish ---------- */
$("nvr-btn").addEventListener("click", () => {
  $("nvr-err").classList.remove("show");
  $("n-out").className = "probe-out";
  $("n-table").innerHTML = "";
  $("n-save").disabled = true;
  $("n-vendor").innerHTML = $("f-vendor").innerHTML;
  $("n-vendor").value = "hikvision";
  openModal("nvr-modal");
});

$("n-pick").addEventListener("click", () => {
  $("nvr-modal").classList.remove("open");
  state.picking = "nvr";
  document.body.classList.add("picking");
  showTab("map");
  toast("Xaritada registrator joylashgan nuqtani bosing");
});

function nvrBody(dryRun) {
  return {
    ip: $("n-ip").value.trim(),
    port: Number($("n-port").value) || 554,
    username: $("n-user").value.trim(),
    password: $("n-pass").value,
    vendor: $("n-vendor").value,
    channels: $("n-channels").value.trim(),
    region: $("n-region").value.trim(),
    name_prefix: $("n-prefix").value.trim(),
    lat: parseFloat($("n-lat").value),
    lng: parseFloat($("n-lng").value),
    spread_m: Number($("n-spread").value) || 0,
    stream: $("n-stream").value,
    probe: true,
    dry_run: dryRun
  };
}

function nvrValidate(body) {
  const err = $("nvr-err");
  const fail = (m) => { err.textContent = m; err.classList.add("show"); return false; };
  err.classList.remove("show");
  if (!body.ip) return fail("NVR manzilini kiriting");
  if (!body.region) return fail("Hududni kiriting");
  if (Number.isNaN(body.lat) || Number.isNaN(body.lng))
    return fail("Koordinatani xaritadan tanlang yoki qo'lda kiriting");
  return true;
}

function showNvrOut(kind, tx) {
  const el = $("n-out");
  el.className = "probe-out show " + kind;
  el.textContent = tx;
}

async function nvrRun(dryRun) {
  const body = nvrBody(dryRun);
  if (!nvrValidate(body)) return;

  const btn = dryRun ? $("n-check") : $("n-save");
  btn.disabled = true;
  showNvrOut("wait", "Kanallar tekshirilmoqda — biroz kuting…");
  try {
    const res = await api("/api/admin/nvr/import", {
      method: "POST", body: JSON.stringify(body)
    });
    renderNvrTable(res.planned);
    const ok = res.reachable;
    if (dryRun) {
      showNvrOut(ok ? "ok" : "bad",
        res.planned.length + " ta kanaldan " + ok + " tasi javob berdi" +
        (ok ? " — «Qo'shish» tugmasini bosing" : ""));
      $("n-save").disabled = ok === 0;
    } else {
      showNvrOut("ok", res.created + " ta kamera qo'shildi");
      closeModal("nvr-modal");
      await loadCameras();
      if (state.tab === "admin") await loadAdminCameras(0);
      addEvent(body.region + " — NVR'dan " + res.created + " ta kamera qo'shildi", "ok");
      toast(res.created + " ta kamera qo'shildi — darhol ishlatsa bo'ladi");
    }
  } catch (e) {
    showNvrOut("bad", e.message);
  }
  btn.disabled = false;
}

function renderNvrTable(planned) {
  if (!planned || !planned.length) { $("n-table").innerHTML = ""; return; }
  const rows = planned.map((p) => {
    const mark = p.ok === null ? "·" : p.ok ? "✓" : "✕";
    const cls = p.ok === null ? "" : p.ok ? "ok" : "bad";
    return '<tr class="' + cls + '"><td>' + p.channel + "</td>" +
           "<td>" + mark + "</td>" +
           "<td>" + esc(p.codec || "—") + (p.transcode ? " →H264" : "") + "</td>" +
           '<td title="' + esc(p.message) + '">' + esc(p.message.slice(0, 44)) + "</td></tr>";
  }).join("");
  $("n-table").innerHTML =
    '<table class="nvr-table"><thead><tr><th>Kanal</th><th></th><th>Kodek</th>' +
    "<th>Holat</th></tr></thead><tbody>" + rows + "</tbody></table>";
}

$("n-scan").addEventListener("click", async () => {
  const ip = $("n-ip").value.trim();
  if (!ip) { showNvrOut("bad", "Avval NVR manzilini kiriting"); return; }
  $("n-scan").disabled = true;
  showNvrOut("wait", "Qurilma aniqlanmoqda — kanallar sanalmoqda…");
  try {
    const res = await api("/api/admin/scan", {
      method: "POST",
      body: JSON.stringify({
        ip,
        port: Number($("n-port").value) || 554,
        username: $("n-user").value.trim(),
        password: $("n-pass").value || ""
      })
    });
    if (!res.found) { showNvrOut("bad", res.message); }
    else {
      $("n-vendor").value = res.vendor;
      $("n-channels").value = res.channels.map((c) => c.channel).join(",");
      showNvrOut("ok", res.vendor_name + " — " + res.channels.length +
        " ta jonli kanal topildi; hudud va nuqtani belgilab «Qo'shish»ni bosing");
      $("n-save").disabled = false;
    }
  } catch (e) { showNvrOut("bad", e.message); }
  $("n-scan").disabled = false;
});

$("n-check").addEventListener("click", () => nvrRun(true));
$("n-save").addEventListener("click", () => nvrRun(false));

/* ---------- MediaMTX ---------- */
$("sync-btn").addEventListener("click", async () => {
  openModal("mtx-modal");
  $("mtx-text").textContent = "Yuklanmoqda…";
  try {
    const r = await api("/api/admin/mediamtx/config");
    $("mtx-text").textContent = r.text;
    $("mtx-lead").textContent = r.api_available
      ? "MediaMTX ishlab turibdi — o'zgarishlar qayta ishga tushirmasdan qo'llanadi."
      : "MediaMTX hozir ishlamayapti — fayl yoziladi, keyin MediaMTX'ni ishga tushiring.";
  } catch (e) {
    $("mtx-text").textContent = e.message;
  }
});

$("mtx-apply").addEventListener("click", async () => {
  $("mtx-apply").disabled = true;
  try {
    const r = await api("/api/admin/mediamtx/sync", { method: "POST" });
    closeModal("mtx-modal");
    toast(r.written + " ta kamera yozildi · " + r.live.message, !r.live.ok);
    addEvent("MediaMTX konfiguratsiyasi qo'llandi", "ok");
  } catch (e) {
    toast(e.message, true);
  }
  $("mtx-apply").disabled = false;
});

/* ---------- Umumiy ---------- */
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const open = document.querySelector(".backdrop.open");
  if (open) { closeModal(open.id); return; }
  if (state.picking) {
    const target = state.picking === "nvr" ? "nvr-modal" : "cam-modal";
    stopPicking(true);
    openModal(target);
    return;
  }
  if (!$("sel-panel").hidden) { closeSel(); return; }
  if (state.tab !== "map") showTab("map");   // devor/dashboard/boshqaruvdan qaytish
});

let toastTimer = null;
function toast(text, bad) {
  const t = $("toast");
  t.textContent = text;
  t.classList.toggle("bad", Boolean(bad));
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
}

/* ---------- Ishga tushirish ---------- */
(async function start() {
  setTheme(localStorage.getItem("nigoh-theme") === "dark" ? "dark" : "light");

  // Server hali ko'tarilmagan bo'lsa (masalan, birga ishga tushirilganda)
  // sahifa bo'sh qolib ketmaydi — ulanguncha qayta urinamiz.
  for (let attempt = 0; ; attempt++) {
    try { await loadCameras(); break; }
    catch (e) {
      if (attempt === 0) toast("Server bilan aloqa yo'q — qayta urinilmoqda…", true);
      if (attempt >= 30) { toast("Server javob bermayapti: " + e.message, true); return; }
      await new Promise((r) => setTimeout(r, 2000));
    }
  }

  // Uzilgan kameralar birinchi ochilishda hodisalar ro'yxatiga tushadi.
  state.cameras.filter((c) => c.online === false).forEach((c) =>
    addEvent(c.name + " — uzilgan (oxirgi onlayn: " + fmtLastSeen(c.last_seen) + ")", "danger"));

  try { await loadVendors(); } catch (e) { /* shakl ochilganda qayta yuklanadi */ }
  try { await checkAuth(); } catch (e) { /* kirilmagan holat — muammo emas */ }

  // Chuqur havola: /#wall, /#dash, /#admin — to'g'ridan-to'g'ri bo'limga olib kiradi.
  const hashTab = location.hash.replace("#", "");
  if (["wall", "dash", "admin"].includes(hashTab)) showTab(hashTab);
})();
