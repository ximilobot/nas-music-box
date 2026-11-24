// 前端冒烟测试：在 jsdom 里加载真实页面，连真实服务端进程（服务端连模拟云端）。
// 准备：npm i jsdom；先启动 tests/mock_cloud.py 18999 和指向它的服务端（端口 18090）。
// 运行：node tests/ui_smoke.js nasbox/static
const { JSDOM } = require("jsdom");
const fs = require("fs"), path = require("path");
const BASE = process.env.NASBOX_URL || "http://127.0.0.1:18090", MOCK = process.env.MOCK_URL || "http://127.0.0.1:18999";
const STATIC = process.argv[2] || path.join(__dirname, "..", "nasbox", "static");
const sleep = ms => new Promise(r => setTimeout(r, ms));
let pass = 0, fail = 0;
const ok = (c, name) => { if (c) { pass++; console.log("  ok   " + name); } else { fail++; console.log("  FAIL " + name); } };
const mock = async p => (await fetch(MOCK + "/__test/" + p)).json();
async function until(fn, name, ms = 8000) {
  const t = Date.now();
  while (Date.now() - t < ms) { try { if (await fn()) return ok(true, name); } catch (e) { /* 再等 */ } await sleep(100); }
  ok(false, name + "（超时）");
}
const errors = [];
process.on("unhandledRejection", e => errors.push("unhandledRejection " + (e && e.stack || e)));

async function boot({ withApp, jar }) {
  const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8").replace('<script src="app.js"></script>', "");
  const dom = new JSDOM(html, { url: BASE + "/", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window, calls = [];
  w.addEventListener("error", e => errors.push(String(e.error && e.error.stack || e.message)));
  w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  w.fetch = async (u, opt = {}) => {
    const headers = Object.assign({}, opt.headers || {});
    if (jar.cookie) headers.Cookie = jar.cookie;
    const r = await fetch(new URL(u, BASE + "/"), Object.assign({}, opt, { headers }));
    const sc = r.headers.get("set-cookie"); if (sc) jar.cookie = sc.split(";")[0];
    return r;
  };
  w.EventSource = class { constructor() { this.readyState = 0; } close() { this.readyState = 2; } };   // 让页面走轮询兜底
  w.prompt = () => "测试歌单"; w.confirm = () => true;
  w.Element.prototype.scrollIntoView = () => {};
  if (withApp) w.NasBoxApp = { openSettings: () => calls.push("openSettings"), setBarColor: c => calls.push("setBarColor:" + c), getVersion: () => "1.0-test", reload() {}, showMenu() {} };
  w.eval(fs.readFileSync(path.join(STATIC, "app.js"), "utf8"));
  const $ = s => w.document.querySelector(s), $$ = s => [...w.document.querySelectorAll(s)];
  const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const refresh = async () => { w.document.dispatchEvent(new w.Event("visibilitychange")); await sleep(400); };
  return { w, $, $$, click, calls, refresh };
}

(async () => {
  const jar = {};
  console.log("[1] 未登录首屏 + 扫码登录 + 安卓壳桥");
  const p = await boot({ withApp: true, jar });
  await until(() => p.$("#authLabel").textContent === "未登录", "顶部状态显示「未登录」");
  ok(!p.$("#banner").hidden && p.$("#banner").textContent.includes("扫码登录"), "横幅提示扫码登录");
  await until(() => p.$("#qrBtn"), "未登录时默认进入设置页");
  ok(!!p.$("#appSet"), "壳内显示「App 设置 / 切换服务器」入口");
  p.click(p.$("#appSet")); ok(p.calls.includes("openSettings"), "点击入口调用 NasBoxApp.openSettings()");
  ok(p.calls.some(c => c.startsWith("setBarColor:#")), "启动时调用 " + p.calls.find(c => c.startsWith("setBarColor")));
  ok(p.$('meta[name="theme-color"]').content.startsWith("#"), "带 theme-color meta");
  const sel = p.$("#theme"); sel.value = "dark"; sel.dispatchEvent(new p.w.Event("change"));
  ok(p.w.document.documentElement.dataset.theme === "dark" && p.calls.filter(c => c.startsWith("setBarColor")).length >= 2, "切换深色后再次同步状态栏颜色");
  p.click(p.$("#qrBtn"));
  await until(() => p.$("#qrBox .qr img"), "弹层里显示二维码图片");
  await mock("scan");
  await until(() => p.$("#authLabel").textContent === "正常", "扫码后状态变为「正常」", 12000);
  await until(() => p.$("#devName").textContent === "客厅音箱", "顶部显示当前音箱");
  await until(() => p.$$("#devs .row").length === 2, "设置页列出 2 台音箱");

  console.log("[2] 曲库 / 播放 / 控制");
  p.click(p.$('#tabs [data-tab="library"]'));
  await until(() => p.$$("#songs .row").length === 8, "曲库列出 8 首歌");
  await mock("duration?ms=12000");
  const row = p.$$("#songs .row").find(r => r.textContent.includes("长曲"));
  p.click(row.querySelector(".meta"));
  await until(async () => (await mock("state")).play_log.length === 1, "点歌后音箱收到播放地址");
  const st = await mock("state");
  const media = await fetch(st.play_log[0], { headers: { Range: "bytes=0-99" } });
  ok(media.status === 206, "音箱视角用 Range 拉流返回 206");
  await until(() => !p.$("#mini").hidden && p.$("#miniTitle").textContent === "长曲", "迷你播放条显示歌名");
  await p.refresh();
  p.click(p.$("#miniMeta")); ok(!p.$("#full").hidden, "点迷你条打开全屏播放页");
  await until(() => p.$("#tDur").textContent === "0:12", "全屏页显示时长 0:12");
  ok(p.w.__nasbox_volume(+1) === true, "window.__nasbox_volume(+1) 可调用");
  await until(async () => (await mock("state")).volume === 35, "音量键加一档：音箱音量 30 → 35");
  p.w.__nasbox_volume(-1); await sleep(600); p.w.__nasbox_volume(-1);
  await until(async () => (await mock("state")).volume === 25, "音量键减两档：35 → 25");
  p.click(p.$("#btnToggle"));
  await until(async () => (await mock("state")).status === 2, "点暂停后音箱暂停");
  p.click(p.$("#btnToggle"));
  await until(async () => (await mock("state")).status === 1, "点继续后音箱恢复");
  await sleep(2500);
  const nBefore = (await mock("state")).play_log.length;
  p.click(p.$("#btnNext"));
  await until(async () => (await mock("state")).play_log.length === nBefore + 1, "下一首");
  p.click(p.$("#btnMode"));
  await until(() => p.$("#modeLabel").textContent === "单曲循环", "切换播放模式");
  p.click(p.$("#btnMode")); p.click(p.$("#btnMode")); p.click(p.$("#btnMode"));
  await until(() => p.$("#modeLabel").textContent === "全部循环", "模式切回全部循环");
  p.click(p.$("#fullFav"));
  await until(() => p.$("#fullFav").classList.contains("on"), "收藏当前歌曲");
  p.click(p.$("#fullQueue"));
  await until(() => p.$$("#sheetBody .row").length === 8, "队列弹层列出 8 首");
  p.click(p.$(".sheet-bg"));
  p.click(p.$("#btnStop"));
  await until(async () => (await mock("state")).status === 0, "停止");
  p.click(p.$("#fullClose"));

  console.log("[3] 搜索 / 歌单");
  p.click(p.$('#tabs [data-tab="search"]'));
  const q = p.$("#q"); q.value = "周杰伦 晴天"; q.dispatchEvent(new p.w.Event("input"));
  await until(() => p.$$("#songs .row").length >= 1 && p.$("#songs .row b").textContent.startsWith("晴天"), "搜索「周杰伦 晴天」首条为晴天");
  p.click(p.$('#tabs [data-tab="lists"]'));
  await until(() => p.$("#new"), "歌单页");
  ok(p.$("#view").textContent.includes("我的收藏") && p.$("#view").textContent.includes("1 首"), "收藏里有 1 首");
  p.click(p.$("#new"));
  await until(() => p.$("#view").textContent.includes("测试歌单"), "新建歌单");

  console.log("[4] 语音口令（真实进程轮询模拟云端的对话记录）");
  const n0 = (await mock("state")).play_log.length;
  await sleep(6000);                                   // 等轮询器记下基线
  await mock("say?q=" + encodeURIComponent("播放歌曲长曲"));
  await until(async () => { const s = await mock("state"); return s.play_log.length === n0 + 1 && s.status === 1; }, "说「播放歌曲长曲」后音箱开始播放", 15000);
  await until(async () => { await p.refresh(); return p.$("#miniTitle").textContent === "长曲"; }, "界面同步显示《长曲》");
  await mock("say?q=" + encodeURIComponent("停止"));
  await until(async () => (await mock("state")).status === 0, "说「停止」后停止", 10000);

  console.log("[5] 掉线恢复（真实进程）");
  const r0 = (await mock("state")).renew_count;
  await mock("expire");
  p.click(p.$('#tabs [data-tab="library"]'));
  await until(() => p.$$("#songs .row").length === 8, "回到曲库");
  p.click(p.$$("#songs .row").find(r => r.textContent.includes("长曲")).querySelector(".meta"));
  await until(async () => { const s = await mock("state"); return s.renew_count === r0 + 1 && s.status === 1; }, "serviceToken 过期后点歌：自动续期并播放成功");
  await p.refresh();
  ok(p.$("#authLabel").textContent === "正常", "续期后状态仍为「正常」");
  p.click(p.$("#miniToggle"));

  console.log("[6] 访问密码：页面内登录");
  await p.w.fetch("api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: "pw-123456" }) });
  const idx = await fetch(BASE + "/"); ok(idx.status === 200, "设置密码后首页仍返回 200");
  const s2 = await (await fetch(BASE + "/api/status")).json();
  ok(s2.app === "nas-music-box" && s2.need_login === true && !("devices" in s2), "免登录 /api/status 返回 200 JSON，且不含敏感字段");
  const p2 = await boot({ withApp: true, jar: {} });
  await until(() => !p2.$("#login").hidden, "未登录访问显示页面内登录框");
  ok(!p2.$("#loginApp").hidden, "登录框里也有「切换服务器」入口");
  p2.$("#loginPw").value = "wrong"; p2.click(p2.$("#loginBtn"));
  await until(() => p2.$("#loginErr").textContent === "密码不正确", "错误密码有提示");
  p2.$("#loginPw").value = "pw-123456"; p2.click(p2.$("#loginBtn"));
  await until(() => p2.$("#login").hidden && p2.$("#authLabel").textContent === "正常", "正确密码登录后进入界面");
  await p.w.fetch("api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: "" }) });

  ok(errors.length === 0, "页面脚本无未捕获异常" + (errors.length ? "\n" + errors.join("\n") : ""));
  console.log(`\n前端冒烟：通过 ${pass}，失败 ${fail}`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
