// yt-dlp 下载助手 - 后台 Service Worker v4
// 职责：① 与本地服务通信（未启动自动拉起）② 同步浏览器 Cookie
//       ③ 全局进度轮询（图标角标 + 完成通知 + 页面进度）④ 右键菜单
const SERVER = "http://127.0.0.1:8787";
const POLL_ACTIVE = 1200;      // 有下载任务时的轮询间隔
const POLL_IDLE = 8000;        // 空闲时的轮询间隔
const pageInfos = new Map();   // tabId -> {url, info}
const notified = new Map();    // taskId -> 已通知过的状态
let pollTimer = null;
let lastTasks = [];
let badgeFlashUntil = 0;

// 需要浏览器登录态（Cookie）才能解析的站点：插件自动把浏览器 Cookie 同步给本地服务
const COOKIE_SITES = [
  { label: "抖音", domain: "douyin.com", hosts: [/(^|\.)douyin\.com$/, /(^|\.)iesdouyin\.com$/] },
  { label: "TikTok", domain: "tiktok.com", hosts: [/(^|\.)tiktok\.com$/] },
  { label: "B站", domain: "bilibili.com", hosts: [/(^|\.)bilibili\.com$/] },
];

function cookieSiteFor(url) {
  try {
    const h = new URL(url).hostname.toLowerCase();
    return COOKIE_SITES.find((s) => s.hosts.some((re) => re.test(h))) || null;
  } catch (e) {
    return null;
  }
}

// Chrome Cookie -> Netscape cookies.txt（httpOnly 必须加 #HttpOnly_ 前缀，否则会丢登录态）
function toNetscape(cookies) {
  const lines = ["# Netscape HTTP Cookie File",
    "# 由 yt-dlp 视频下载助手从浏览器自动同步"];
  let count = 0;
  for (const c of cookies) {
    const dom = c.domain || "";
    const name = c.name || "";
    const value = c.value == null ? "" : String(c.value);
    if (!dom || !name) continue;
    if (/[\t\r\n]/.test(name) || /[\t\r\n]/.test(value)) continue;
    let exp = Math.floor(c.expirationDate || 0);
    if (exp <= 0) exp = Math.floor(Date.now() / 1000) + 31536000;  // 会话 Cookie 给一年有效期
    const flag = dom.startsWith(".") ? "TRUE" : "FALSE";
    lines.push(`${c.httpOnly ? "#HttpOnly_" : ""}${dom}\t${flag}\t${c.path || "/"}\t` +
      `${c.secure ? "TRUE" : "FALSE"}\t${exp}\t${name}\t${value}`);
    count++;
  }
  return { content: lines.join("\n") + "\n", count };
}

// 把当前浏览器里该站点的 Cookie 推给本地服务（yt-dlp 解析/下载时会自动使用）
async function syncCookies(url) {
  const site = cookieSiteFor(url);
  if (!site) return null;
  let cookies = [];
  try {
    cookies = await chrome.cookies.getAll({ domain: site.domain });
  } catch (e) {
    return { ok: false, site: site.label, error: `读取浏览器 Cookie 失败：${e.message || e}` };
  }
  if (!cookies.length) {
    return { ok: false, site: site.label, count: 0,
      error: `浏览器里还没有 ${site.domain} 的 Cookie：请先在浏览器打开一次该站点（例如抖音视频页）再重试` };
  }
  const { content, count } = toNetscape(cookies);
  try {
    const res = await fetch(`${SERVER}/api/cookies`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host: site.domain, content }),
    });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) return { ok: false, site: site.label, error: data.error || "保存 Cookie 失败" };
    return { ok: true, site: site.label, domain: site.domain, count: data.count || count };
  } catch (e) {
    return { ok: false, site: site.label, error: "本地服务未连接，无法保存 Cookie" };
  }
}

// ---------- 一键启动本地服务（Native Messaging） ----------
let launching = null;

function launchServer() {
  return new Promise((resolve) => {
    let port;
    try {
      port = chrome.runtime.connectNative("com.ytdlp.server");
    } catch (e) {
      resolve({ ok: false, error: `无法连接本地启动器：${e.message || e}（请双击运行 install_host.bat 安装）` });
      return;
    }
    let settled = false;
    const finish = (res) => {
      if (settled) return;
      settled = true;
      try { port.disconnect(); } catch (e) { /* ignore */ }
      resolve(res);
    };
    port.onMessage.addListener((msg) => finish(msg || { ok: false, error: "无响应" }));
    port.onDisconnect.addListener(async () => {
      if (settled) return;
      const err = chrome.runtime.lastError ? chrome.runtime.lastError.message : "";
      // 启动器应答后会主动退出（避免残留进程），此时可能先收到 disconnect：
      // 直接查一次 /health 兜底判断服务是否真的起来了
      const health = await rawApi("/health").catch(() => null);
      finish(health && health.ok
        ? { ok: true, status: "started", version: health.version, download_dir: health.download_dir }
        : { ok: false, error: err || "本地启动器未安装（请双击 install_host.bat 一键安装）" });
    });
    port.postMessage({ type: "start" });
    setTimeout(() => finish({ ok: false, error: "启动超时" }), 15000);
  });
}

// 同一时刻只允许一个启动请求，避免重复拉起
function ensureServer() {
  if (!launching) {
    launching = launchServer().finally(() => {
      setTimeout(() => { launching = null; }, 4000);
    });
  }
  return launching;
}

// ---------- 与本地服务通信 ----------
function isConnError(e) {
  const m = String((e && e.message) || e);
  return e instanceof TypeError || /fetch|network|failed|连接|refused/i.test(m);
}

async function rawApi(path, options = {}) {
  const res = await fetch(`${SERVER}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `服务响应异常 (${res.status})`);
  return data;
}

// 服务没启动时自动拉起一次再重试（用户无需先手动打开程序）
async function api(path, options = {}, allowLaunch = true) {
  try {
    return await rawApi(path, options);
  } catch (e) {
    if (!allowLaunch || !isConnError(e)) throw e;
    const res = await ensureServer();
    if (!res || !res.ok) {
      const err = new Error((res && res.error) || "本地服务未启动");
      err.launchFailed = true;
      throw err;
    }
    return await rawApi(path, options);
  }
}

// 默认下载格式：MP4 容器 + 720p 优先（回退 480 及以下）
const DEFAULT_FORMAT = "720mp4";

function startDownload(url, format = DEFAULT_FORMAT, dir = null, referer = null) {
  const body = { url, format };
  if (dir) body.dir = dir;
  if (referer) body.referer = referer;
  return api("/api/download", { method: "POST", body: JSON.stringify(body) });
}

async function prepareCookies(url) {
  if (!cookieSiteFor(url)) return null;
  return await syncCookies(url);
}

async function preview(url) {
  const sync = await prepareCookies(url);
  let data = await api("/api/preview", { method: "POST", body: JSON.stringify({ url }) });
  if (data && data.ok && sync && sync.ok) {
    data.cookies_synced = sync;      // 已自动同步给本地服务，告诉用户一声
  }
  if (data && data.ok === false && data.error_code === "need_cookies") {
    const again = await syncCookies(url);
    if (again && again.ok) {
      data = await api("/api/preview", { method: "POST", body: JSON.stringify({ url }) });
      if (data && data.ok) data.cookies_synced = again;
    }
    if (data && data.ok === false) data.cookie_sync = again || sync;
  }
  return data;
}

// ---------- 打开文件夹 / 定位文件 ----------
function openPath(path, dir) {
  const body = {};
  if (path) body.path = path;
  if (dir) body.dir = dir;
  return api("/api/open-folder", { method: "POST", body: JSON.stringify(body) }, false);
}

// ---------- 页面与任务的匹配（悬浮按钮显示进度用） ----------
function domainRoot(u) {
  try {
    const h = new URL(u).hostname.toLowerCase().replace(/^www\./, "");
    const p = h.split(".");
    return p.length > 2 && /^(com|net|org|gov|edu|co)\.[a-z]{2}$/.test(p.slice(-2).join("."))
      ? p.slice(-3).join(".") : p.slice(-2).join(".");
  } catch (e) {
    return "";
  }
}

function matchTask(tasks, url) {
  if (!url) return null;
  const pick = (list) => {
    const exact = list.find((t) => t.url === url);
    if (exact) return exact;
    const ids = String(url).match(/\d{8,}/g) || [];   // 视频 ID 相同即为同一视频
    if (ids.length) {
      const byId = list.find((t) => ids.some((i) => String(t.url).includes(i)));
      if (byId) return byId;
    }
    const root = domainRoot(url);                      // 同站点（如 v.douyin.com 与 www.douyin.com）
    if (root) {
      const same = list.filter((t) => domainRoot(t.url) === root);
      if (same.length) return same[0];
    }
    return null;
  };
  // 优先匹配"正在下载"的任务，避免页面按钮显示成上一次已完成的任务
  return pick(tasks.filter((t) => t.status === "downloading" || t.status === "queued")) ||
    pick(tasks);
}

// ---------- 全局进度：图标角标 + 完成通知 ----------
function activeTasks(tasks) {
  return tasks.filter((t) => t.status === "downloading" || t.status === "queued");
}

function updateBadge(tasks) {
  const active = activeTasks(tasks);
  try {
    if (active.length) {
      const pct = Math.floor(Math.max(...active.map((t) => t.percent || 0)));
      chrome.action.setBadgeBackgroundColor({ color: "#f03c3c" });
      chrome.action.setBadgeText({ text: active.length > 1 ? `${active.length}↓` : `${pct}%` });
      chrome.action.setTitle({ title: `yt-dlp 下载助手 · 下载中 ${active.length} 个` });
      return;
    }
    if (Date.now() < badgeFlashUntil) return;   // 完成/失败的短暂提示，稍后再清
    chrome.action.setBadgeText({ text: "" });
    chrome.action.setTitle({ title: "yt-dlp 下载助手" });
  } catch (e) { /* ignore */ }
}

function flashBadge(text, color, ms = 10000) {
  badgeFlashUntil = Date.now() + ms;
  try {
    chrome.action.setBadgeBackgroundColor({ color });
    chrome.action.setBadgeText({ text });
  } catch (e) { /* ignore */ }
  setTimeout(() => {
    if (!activeTasks(lastTasks).length) {
      try { chrome.action.setBadgeText({ text: "" }); } catch (e) { /* ignore */ }
    }
  }, ms + 200);
}

function notifyTask(task) {
  const size = task.size_mb ? `（${task.size_mb} MB）` : "";
  const done = task.status === "done";
  chrome.notifications.create(`ytdlp-${task.id}`, {
    type: "basic",
    iconUrl: "icons/icon128.png",
    title: done ? "✅ 下载完成" : "❌ 下载失败",
    message: done
      ? `${(task.filename || "").slice(0, 60)}${size}`
      : `${(task.error || "未知错误").slice(0, 120)}`,
    contextMessage: done ? "点击定位到文件" : "点击打开下载目录",
    buttons: [{ title: "📊 任务中心" }],
    priority: 1,
  });
}

// 轮询任务（弹窗、任务中心页、页面按钮都依赖它维护角标与通知）
let lastSig = null;
let lastTasksAt = 0;

function broadcastTasksChanged(tasks) {
  const active = activeTasks(tasks);
  // 任务集合（含 id）变化时才广播：新任务开始 / 全部结束 / 换了任务
  const sig = `${active.length}|${active.map((t) => t.id).join(",")}`;
  if (sig === lastSig) return;
  lastSig = sig;
  chrome.tabs.query({}, (tabs) => {
    for (const t of tabs || []) {
      if (!t.url || !/^https?:/.test(t.url)) continue;
      chrome.tabs.sendMessage(t.id, { type: "tasksChanged", active: active.length },
        () => void chrome.runtime.lastError);
    }
  });
}

async function pollTasks() {
  let tasks = [];
  try {
    const res = await api("/api/tasks", {}, false);
    tasks = (res && res.tasks) || [];
  } catch (e) {
    schedulePoll(false);
    return;
  }
  lastTasks = tasks;
  lastTasksAt = Date.now();
  for (const t of tasks) {
    const prev = notified.get(t.id);
    if (prev === t.status) continue;
    const first = prev === undefined;
    notified.set(t.id, t.status);
    if (first) continue;                       // 首次看到的历史任务不通知
    if (t.status === "done") {
      flashBadge("✓", "#34c759");
      notifyTask(t);
    } else if (t.status === "error") {
      flashBadge("!", "#ff453a", 15000);
      notifyTask(t);
    }
  }
  // 清理过老的记录，避免 Map 无限增长
  if (notified.size > 300) {
    const ids = new Set(tasks.map((t) => t.id));
    for (const id of [...notified.keys()]) if (!ids.has(id)) notified.delete(id);
  }
  updateBadge(tasks);
  broadcastTasksChanged(tasks);
  schedulePoll(activeTasks(tasks).length > 0);
}

function schedulePoll(active) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(pollTasks, active ? POLL_ACTIVE : POLL_IDLE);
}

// ---------- 通知点击 / 按钮 ----------
chrome.notifications.onClicked.addListener(async (id) => {
  chrome.notifications.clear(id);
  const m = /^ytdlp-([0-9a-f]{8,})$/.exec(id);
  if (!m) { openPath(null, null).catch(() => {}); return; }
  try {
    const t = await api(`/api/tasks/${m[1]}`, {}, false);
    if (t && t.status === "done") { await openPath(t.file_path || null, t.dir); return; }
    await openPath(null, (t && t.dir) || null);
  } catch (e) {
    openPath(null, null).catch(() => {});
  }
});

chrome.notifications.onButtonClicked.addListener((id, idx) => {
  chrome.notifications.clear(id);
  if (idx === 0) openTasksPage();
});

function openTasksPage() {
  const url = chrome.runtime.getURL("tasks.html");
  chrome.tabs.query({}, (tabs) => {
    const hit = (tabs || []).find((t) => t.url && t.url.startsWith(url));
    if (hit) {
      chrome.tabs.update(hit.id, { active: true });
      chrome.windows.update(hit.windowId, { focused: true });
    } else {
      chrome.tabs.create({ url });
    }
  });
}

// ---------- 右键菜单 ----------
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "ytdlp-download",
    title: "用 yt-dlp 下载此视频",
    contexts: ["page", "video", "audio", "link"],
  });
  chrome.alarms.create("ytdlp-poll", { periodInMinutes: 0.5 });
  pollTasks();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("ytdlp-poll", { periodInMinutes: 0.5 });
  pollTasks();
});

if (chrome.alarms && chrome.alarms.onAlarm) {
  chrome.alarms.onAlarm.addListener((a) => {
    if (a.name === "ytdlp-poll") pollTasks();
  });
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.linkUrl || info.srcUrl || (tab && tab.url);
  if (!url) return;
  try {
    await prepareCookies(url);
    await startDownload(url);
    chrome.notifications.create(`ytdlp-q-${Date.now()}`, {
      type: "basic",
      iconUrl: "icons/icon128.png",
      title: "📥 已加入下载队列",
      message: url.slice(0, 100),
      contextMessage: "进度可在图标角标或任务中心查看",
      buttons: [{ title: "📊 任务中心" }],
    });
    pollTasks();
  } catch (e) {
    chrome.notifications.create(`ytdlp-e-${Date.now()}`, {
      type: "basic",
      iconUrl: "icons/icon128.png",
      title: "下载失败",
      message: `${e.message}（本地服务会自动启动，若持续失败请运行 install_host.bat）`,
    });
  }
});

// 启动时先跑一次轮询，保证角标状态正确
schedulePoll(false);

// ---------- 页面信息（content script 上报） ----------
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    switch (msg.type) {
      case "pageInfo": {
        if (sender.tab && sender.tab.id != null) {
          pageInfos.set(sender.tab.id, { url: sender.url || sender.tab.url, info: msg.info });
        }
        return { ok: true };
      }
      case "getPageInfo": {
        return pageInfos.get(msg.tabId) || null;
      }
      case "downloadFromPage": {
        await prepareCookies(msg.url);
        const { task_id } = await startDownload(msg.url, msg.format || DEFAULT_FORMAT, msg.dir || null);
        pollTasks();
        return { ok: true, task_id };
      }
      case "download": {
        await prepareCookies(msg.url);
        const { task_id } = await startDownload(msg.url, msg.format || "best", msg.dir, msg.referer);
        pollTasks();
        return { ok: true, task_id };
      }
      case "listDir": {
        const p = msg.path ? `?path=${encodeURIComponent(msg.path)}` : "";
        return await api(`/api/list-dir${p}`);
      }
      case "setDefaultDir":
        return await api("/api/set-default-dir", { method: "POST", body: JSON.stringify({ dir: msg.dir }) });
      case "preview": {
        return await preview(msg.url);
      }
      case "syncCookies": {
        const res = await syncCookies(msg.url);
        return res || { ok: false, error: "该站点无需 Cookie" };
      }
      case "retry": {
        const res = await api("/api/retry", { method: "POST", body: JSON.stringify({ task_id: msg.taskId }) });
        pollTasks();
        return res;
      }
      case "health":
        return await api("/health");
      case "launchServer":
        return await ensureServer();
      case "tasks":
        // 轮询不做自动拉起，避免每 1~2 秒尝试一次；由 health 检查负责自动启动服务
        return await api("/api/tasks", {}, false);
      case "openFolder":
        return await openPath(msg.path || null, msg.dir || null);
      case "openTasksPage":
        openTasksPage();
        return { ok: true };
      case "pageProgress": {
        // 页面悬浮按钮用：返回该页面（或同一站点/同一视频）的下载进度
        const url = msg.url || "";
        let tasks = lastTasks;
        if (!tasks.length || Date.now() - lastTasksAt > 2000) {
          tasks = await api("/api/tasks", {}, false).then((r) => (r && r.tasks) || []).catch(() => lastTasks);
          lastTasks = tasks;
          lastTasksAt = Date.now();
        }
        return { ok: true, task: matchTask(tasks, url) };
      }
      default:
        throw new Error("未知消息类型");
    }
  })().then(sendResponse, (e) => sendResponse({ ok: false, error: e.message }));
  return true; // 异步响应
});
