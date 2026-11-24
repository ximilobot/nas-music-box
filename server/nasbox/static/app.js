/* NAS 音乐盒 前端：原生 JS 单页，无外部依赖。 */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const inApp = () => typeof window.NasBoxApp !== "undefined" && window.NasBoxApp;

  const IC = {
    music: '<svg viewBox="0 0 24 24"><path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/></svg>',
    list: '<svg viewBox="0 0 24 24"><path d="M4 6h12M4 12h12M4 18h8"/><path d="M20 10v7"/><circle cx="18" cy="17" r="2"/></svg>',
    search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    gear: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.8 1.2V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3.1 14H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 3.1V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0 1.2 2.9h.1a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
    play: '<svg class="fill" viewBox="0 0 24 24"><path d="M8 5.1v13.8a1 1 0 0 0 1.5.9l11-6.9a1 1 0 0 0 0-1.8l-11-6.9A1 1 0 0 0 8 5.1z"/></svg>',
    pause: '<svg class="fill" viewBox="0 0 24 24"><rect x="6" y="5" width="4.5" height="14" rx="1.2"/><rect x="13.5" y="5" width="4.5" height="14" rx="1.2"/></svg>',
    next: '<svg class="fill" viewBox="0 0 24 24"><path d="M5 5.6v12.8a1 1 0 0 0 1.6.8l8.9-6.4a1 1 0 0 0 0-1.6L6.6 4.8A1 1 0 0 0 5 5.600z"/><rect x="17" y="5" width="2.6" height="14" rx="1"/></svg>',
    prev: '<svg class="fill" viewBox="0 0 24 24"><path d="M19 5.6v12.8a1 1 0 0 1-1.6.8l-8.900-6.400a1 1 0 0 1 0-1.600l8.900-6.400a1 1 0 0 1 1.6.8z"/><rect x="4.400" y="5" width="2.6" height="14" rx="1"/></svg>',
    stop: '<svg class="fill" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
    down: '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
    queue: '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h10"/></svg>',
    heart: '<svg viewBox="0 0 24 24"><path d="M12 20s-7-4.400-9.200-9A5 5 0 0 1 12 6.500 5 5 0 0 1 21.200 11C19 15.600 12 20 12 20z"/></svg>',
    heartOn: '<svg class="fill" viewBox="0 0 24 24"><path d="M12 20s-7-4.400-9.200-9A5 5 0 0 1 12 6.500 5 5 0 0 1 21.200 11C19 15.600 12 20 12 20z"/></svg>',
    more: '<svg class="fill" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.800"/><circle cx="12" cy="12" r="1.800"/><circle cx="19" cy="12" r="1.800"/></svg>',
    vol: '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16.500 8.500a5 5 0 0 1 0 7"/></svg>',
    folder: '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
    back: '<svg viewBox="0 0 24 24"><path d="m15 6-6 6 6 6"/></svg>',
    all: '<svg viewBox="0 0 24 24"><path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/></svg>',
    one: '<svg viewBox="0 0 24 24"><path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/><path d="M11.500 10.500 13 9.500v5.500" stroke-width="1.600"/></svg>',
    random: '<svg viewBox="0 0 24 24"><path d="M16 3h5v5"/><path d="M4 20 21 3"/><path d="M21 16v5h-5"/><path d="m15 15 6 6"/><path d="m4 4 5 5"/></svg>',
    seq: '<svg viewBox="0 0 24 24"><path d="M3 6h14M3 12h14M3 18h14"/><path d="m19 15 2 3-2 3" /></svg>',
  };
  const MODES = ["all", "one", "random", "seq"];
  const MODE_NAME = { all: "全部循环", one: "单曲循环", random: "随机播放", seq: "顺序播放" };
  const fmt = s => { s = Math.max(0, Math.round(s || 0)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };

  const S = { st: null, tab: "library", libMode: "songs", dir: null, list: null, q: "", settings: null, stAt: 0, seeking: false, voling: false, es: null };

  /* ---------------- 基础 ---------------- */
  let toastTimer;
  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 2600);
  }
  async function api(path, body, method) {
    const opt = { method: method || (body !== undefined ? "POST" : "GET"), credentials: "same-origin", headers: {} };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    let r;
    try { r = await fetch("api/" + path, opt); } catch (e) { throw new Error("连不上 NAS 音乐盒服务"); }
    if (r.status === 401) { showLogin(); throw new Error("需要登录"); }
    let d = {};
    try { d = await r.json(); } catch (e) { /* 非 JSON */ }
    if (!r.ok || d.ok === false) throw new Error(d.error || ("请求失败 " + r.status));
    return d;
  }
  const act = async (fn, okMsg) => { try { const r = await fn(); if (okMsg) toast(okMsg); return r; } catch (e) { toast(e.message); } };
  const ctl = (action, value) => act(async () => { const d = await api("control", { action, value }); if (d.player) { S.st.player = d.player; S.stAt = Date.now(); renderPlayer(); } });

  /* ---------------- 主题 ---------------- */
  function applyTheme() {
    const pref = localStorage.getItem("nasbox_theme") || "auto";
    const dark = pref === "dark" || (pref === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    const color = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() || (dark ? "#111315" : "#f5f6f8");
    const m = $('meta[name="theme-color"]'); if (m) m.setAttribute("content", color);
    try { if (inApp() && NasBoxApp.setBarColor) NasBoxApp.setBarColor(color); } catch (e) { /* 壳不支持时忽略 */ }
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

  /* ---------------- 登录（页面内） ---------------- */
  function showLogin() {
    $("#login").hidden = false;
    $("#loginApp").hidden = !inApp();
    setTimeout(() => $("#loginPw").focus(), 50);
  }
  async function doLogin() {
    $("#loginErr").textContent = "";
    try {
      const r = await fetch("api/login", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify({ password: $("#loginPw").value }) });
      if (!r.ok) throw new Error(r.status === 403 ? "密码不正确" : "登录失败");
      $("#login").hidden = true; $("#loginPw").value = "";
      start();
    } catch (e) { $("#loginErr").textContent = e.message; }
  }

  /* ---------------- 状态与推送 ---------------- */
  function onStatus(st) {
    if (st.need_login) { showLogin(); return; }
    S.st = st; S.stAt = Date.now();
    renderTop(); renderPlayer();
    if (S.tab === "settings" && $("#authCard")) renderAuthCard();
    if (S.tab === "library" && $("#scanInfo")) renderScanInfo();
  }
  function connectEvents() {
    if (S.es) S.es.close();
    const es = S.es = new EventSource("api/events");
    es.onmessage = e => { try { onStatus(JSON.parse(e.data)); } catch (err) { /* 忽略坏包 */ } };
    es.onerror = () => { /* 浏览器会自动重连；同时用轮询兜底 */ };
  }
  setInterval(() => { if (!document.hidden && (!S.es || S.es.readyState !== 1) && $("#login").hidden) refresh(); }, 5000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && $("#login").hidden) { refresh(true); if (!S.es || S.es.readyState === 2) connectEvents(); } });
  async function refresh(deep) { try { const r = await fetch("api/status" + (deep ? "?refresh=1" : ""), { credentials: "same-origin" }); onStatus(await r.json()); } catch (e) { setOffline(); } }
  function setOffline() {
    const c = $("#authChip"); c.className = "chip st-network_error"; $("#authLabel").textContent = "服务未连接";
  }

  function renderTop() {
    const st = S.st, a = st.auth;
    $("#authChip").className = "chip st-" + a.state; $("#authLabel").textContent = a.label;
    const dev = st.devices.find(d => d.device_id === st.current_device);
    $("#devName").textContent = dev ? dev.name : (st.devices.length ? "选择音箱" : "无音箱");
    const b = $("#banner");
    let html = "", bad = false;
    if (a.state === "no_login") { html = '还没有登录小米账号。<a href="#" data-go="qr">扫码登录</a>'; bad = true; }
    else if (a.state === "need_qr") { html = '小米账号登录已失效，需要重新扫码。<a href="#" data-go="qr">重新扫码</a>'; bad = true; }
    else if (a.state === "network_error") html = "连接小米云端异常，正在自动重试（登录信息不受影响）。" + (a.last_error ? "<br><span class='muted'>" + esc(a.last_error) + "</span>" : "");
    else if (a.state === "ok" && !st.library.music_dir_ok) html = "曲库目录不存在，请检查 /music 的挂载。";
    else if (a.state === "ok" && dev && !dev.online) html = st.player && st.player.error ? esc(st.player.error) : "当前音箱离线（可能已关机或断网），指令无法送达。";
    if (!html && st.player && st.player.error) html = esc(st.player.error);
    b.hidden = !html; b.innerHTML = html; b.className = bad ? "bad" : "";
  }

  function coverHtml(song) { return song && song.has_cover ? '<img loading="lazy" alt="" src="api/cover/' + song.id + '">' : IC.music; }
  function curPos() {
    const p = S.st && S.st.player; if (!p) return 0;
    let pos = p.position;
    if (p.state === "playing") pos += (Date.now() - S.stAt) / 1000;
    return p.duration > 0 ? Math.min(pos, p.duration) : pos;
  }
  function renderPlayer() {
    const p = S.st && S.st.player, song = p && p.song;
    $("#mini").hidden = !song;
    if (!song) { $("#full").hidden = true; return; }
    const playing = p.state === "playing" || p.state === "starting" || p.state === "retrying";
    const sub = (song.artist || "未知歌手") + (p.state === "starting" ? " · 正在连接音箱" : p.state === "retrying" ? " · 重试中" : p.state === "paused" ? " · 已暂停" : p.state === "idle" ? " · 已停止" : "");
    if ($("#miniTitle").textContent !== song.title || S._coverId !== song.id) {
      S._coverId = song.id;
      $("#miniCover").innerHTML = coverHtml(song); $("#fullCover").innerHTML = coverHtml(song);
    }
    $("#miniTitle").textContent = song.title; $("#miniSub").textContent = sub;
    $("#miniToggle").innerHTML = playing ? IC.pause : IC.play;
    $("#fullTitle").textContent = song.title;
    $("#fullSub").textContent = (song.artist || "未知歌手") + (song.album ? " · " + song.album : "");
    $("#fullSrc").textContent = (p.device_name || "") + (p.source ? " · " + p.source : "") + (p.queue_len ? "（" + (p.index + 1) + "/" + p.queue_len + "）" : "");
    $("#btnToggle").innerHTML = playing ? IC.pause : IC.play;
    $("#fullFav").innerHTML = song.fav ? IC.heartOn : IC.heart; $("#fullFav").classList.toggle("on", !!song.fav);
    $("#btnMode").innerHTML = IC[p.mode]; $("#modeLabel").textContent = MODE_NAME[p.mode];
    $("#fullErr").hidden = !p.error; $("#fullErr").textContent = p.error || "";
    $("#btnSleep").textContent = p.sleep_remaining ? "定时关闭 " + Math.ceil(p.sleep_remaining / 60) + " 分" : "定时关闭";
    if (!S.voling && p.volume != null) { $("#vol").value = p.volume; $("#volNum").textContent = p.volume; }
    $("#tDur").textContent = fmt(p.duration);
    tick();
    $$(".row[data-id]").forEach(r => r.classList.toggle("cur", +r.dataset.id === song.id));
  }
  function tick() {
    const p = S.st && S.st.player; if (!p || !p.song) return;
    const pos = curPos(), ratio = p.duration > 0 ? pos / p.duration : 0;
    $("#miniProg").style.width = (ratio * 100).toFixed(1) + "%";
    if (!S.seeking) { $("#seek").value = Math.round(ratio * 1000); $("#tCur").textContent = fmt(pos); }
  }
  setInterval(tick, 500);

  /* ---------------- 底部弹层 ---------------- */
  function sheet(html, onReady) {
    $("#sheetBody").innerHTML = html; $("#sheet").hidden = false;
    if (onReady) onReady($("#sheetBody"));
  }
  function closeSheet() { $("#sheet").hidden = true; $("#sheetBody").innerHTML = ""; if (S.qrTimer) { clearInterval(S.qrTimer); S.qrTimer = null; } }
  $(".sheet-bg").onclick = closeSheet;

  /* ---------------- 歌曲列表 ---------------- */
  function songRows(songs) {
    const cur = S.st && S.st.player && S.st.player.song ? S.st.player.song.id : -1;
    return songs.map(s => '<div class="row' + (s.id === cur ? " cur" : "") + '" data-id="' + s.id + '"><div class="cover sm">' + coverHtml(s) + '</div><div class="meta"><b>' + esc(s.title) +
      (s.ext !== "mp3" ? '<i class="tag">' + esc(s.ext) + "</i>" : "") + "</b><span>" + esc(s.artist || "未知歌手") + (s.album ? " · " + esc(s.album) : "") + '</span></div><span class="dur">' + fmt(s.duration) +
      '</span><button class="ib" data-more="' + s.id + '" aria-label="更多">' + IC.more + "</button></div>").join("");
  }
  function bindSongs(root, songs, source) {
    root.onclick = e => {
      const more = e.target.closest("[data-more]");
      if (more) { songMenu(songs.find(s => s.id === +more.dataset.more), source); return; }
      const row = e.target.closest(".row[data-id]");
      if (row) playSong(+row.dataset.id, source);
    };
  }
  function playSong(id, source) {
    if (!S.st || !S.st.current_device) { toast(S.st && S.st.auth.state === "ok" ? "没有可用的音箱" : "请先在设置里扫码登录小米账号"); return; }
    act(async () => { const d = await api("play", { song_id: id, source }); S.st.player = d.player; S.stAt = Date.now(); renderPlayer(); });
  }
  function playAll(source, shuffle) { act(async () => { const d = await api("play", { source, shuffle: !!shuffle }); S.st.player = d.player; S.stAt = Date.now(); renderPlayer(); }); }
  async function songMenu(s, source) {
    const pl = (await api("playlists")).playlists;
    sheet("<h2>" + esc(s.title) + '</h2><div class="muted">' + esc(s.artist || "未知歌手") + " · " + esc(s.path) + "</div>" +
      '<button class="row" data-a="play"><div class="meta"><b>立即播放</b></div></button>' +
      '<button class="row" data-a="next"><div class="meta"><b>下一首播放</b></div></button>' +
      '<button class="row" data-a="queue"><div class="meta"><b>加入播放队列</b></div></button>' +
      '<button class="row" data-a="fav"><div class="meta"><b>' + (s.fav ? "取消收藏" : "加入收藏") + "</b></div></button>" +
      (S.list && S.list.type === "playlist" ? '<button class="row" data-a="rm"><div class="meta"><b>从当前歌单移除</b></div></button>' : "") +
      "<h3>加入歌单</h3>" + pl.map(p => '<button class="row" data-pl="' + p.id + '"><div class="meta"><b>' + esc(p.name) + "</b><span>" + p.count + " 首</span></div></button>").join("") +
      '<button class="row" data-a="newpl"><div class="meta"><b>新建歌单并加入…</b></div></button>', root => {
        root.onclick = async e => {
          const b = e.target.closest("button"); if (!b) return;
          const a = b.dataset.a;
          closeSheet();
          if (b.dataset.pl) return act(() => api("playlists/" + b.dataset.pl, { add: [s.id] }), "已加入歌单");
          if (a === "play") playSong(s.id, source);
          else if (a === "next") act(() => api("queue", { add: [s.id], next: true }), "已安排为下一首");
          else if (a === "queue") act(() => api("queue", { add: [s.id] }), "已加入队列");
          else if (a === "fav") { await act(() => api("favorites", { song_id: s.id, on: !s.fav }), s.fav ? "已取消收藏" : "已收藏"); s.fav = !s.fav; if (S.tab === "lists" && S.list && S.list.type === "favorites") render(); }
          else if (a === "rm") { await act(() => api("playlists/" + S.list.id, { remove: [s.id] }), "已移除"); render(); }
          else if (a === "newpl") { const name = prompt("歌单名称"); if (name) act(() => api("playlists", { name, song_ids: [s.id] }), "已创建并加入"); }
        };
      });
  }

  /* ---------------- 视图：曲库 ---------------- */
  function renderScanInfo() {
    const l = S.st.library, el = $("#scanInfo"); if (!el) return;
    el.textContent = l.scanning ? "正在扫描… " + l.progress.done + "/" + l.progress.total : "共 " + l.count + " 首";
    if (S._wasScanning && !l.scanning) { S._wasScanning = false; render(); }
    S._wasScanning = l.scanning;
  }
  async function viewLibrary(v) {
    if (S.dir !== null) {
      const d = await api("songs?dir=" + encodeURIComponent(S.dir) + "&limit=1000");
      const src = { type: "folder", id: S.dir };
      v.innerHTML = '<div class="bar"><button class="ib" id="back">' + IC.back + "</button><h2 style='margin:0;flex:1'>" + esc(S.dir.split("/").pop() || "根目录") + '</h2></div><div class="bar"><button class="btn primary" id="pa">播放全部</button><button class="btn" id="ps">随机播放</button><span class="muted">' + d.total + ' 首</span></div><div class="card" id="songs">' + songRows(d.songs) + "</div>";
      $("#back").onclick = () => { S.dir = null; render(); };
      $("#pa").onclick = () => playAll(src); $("#ps").onclick = () => playAll(src, true);
      bindSongs($("#songs"), d.songs, src);
      return;
    }
    v.innerHTML = '<div class="seg"><button data-m="songs">全部歌曲</button><button data-m="folders">文件夹</button><button data-m="added">最近添加</button></div><div class="bar"><button class="btn primary" id="pa">播放全部</button><button class="btn" id="ps">随机播放</button><span class="muted" id="scanInfo"></span><span style="flex:1"></span><button class="pill" id="scan">重新扫描</button></div><div id="body"></div>';
    $$(".seg button", v).forEach(b => { b.classList.toggle("on", b.dataset.m === S.libMode); b.onclick = () => { S.libMode = b.dataset.m; render(); }; });
    $("#pa").onclick = () => playAll({ type: "all" }); $("#ps").onclick = () => playAll({ type: "all" }, true);
    $("#scan").onclick = () => act(() => api("scan", {}), "开始扫描");
    renderScanInfo();
    const body = $("#body");
    if (S.libMode === "folders") {
      const f = (await api("folders")).folders;
      body.innerHTML = f.length ? '<div class="card">' + f.map((x, i) => '<button class="row" data-i="' + i + '"><div class="cover sm">' + IC.folder + '</div><div class="meta"><b>' + esc(x.name) + "</b><span>" + esc(x.dir || "/") + " · " + x.count + " 首</span></div></button>").join("") + "</div>" : emptyLib();
      body.onclick = e => { const r = e.target.closest("[data-i]"); if (r) { S.dir = f[+r.dataset.i].dir; render(); } };
    } else {
      let songs = [], total = 0, loading = false;
      const sort = S.libMode === "added" ? "added" : "title";
      body.innerHTML = '<div class="card" id="songs"></div><div class="empty" id="more" hidden><button class="btn" id="moreBtn">加载更多</button></div>';
      const load = async () => {
        if (loading) return; loading = true;
        const d = await api("songs?sort=" + sort + "&limit=200&offset=" + songs.length);
        total = d.total; songs = songs.concat(d.songs);
        if (!total) { body.innerHTML = emptyLib(); return; }
        $("#songs").insertAdjacentHTML("beforeend", songRows(d.songs));
        $("#more").hidden = songs.length >= total; loading = false;
        bindSongs($("#songs"), songs, { type: "all" });
      };
      $("#moreBtn").onclick = load;
      await load();
    }
  }
  const emptyLib = () => '<div class="empty">曲库里还没有歌曲。<br>请把音乐目录挂载到容器的 /music，然后点「重新扫描」。</div>';

  /* ---------------- 视图：歌单 ---------------- */
  async function viewLists(v) {
    if (S.list) {
      const L = S.list;
      const d = L.type === "favorites" ? await api("favorites") : await api("playlists/" + L.id);
      const src = L.type === "favorites" ? { type: "favorites" } : { type: "playlist", id: L.id };
      v.innerHTML = '<div class="bar"><button class="ib" id="back">' + IC.back + "</button><h2 style='margin:0;flex:1'>" + esc(L.name) + "</h2>" +
        (L.type === "playlist" ? '<button class="pill" id="ren">改名</button><button class="pill" id="del">删除</button>' : "") +
        '</div><div class="bar"><button class="btn primary" id="pa">播放全部</button><button class="btn" id="ps">随机播放</button><span class="muted">' + d.songs.length + " 首</span></div>" +
        (d.songs.length ? '<div class="card" id="songs">' + songRows(d.songs) + "</div>" : '<div class="empty">这里还是空的。在歌曲右侧的「更多」里可以收藏或加入歌单。</div>');
      $("#back").onclick = () => { S.list = null; render(); };
      $("#pa").onclick = () => playAll(src); $("#ps").onclick = () => playAll(src, true);
      if ($("#songs")) bindSongs($("#songs"), d.songs, src);
      if ($("#ren")) $("#ren").onclick = async () => { const n = prompt("新的歌单名称", L.name); if (n) { await act(() => api("playlists/" + L.id, { name: n })); L.name = n; render(); } };
      if ($("#del")) $("#del").onclick = async () => { if (confirm("删除歌单「" + L.name + "」？歌曲文件不会被删除。")) { await act(() => api("playlists/" + L.id, undefined, "DELETE")); S.list = null; render(); } };
      return;
    }
    const d = await api("playlists");
    v.innerHTML = '<div class="bar"><h2 style="margin:0;flex:1">歌单</h2><button class="btn" id="new">新建歌单</button></div><div class="card"><button class="row" data-fav="1"><div class="cover sm">' + IC.heartOn + '</div><div class="meta"><b>我的收藏</b><span>' + d.favorites + " 首</span></div></button>" +
      d.playlists.map(p => '<button class="row" data-pl="' + p.id + '" data-name="' + esc(p.name) + '"><div class="cover sm">' + IC.list + '</div><div class="meta"><b>' + esc(p.name) + "</b><span>" + p.count + " 首</span></div></button>").join("") +
      '</div><p class="muted">文件夹会自动成为歌单，在「曲库 → 文件夹」里查看；语音说「播放歌单 + 文件夹名」也可以。</p>';
    $("#new").onclick = async () => { const n = prompt("歌单名称"); if (n) { await act(() => api("playlists", { name: n }), "已创建"); render(); } };
    v.querySelector(".card").onclick = e => {
      const r = e.target.closest(".row"); if (!r) return;
      S.list = r.dataset.fav ? { type: "favorites", name: "我的收藏" } : { type: "playlist", id: +r.dataset.pl, name: r.dataset.name };
      render();
    };
  }

  /* ---------------- 视图：搜索 ---------------- */
  function viewSearch(v) {
    v.innerHTML = '<h2>搜索</h2><input id="q" type="search" placeholder="歌名 / 歌手 / 专辑 / 文件名" value="' + esc(S.q) + '" enterkeyhint="search"><div id="res" style="margin-top:12px"></div>';
    let timer, seq = 0;
    const run = async () => {
      const q = S.q = $("#q").value.trim(), my = ++seq, res = $("#res");
      if (!q) { res.innerHTML = '<div class="empty">输入关键词开始搜索，支持「周杰伦 晴天」这样的组合，也能容忍个别错字。</div>'; return; }
      try {
        const d = await api("search?q=" + encodeURIComponent(q));
        if (my !== seq) return;
        res.innerHTML = d.songs.length ? '<div class="bar"><button class="btn primary" id="pa">播放全部结果</button><span class="muted">' + d.songs.length + ' 首</span></div><div class="card" id="songs">' + songRows(d.songs) + "</div>" : '<div class="empty">没有找到「' + esc(q) + "」</div>";
        const src = { type: "search", q };
        if ($("#songs")) { bindSongs($("#songs"), d.songs, src); $("#pa").onclick = () => playAll(src); }
      } catch (e) { toast(e.message); }
    };
    $("#q").oninput = () => { clearTimeout(timer); timer = setTimeout(run, 250); };
    run();
  }

  /* ---------------- 视图：设置 ---------------- */
  const tsText = t => t ? new Date(t * 1000).toLocaleString("zh-CN", { hour12: false }) : "—";
  function renderAuthCard() {
    const a = S.st.auth, el = $("#authCard"); if (!el) return;
    el.innerHTML = '<div class="kv"><span>连接状态</span><span><b style="color:var(--' + (a.state === "ok" ? "ok" : a.state === "need_qr" || a.state === "no_login" ? "bad" : "warn") + ')">' + esc(a.label) + "</b></span></div>" +
      '<div class="kv"><span>小米账号</span><span>' + esc(a.user_id || "未登录") + "</span></div>" +
      '<div class="kv"><span>最近一次成功</span><span>' + tsText(a.last_ok) + "</span></div>" +
      '<div class="kv"><span>最近一次续期</span><span>' + tsText(a.last_renew) + "</span></div>" +
      (a.last_error ? '<div class="kv"><span>最近错误</span><span>' + esc(a.last_error) + "（" + tsText(a.last_error_ts) + "）</span></div>" : "") +
      '<div class="kv"><span>语音口令</span><span>' + (S.st.voice.enabled ? "已开启，每 " + S.st.voice.interval + " 秒检查" + (S.st.voice.error ? "（" + esc(S.st.voice.error) + "）" : "") : "已关闭") + (S.st.voice.last_query ? "<br>上次：" + esc(S.st.voice.last_query) + " → " + esc(S.st.voice.last_result) : "") + "</span></div>" +
      '<div class="kv"><span>音箱拉流地址</span><span>' + esc(S.st.base_url) + "<br>" + esc(S.st.base_url_note) + "</span></div>";
  }
  async function viewSettings(v) {
    const d = await api("settings"), s = S.settings = d.settings, kw = s.keywords;
    const KW = [["play_song", "播放歌曲（后面可跟歌名）"], ["play_list", "播放歌单（后面跟歌单名）"], ["next", "下一首"], ["prev", "上一首"], ["pause", "暂停"], ["resume", "继续"], ["stop", "停止"], ["mode_one", "单曲循环"], ["mode_all", "全部循环"], ["mode_random", "随机播放"], ["fav_add", "加入收藏"], ["fav_del", "取消收藏"]];
    const num = (k, label, hint, step) => '<div class="field inline"><label>' + label + (hint ? '<span class="muted">' + hint + "</span>" : "") + '</label><input type="number" data-k="' + k + '" value="' + s[k] + '" step="' + (step || 1) + '"></div>';
    const sel = (k, label, opts, hint) => '<div class="field inline"><label>' + label + (hint ? '<span class="muted">' + hint + "</span>" : "") + '</label><select data-k="' + k + '">' + opts.map(o => '<option value="' + o[0] + '"' + (s[k] === o[0] ? " selected" : "") + ">" + o[1] + "</option>").join("") + "</select></div>";
    const theme = localStorage.getItem("nasbox_theme") || "auto";
    v.innerHTML = "<h2>设置</h2>" +
      (inApp() ? '<div class="card"><button class="row" id="appSet"><div class="meta"><b>App 设置 / 切换服务器</b><span>安卓 App' + (function () { try { return NasBoxApp.getVersion ? " " + esc(NasBoxApp.getVersion()) : ""; } catch (e) { return ""; } })() + "</span></div></button></div>" : "") +
      '<h3>小米账号与连接</h3><div class="card" id="authCard"></div><div class="bar"><button class="btn primary" id="qrBtn">扫码登录</button><button class="btn" id="chk">立即检测</button><button class="btn danger" id="miOut">退出小米账号</button></div>' +
      '<h3>音箱</h3><div class="card" id="devs"></div>' +
      '<h3>播放</h3><div class="card">' +
      '<div class="field"><label>音箱拉流地址</label><input data-k="base_url" placeholder="留空自动识别，例如 http://192.168.1.10:8090" value="' + esc(s.base_url) + '"><span class="muted">音箱要能访问到这个地址。当前生效：' + esc(d.base_url_effective) + "（" + esc(d.base_url_note) + '）</span><div class="bar" style="margin:8px 0 0"><button class="pill" id="useOrigin">填入当前浏览器地址</button></div></div>' +
      sel("transcode", "格式转换", [["auto", "仅转换下列格式"], ["all", "全部转为 MP3"], ["off", "不转换"]], "音箱放不出的格式由 NAS 用 ffmpeg 转成 MP3") +
      '<div class="field"><label>需要转换的格式</label><input data-k="transcode_formats" value="' + esc(s.transcode_formats.join(",")) + '"><span class="muted">逗号分隔。若 FLAC 在音箱上没声音，把 flac 加进来</span></div>' +
      sel("transcode_bitrate", "转换码率", [["128k", "128k"], ["192k", "192k"], ["320k", "320k"]]) +
      num("cache_max_mb", "转换缓存上限（MB）") +
      sel("play_api", "播放接口", [["auto", "自动（按型号）"], ["music", "player_play_music"], ["url", "player_play_url"]], "一般不用改；不出声时可切换试试") +
      sel("tts_mode", "文字播报方式", [["auto", "自动"], ["miot", "MIoT 指令"], ["mina", "小爱 TTS 接口"]]) +
      num("volume_step", "音量键每档步进") +
      num("status_poll_sec", "播放时状态检查间隔（秒）", "越小切歌越及时，请求越多", 0.5) +
      num("end_grace_sec", "兜底切歌宽限（秒）") +
      '</div><h3>语音口令</h3><div class="card">' +
      '<div class="field inline"><label>启用语音口令<span class="muted">通过定时读取小爱的对话记录实现；关闭后只能在界面里点播</span></label><input type="checkbox" class="sw" data-k="voice_enabled"' + (s.voice_enabled ? " checked" : "") + "></div>" +
      num("voice_poll_sec", "活跃时检查间隔（秒）", "", 0.5) + num("voice_poll_idle_sec", "空闲时检查间隔（秒）", "", 0.5) + num("voice_idle_after_min", "多久没用算空闲（分钟）") +
      '<div class="field inline"><label>曲库里有就由 NAS 播<span class="muted">不说“歌曲”也行：“播放周杰伦的歌”，歌手、专辑、歌名、歌单或文件夹名完全一致才接管，否则交给小爱</span></label><input type="checkbox" class="sw" data-k="voice_nas_first"' + (s.voice_nas_first ? " checked" : "") + "></div>" +
      '<div class="field inline"><label>被对话打断后自动重放<span class="muted">播放中调音量、问天气会把歌打断；小爱答完后从头重放当前这首</span></label><input type="checkbox" class="sw" data-k="voice_resume_after_talk"' + (s.voice_resume_after_talk ? " checked" : "") + "></div>" +
      '<div class="field inline"><label>找不到歌曲时语音提示</label><input type="checkbox" class="sw" data-k="voice_tts_feedback"' + (s.voice_tts_feedback ? " checked" : "") + "></div>" +
      KW.map(k => '<div class="field"><label>' + k[1] + '</label><input data-kw="' + k[0] + '" value="' + esc((kw[k[0]] || []).join(",")) + '"></div>').join("") +
      '<div class="field"><label>口令测试（只解析，不执行）</label><input id="vt" placeholder="例如：播放歌曲周杰伦的晴天"><span class="muted" id="vtRes"></span></div>' +
      '</div><h3>连接保活</h3><div class="card">' + num("keepalive_min", "会话检测间隔（分钟）") + num("renew_hours", "提前续期周期（小时）") + "</div>" +
      '<h3>界面与安全</h3><div class="card">' +
      '<div class="field inline"><label>外观</label><select id="theme"><option value="auto">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></div>' +
      '<div class="field"><label>访问密码' + (s.password_set ? "（已设置）" : "（未设置）") + '</label><input type="password" id="pw" placeholder="' + (s.password_set ? "输入新密码可修改" : "留空表示不启用") + '" autocomplete="new-password"><div class="bar" style="margin:8px 0 0"><button class="pill" id="pwSave">保存密码</button>' + (s.password_set ? '<button class="pill" id="pwClear">取消密码</button><button class="pill" id="logout">退出登录</button>' : "") + '</div><span class="muted">请只在家里局域网使用，不要把端口直接暴露到公网</span></div>' +
      sel("log_level", "日志级别", [["INFO", "普通"], ["DEBUG", "详细"]]) +
      '</div><div class="bar"><button class="btn" id="logView">查看日志</button><a class="btn" href="api/logs/download" download>下载脱敏日志</a></div>' +
      '<p class="muted">NAS 音乐盒 v' + esc(S.st.version) + " · 曲库 " + S.st.library.count + " 首</p>";
    renderAuthCard();
    const devs = S.st.devices;
    $("#devs").innerHTML = devs.length ? devs.map(x => '<button class="row" data-dev="' + esc(x.device_id) + '"><div class="meta"><b>' + esc(x.name) + (x.device_id === S.st.current_device ? "（当前）" : "") + "</b><span>" + esc(x.hardware) + " · " + (x.online ? "在线" : "离线") + "</span></div></button>").join("") : '<div class="empty">登录后这里会列出账号下的音箱</div>';
    $("#devs").onclick = e => { const r = e.target.closest("[data-dev]"); if (r) act(async () => { await api("device/select", { device_id: r.dataset.dev }); await refresh(); render(); }, "已切换音箱"); };
    $("#theme").value = theme;
    $("#theme").onchange = e => { localStorage.setItem("nasbox_theme", e.target.value); applyTheme(); };
    if ($("#appSet")) $("#appSet").onclick = () => { try { NasBoxApp.openSettings(); } catch (e) { toast("App 版本过旧，不支持此入口"); } };
    const save = patch => act(async () => { await api("settings", patch); await refresh(); }, "已保存");
    $$("[data-k]", v).forEach(el => { el.onchange = () => save({ [el.dataset.k]: el.type === "checkbox" ? el.checked : el.value }); });
    $$("[data-kw]", v).forEach(el => { el.onchange = () => { const k = {}; $$("[data-kw]", v).forEach(x => (k[x.dataset.kw] = x.value)); save({ keywords: k }); }; });
    $("#useOrigin").onclick = () => { const i = $('[data-k="base_url"]'); i.value = location.origin; i.onchange(); };
    $("#vt").oninput = async e => { try { const r = await api("voice/test", { text: e.target.value, dry: true }); $("#vtRes").textContent = r.parsed ? "命中：" + r.parsed[0] + (r.parsed[1] ? "，参数「" + r.parsed[1] + "」" : "") : "未命中任何口令（交给小爱自己处理）"; } catch (err) { /* 忽略 */ } };
    $("#qrBtn").onclick = qrLogin;
    $("#chk").onclick = () => act(async () => { await api("mi/check", {}); await refresh(); render(); }, "连接正常");
    $("#miOut").onclick = () => { if (confirm("退出小米账号？之后需要重新扫码才能控制音箱。")) act(async () => { await api("mi/logout", {}); await refresh(); render(); }, "已退出"); };
    $("#pwSave").onclick = () => { const p = $("#pw").value; if (!p) return toast("请输入密码"); act(async () => { await api("settings", { password: p }); render(); }, "访问密码已设置"); };
    if ($("#pwClear")) $("#pwClear").onclick = () => act(async () => { await api("settings", { password: "" }); render(); }, "已取消访问密码");
    if ($("#logout")) $("#logout").onclick = () => act(async () => { await api("logout", {}); showLogin(); });
    $("#logView").onclick = async () => { const r = await act(() => api("logs?lines=200")); if (r) sheet('<h2>最近日志</h2><pre class="log">' + esc(r.lines.join("\n")) + "</pre>", b => { const p = $("pre", b); p.scrollTop = p.scrollHeight; }); };
  }

  /* ---------------- 扫码登录 ---------------- */
  async function qrLogin() {
    sheet('<h2>扫码登录小米账号</h2><div id="qrBox" class="empty">正在获取二维码…</div>');
    const show = qr => {
      const box = $("#qrBox"); if (!box) return false;
      if (qr.state === "pending") { if (!box.querySelector(".qr")) box.innerHTML = '<div class="qr"><img alt="登录二维码" src="' + esc(qr.png) + '"></div><p>用<b>米家 App</b> 或小米手机相机扫码，并在手机上确认登录</p><p class="muted">二维码约 3 分钟有效。本程序不保存账号密码。</p>'; }
      else if (qr.state === "ok") { box.innerHTML = "<p><b>登录成功</b></p>" + (qr.error ? '<p class="muted">' + esc(qr.error) + "</p>" : ""); setTimeout(() => { closeSheet(); refresh().then(render); }, 900); return false; }
      else if (qr.state === "expired") { box.innerHTML = '<p>二维码已过期</p><button class="btn primary" id="qrAgain">重新获取</button>'; $("#qrAgain").onclick = qrLogin; return false; }
      else if (qr.state === "error") { box.innerHTML = "<p>" + esc(qr.error || "获取失败") + '</p><button class="btn primary" id="qrAgain">重试</button>'; $("#qrAgain").onclick = qrLogin; return false; }
      return true;
    };
    try {
      const d = await api("mi/qr/start", {});
      if (!show(d.qr)) return;
      S.qrTimer = setInterval(async () => {
        try { const r = await api("mi/qr/status"); if (!show(r.qr)) { clearInterval(S.qrTimer); S.qrTimer = null; } } catch (e) { /* 下次再试 */ }
      }, 2000);
    } catch (e) { const b = $("#qrBox"); if (b) b.textContent = e.message; }
  }

  /* ---------------- 设备选择 / 队列 / 定时 / 播报 ---------------- */
  function pickDevice() {
    const devs = (S.st && S.st.devices) || [];
    if (!devs.length) { go("settings"); return; }
    sheet("<h2>选择音箱</h2>" + devs.map(x => '<button class="row" data-dev="' + esc(x.device_id) + '"><div class="meta"><b>' + esc(x.name) + (x.device_id === S.st.current_device ? "（当前）" : "") + "</b><span>" + esc(x.hardware) + " · " + (x.online ? "在线" : "离线") + "</span></div></button>").join(""), root => {
      root.onclick = e => { const r = e.target.closest("[data-dev]"); if (r) { closeSheet(); act(async () => { await api("device/select", { device_id: r.dataset.dev }); await refresh(); }, "已切换音箱"); } };
    });
  }
  async function showQueue() {
    const d = await act(() => api("queue")); if (!d) return;
    sheet("<h2>播放队列</h2><div class='muted'>" + esc(d.source || "") + " · 共 " + d.total + " 首</div>" + d.songs.map((s, i) => '<button class="row' + (d.offset + i === d.index ? " cur" : "") + '" data-pos="' + (d.offset + i) + '"><div class="meta"><b>' + esc(s.title) + "</b><span>" + esc(s.artist || "未知歌手") + '</span></div><span class="dur">' + fmt(s.duration) + "</span></button>").join(""), root => {
      root.onclick = e => { const r = e.target.closest("[data-pos]"); if (r) { closeSheet(); ctl("play_index", +r.dataset.pos); } };
      const cur = $(".cur", root); if (cur) cur.scrollIntoView({ block: "center" });
    });
  }
  function sleepSheet() {
    sheet("<h2>定时关闭</h2>" + [15, 30, 45, 60, 90].map(m => '<button class="row" data-m="' + m + '"><div class="meta"><b>' + m + " 分钟后停止播放</b></div></button>").join("") + '<button class="row" data-m="0"><div class="meta"><b>取消定时</b></div></button>', root => {
      root.onclick = e => { const r = e.target.closest("[data-m]"); if (r) { closeSheet(); act(async () => { const d = await api("sleep", { minutes: +r.dataset.m }); S.st.player = d.player; renderPlayer(); }, +r.dataset.m ? "已设置定时关闭" : "已取消定时"); } };
    });
  }
  function ttsSheet() {
    sheet('<h2>让音箱播报文字</h2><textarea id="ttsText" rows="3" placeholder="例如：开饭了"></textarea><div class="bar" style="margin-top:10px"><button class="btn primary" id="ttsGo">播报</button></div>', () => {
      $("#ttsGo").onclick = () => { const t = $("#ttsText").value.trim(); if (!t) return; closeSheet(); act(() => api("tts", { text: t }), "已发送"); };
    });
  }

  /* ---------------- 路由 ---------------- */
  async function render() {
    const v = $("#view");
    $$("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === S.tab));
    try {
      if (S.tab === "library") await viewLibrary(v);
      else if (S.tab === "lists") await viewLists(v);
      else if (S.tab === "search") viewSearch(v);
      else await viewSettings(v);
    } catch (e) { if ($("#login").hidden) v.innerHTML = '<div class="empty">' + esc(e.message) + '<br><br><button class="btn" id="retry">重试</button></div>', $("#retry").onclick = render; }
  }
  function go(tab) { if (S.tab === tab) { S.dir = null; S.list = null; } S.tab = tab; $("#view").scrollTop = 0; render(); }

  /* ---------------- 事件绑定 ---------------- */
  function bind() {
    $$("[data-ic]").forEach(el => (el.innerHTML = IC[el.dataset.ic]));
    $("#miniNext").innerHTML = IC.next; $("#btnNext").innerHTML = IC.next; $("#btnPrev").innerHTML = IC.prev; $("#btnStop").innerHTML = IC.stop;
    $("#fullClose").innerHTML = IC.down; $("#fullQueue").innerHTML = IC.queue;
    $$("#tabs button").forEach(b => (b.onclick = () => go(b.dataset.tab)));
    $("#devBtn").onclick = pickDevice;
    $("#authChip").onclick = () => go("settings");
    $("#banner").onclick = e => { if (e.target.dataset.go === "qr") { e.preventDefault(); qrLogin(); } };
    $("#miniMeta").onclick = $("#miniCover").onclick = () => { $("#full").hidden = false; };
    $("#fullClose").onclick = () => { $("#full").hidden = true; };
    $("#miniToggle").onclick = $("#btnToggle").onclick = () => ctl("toggle");
    $("#miniNext").onclick = $("#btnNext").onclick = () => ctl("next");
    $("#btnPrev").onclick = () => ctl("prev");
    $("#btnStop").onclick = () => ctl("stop");
    $("#btnMode").onclick = () => { const m = MODES[(MODES.indexOf(S.st.player.mode) + 1) % MODES.length]; S.st.player.mode = m; renderPlayer(); ctl("mode", m).then(() => toast(MODE_NAME[m])); };
    $("#fullFav").onclick = () => { const s = S.st.player.song; act(async () => { await api("favorites", { song_id: s.id, on: !s.fav }); s.fav = !s.fav; renderPlayer(); }); };
    $("#fullQueue").onclick = showQueue; $("#btnSleep").onclick = sleepSheet; $("#btnTts").onclick = ttsSheet;
    const seek = $("#seek");
    seek.oninput = () => { S.seeking = true; $("#tCur").textContent = fmt(seek.value / 1000 * S.st.player.duration); };
    seek.onchange = () => { const t = seek.value / 1000 * S.st.player.duration; ctl("seek", t).then(() => (S.seeking = false)); };
    const vol = $("#vol"); let vt;
    vol.oninput = () => { S.voling = true; $("#volNum").textContent = vol.value; clearTimeout(vt); vt = setTimeout(() => ctl("volume", +vol.value).then(() => (S.voling = false)), 250); };
    $("#loginBtn").onclick = doLogin;
    $("#loginPw").onkeydown = e => { if (e.key === "Enter") doLogin(); };
    $("#loginApp").onclick = () => { try { NasBoxApp.openSettings(); } catch (e) { /* 忽略 */ } };
    // 安卓返回键 / 浏览器后退：先收起弹层
    window.addEventListener("popstate", () => { if (!$("#sheet").hidden) closeSheet(); else if (!$("#full").hidden) $("#full").hidden = true; });
  }

  /* 与安卓壳的约定：按手机音量键时调用，delta = +1 / -1 表示音箱音量加 / 减一档 */
  window.__nasbox_volume = function (delta) {
    delta = delta > 0 ? 1 : -1;
    if (!S.st || !S.st.current_device) return false;
    const p = S.st.player;
    if (p && p.volume != null) { const step = (S.settings && S.settings.volume_step) || 5; p.volume = Math.max(0, Math.min(100, p.volume + delta * step)); $("#vol").value = p.volume; $("#volNum").textContent = p.volume; toast("音箱音量 " + p.volume); }
    ctl("volume_step", delta);
    return true;
  };

  async function start() {
    try {
      const r = await fetch("api/status?refresh=1", { credentials: "same-origin" });
      const st = await r.json();
      if (st.need_login) { showLogin(); return; }
      onStatus(st);
    } catch (e) { setOffline(); $("#view").innerHTML = '<div class="empty">连不上 NAS 音乐盒服务<br><br><button class="btn" id="retry">重试</button></div>'; $("#retry").onclick = start; return; }
    connectEvents();
    if (S.st.auth.state === "no_login") S.tab = "settings";
    render();
  }

  applyTheme(); bind(); start();
  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) navigator.serviceWorker.register("sw.js").catch(() => {});
})();
