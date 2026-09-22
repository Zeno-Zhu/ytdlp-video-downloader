// yt-dlp 下载助手 - 弹窗逻辑 v4
const $ = (id) => document.getElementById(id);
let healthOk = false;
let previewData = null;      // 最近一次预览结果
let currentTabId = null;
let selectedReferer = null;  // 直链下载时的 Referer（TikTok/抖音直链）
let defaultDir = "";         // 服务端默认下载目录
let recentDirs = [];         // 最近使用过的文件夹
let taskTimer = null;
const round1 = (n) => Math.round(n * 10) / 10;

function send(msg) {
  return new Promise((resolve) => chrome.runtime.sendMessage(msg, resolve));
}

function sendToTab(tabId, msg) {
  return new Promise((resolve) => {
    try {
      chrome.tabs.sendMessage(tabId, msg, (res) => {
        if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
        else resolve(res || { ok: false });
      });
    } catch (e) {
      resolve({ ok: false, error: String(e) });
    }
  });
}

function hint(text) {
  $("hint").textContent = text;
}

// 从粘贴的分享文案里提取链接（抖音/快手等的分享文本含大量说明文字）
function pickUrl(text) {
  const s = (text || "").trim();
  const m = s.match(/https?:\/\/[^\s"'<>，。、）)】\]]+/);
  return m ? m[0].replace(/[.,;，。；、]+$/, "") : s;
}

// ---------- 保存位置 ----------
function renderRecentDirs() {
  const dl = $("recentDirs");
  dl.innerHTML = "";
  for (const d of recentDirs) {
    const o = document.createElement("option");
    o.value = d;
    dl.appendChild(o);
  }
}

async function rememberDir(dir) {
  if (!dir) return;
  recentDirs = [dir, ...recentDirs.filter((d) => d !== dir)].slice(0, 6);
  chrome.storage.local.set({ ytdlpRecentDirs: recentDirs });
  renderRecentDirs();
}

function currentDir() {
  return $("saveDir").value.trim();
}

async function applyDefaultDir(dir) {
  defaultDir = dir;
  if (!currentDir()) $("saveDir").value = dir;
  await rememberDir(dir);
}

function updateDirMode() {
  const dir = currentDir();
  const isDefault = defaultDir && dir && dir.toLowerCase() === defaultDir.toLowerCase();
  const badge = $("dirMode");
  badge.textContent = isDefault ? "默认位置" : "本次下载";
  badge.style.background = isDefault ? "" : "#252112";
  badge.style.color = isDefault ? "" : "var(--warn)";
  badge.style.borderColor = isDefault ? "" : "#5c522a";
}

// ---------- 健康检查 ----------
async function refreshHealth() {
  const el = $("health");
  const launchBtn = $("launchBtn");
  if (!healthOk) {
    el.className = "badge";
    el.textContent = "连接服务中…";
  }
  try {
    const h = await send({ type: "health" });
    if (h && h.ok) {
      healthOk = true;
      el.className = "badge ok";
      el.textContent = `服务已连接 · yt-dlp ${h.yt_dlp || ""}`;
      launchBtn.style.display = "none";
      const ck = (h.cookies || []).map((c) => c.host.split(".")[0]).join("/");
      hint(`下载目录：${h.download_dir}${ck ? ` · 已同步 Cookie：${ck}` : ""}`);
      await applyDefaultDir(h.download_dir);
      updateDirMode();
    } else throw new Error((h && h.error) || "服务未就绪");
  } catch (e) {
    healthOk = false;
    el.className = "badge bad";
    el.textContent = "本地服务未启动";
    launchBtn.style.display = "";
    hint(`❌ ${e.message}\n点上方「⚡ 启动服务」即可拉起；若提示未安装启动器，双击运行 install_host.bat（自动识别扩展 ID，无需手工填写）`);
  }
  updateDownloadBtn();
}

// ---------- 一键启动服务 ----------
async function launchServer() {
  const btn = $("launchBtn");
  btn.disabled = true;
  btn.textContent = "启动中…";
  hint("正在启动本地服务…");
  try {
    const res = await send({ type: "launchServer" });
    if (res && res.ok) {
      const map = {
        started: "✅ 服务已启动",
        already_running: "✅ 服务已在运行",
        running_other_version: "⚠️ 已有旧版本服务在运行，建议重启电脑或结束旧进程",
      };
      hint(map[res.status] || "✅ 服务已启动");
    } else {
      hint(`❌ ${(res && res.error) || "启动失败"}\n可双击 install_host.bat 一键安装启动器（自动识别扩展 ID）`);
    }
  } catch (e) {
    hint(`❌ ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "⚡ 启动服务";
    await refreshHealth();
  }
}

// ---------- 当前标签页 URL + 页面检测信息 ----------
async function fillFromTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) return;
    currentTabId = tab.id;
    const page = await send({ type: "getPageInfo", tabId: tab.id });
    if (page && page.info && page.info.isVideoPage) {
      $("pageBadge").style.display = "";
      const yt = page.info.yt;
      if (yt && yt.hasAdSlots) {
        $("pvAd").classList.add("show");
        if (yt.adActive) {
          $("pvAd").textContent = "⚠️ 检测到页面正在播放广告！下载的目标仍是视频本体（非广告），确认下方解析出的标题是否为目标视频";
        } else {
          $("pvAd").textContent = "ℹ️ 该视频含广告位，但 yt-dlp 下载的是视频本体，不受广告影响";
        }
      }
    }
    if (tab.url && /^https?:\/\//.test(tab.url)) {
      $("url").value = tab.url;
      if (!$("hint").textContent.startsWith("下载目录")) {
        hint(`已获取当前页面：${tab.url.slice(0, 50)}…`);
      }
    }
  } catch { /* 忽略 */ }
}

// ---------- 页面视频候选列表 ----------
async function refreshCandidates() {
  const box = $("pageVideos");
  if (!currentTabId) { box.style.display = "none"; return; }
  const res = await sendToTab(currentTabId, { type: "getCandidates" });
  const cands = (res && res.ok && res.candidates) || [];
  if (!cands.length) { box.style.display = "none"; return; }
  box.style.display = "";
  const list = $("pageVideoList");
  list.innerHTML = "";
  const more = cands.length >= 20;
  cands.slice(0, 20).forEach((c) => {
    const item = document.createElement("div");
    item.className = "pv-item";
    const img = document.createElement("img");
    img.src = c.thumb || "";
    img.onerror = () => { img.style.display = "none"; };
    const title = document.createElement("span");
    title.className = "pv-title";
    title.textContent = c.title || c.url;
    title.title = c.title || c.url;
    const badge = document.createElement("span");
    badge.className = "pv-badge";
    badge.textContent = "选择 ▸";
    item.append(img, title, badge);
    item.addEventListener("click", () => pickCandidate(c));
    list.appendChild(item);
  });
  if (more) {
    const tip = document.createElement("div");
    tip.className = "pv-item";
    tip.style.cursor = "default";
    tip.textContent = "（仅显示前 20 个，可刷新页面后重试）";
    list.appendChild(tip);
  }
}

async function pickCandidate(c) {
  selectedReferer = c.referer || null;
  const hl = await sendToTab(currentTabId, { type: "highlightCandidate", id: c.id });
  $("url").value = c.url;
  $("preview").classList.remove("show");
  if (hl && hl.ok) {
    hint(`已选择：${c.title.slice(0, 30)}…（网页已红框高亮，确认后点下载）`);
  } else {
    hint(`已选择：${c.title.slice(0, 30)}…`);
  }
  await doPreview();
}

// ---------- 预览 ----------
let previewing = false;
let previewTimer = null;

function queuePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(doPreview, 900);
}

async function doPreview() {
  const url = pickUrl($("url").value);
  if (!url || !/^https?:\/\//.test(url)) {
    hint("请输入以 http(s):// 开头的视频链接（粘贴抖音分享文案也会自动提取链接）");
    return;
  }
  if (url !== $("url").value.trim()) $("url").value = url;
  if (previewing) return;
  previewing = true;
  $("previewBtn").textContent = "解析中…";
  try {
    const res = await send({ type: "preview", url });
    if (res && res.ok) {
      renderPreview(res);
      const ck = res.cookies_synced
        ? ` · 🍪 已同步${res.cookies_synced.site} Cookie ${res.cookies_synced.count} 条`
        : (res.cookies_used ? ` · 🍪 使用 ${res.cookies_used}` : "");
      hint(`解析完成 · ${res.previewed_at}${ck}`);
    } else {
      showPreviewError(res);
    }
  } catch (e) {
    showPreviewError({ error: e.message });
  } finally {
    previewing = false;
    $("previewBtn").textContent = "🔍 解析视频信息";
  }
}

function showPreviewError(res) {
  const msg = (res && res.error) || "解析失败";
  let extra = "";
  const code = res && res.error_code;
  if (code === "need_cookies") {
    const sync = res.cookie_sync;
    extra = sync && sync.error
      ? `\n🍪 ${sync.error}`
      : "\n🍪 需要该站点的浏览器登录态：请先在浏览器打开一次该视频页（抖音/TikTok/B站），再回来解析（插件会自动同步 Cookie）";
  } else if (res && res.hint) {
    extra = `\n${res.hint}`;
  } else if (code === "network" || code === "timeout" || /Failed to fetch|本地服务/.test(msg)) {
    extra = "\n请确认网络/代理（Clash 等）已开启；若提示服务未启动，点「⚡ 启动服务」";
  }
  $("preview").classList.remove("show");
  hint(`❌ 解析失败：${msg}${extra}`);
  updateDownloadBtn();
}

function codecLabel(f) {
  const parts = [];
  if (f.fps && f.fps > 30) parts.push(`${f.fps}fps`);
  if (f.vcodec === "h265") parts.push("HEVC");
  if (f.watermarked) parts.push("含水印");
  if (f.has_audio === false) parts.push("无音轨");
  return parts.length ? ` · ${parts.join(" · ")}` : "";
}

function renderPreview(res) {
  previewData = res;
  $("pvTitle").textContent = res.title || "(无标题)";
  $("pvAuthor").textContent = res.author ? `频道：${res.author}` : "";
  $("pvDuration").textContent = (res.duration ? `时长：${res.duration}` : "") +
    (res.extractor ? ` · ${res.extractor}` : "");

  const sel = $("fmtSelect");
  sel.innerHTML = "";
  const opts = [];
  const fmts = res.formats || [];
  const maxH = fmts.length ? fmts[0].height : 0;
  const bestSize = fmts.length ? fmts[0].size : null;
  opts.push({
    value: "best", height: maxH,
    label: `最佳画质（最高 ${maxH}p）`,
    size: bestSize != null && res.audio_size != null ? round1(bestSize + res.audio_size) : bestSize,
  });
  for (const f of fmts) {
    const est = f.size != null && f.has_audio === false && res.audio_size != null
      ? round1(f.size + res.audio_size) : f.size;
    opts.push({
      value: `id:${f.format_id}`,
      height: f.height,
      label: `${f.height}p${codecLabel(f)}`,
      size: est,
    });
  }
  if (res.audio_size != null) {
    opts.push({ value: "audio", height: 0, label: "仅音频 MP3", size: res.audio_size });
  }
  for (const o of opts) {
    const opt = document.createElement("option");
    opt.value = o.value;
    opt.textContent = o.size != null ? `${o.label} · 约 ${o.size} MB` : o.label;
    sel.appendChild(opt);
  }

  // 默认选中：在具体清晰度里挑 720p 优先 → 480p → ≤720 的最大档 → 兜底最佳画质
  const fmtOpts = opts.map((o, i) => ({ o, i })).filter((x) => x.o.value.startsWith("id:"));
  const at = (h) => fmtOpts.find((x) => x.o.height === h);
  let defaultIdx = 0;
  const h720 = at(720), h480 = at(480);
  if (h720) defaultIdx = h720.i;
  else if (h480) defaultIdx = h480.i;
  else {
    const fit = fmtOpts.filter((x) => x.o.height > 0 && x.o.height <= 720)
      .sort((a, b) => b.o.height - a.o.height)[0];
    if (fit) defaultIdx = fit.i;
  }
  sel.selectedIndex = defaultIdx;
  updateSizeLabel();
  $("preview").classList.add("show");
  updateDownloadBtn();
}

function updateSizeLabel() {
  const v = $("fmtSelect").value;
  if (!previewData) return;
  for (const opt of $("fmtSelect").options) {
    if (opt.value === v) {
      const m = opt.textContent.match(/约 ([\d.]+) MB/);
      let txt = m ? `预计 ${m[1]} MB` : "";
      if (/HEVC/.test(opt.textContent)) txt += (txt ? " · " : "") + "H.265 需系统解码器";
      $("pvSize").textContent = txt;
      break;
    }
  }
}

// ---------- 下载 ----------
async function doDownload() {
  const url = pickUrl($("url").value);
  if (!url) { hint("请输入视频链接"); return; }
  if (!/^https?:\/\//.test(url)) { hint("链接必须以 http(s):// 开头（可直接粘贴分享文案）"); return; }
  let format;
  if (previewData) {
    const v = $("fmtSelect").value;
    format = v === "best" ? "best" : v === "audio" ? "audio" : v;
  } else {
    format = "720mp4"; // 默认 MP4 + 720p 优先
  }
  const dir = currentDir() || null;
  if (dir && !/^[a-zA-Z]:[\\/]/.test(dir)) { hint("保存位置需为绝对路径，如 D:\\Videos"); return; }
  const btn = $("download");
  btn.disabled = true;
  btn.textContent = "提交中…";
  try {
    // 勾选了「同时设为默认」才改动全局默认目录，否则只对本次下载生效
    if ($("setAsDefault").checked && dir && dir !== defaultDir) {
      const res = await send({ type: "setDefaultDir", dir });
      if (res && res.ok) {
        defaultDir = res.download_dir;
        $("setAsDefault").checked = false;
      } else if (res && res.error) {
        hint(`⚠️ 默认目录设置失败：${res.error}（本次仍会保存到 ${dir}）`);
      }
    }
    await rememberDir(dir);
    const msg = { type: "download", url, format, dir };
    if (selectedReferer) msg.referer = selectedReferer;
    const res = await send(msg);
    if (res && res.ok) {
      hint(`📥 已加入下载队列（${res.task_id}）\n保存到：${dir || defaultDir}｜进度见图标角标或「📊 任务中心」`);
      $("preview").classList.remove("show");
      await refreshTasks();
    } else {
      hint(`❌ ${(res && res.error) || "下载失败"}`);
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "下载";
    updateDownloadBtn();
  }
}

function updateDownloadBtn() {
  $("download").disabled = !healthOk;
}

// ---------- 任务列表 ----------
function taskPath(t) {
  if (t.file_path) return t.file_path;
  if (t.dir && t.filename) return t.dir.replace(/[\\/]$/, "") + "\\" + t.filename;
  return t.dir || "";
}

const isActive = (t) => t.status === "downloading" || t.status === "queued";

function renderSummary(tasks) {
  const head = $("tasksHead");
  const active = tasks.filter(isActive);
  if (!tasks.length) { head.style.display = "none"; return; }
  head.style.display = "";
  if (active.length) {
    const avg = active.reduce((a, t) => a + (t.percent || 0), 0) / active.length;
    $("tasksSummary").textContent = `⚡ 下载中 ${active.length} 个 · ${avg.toFixed(1)}%`;
    const spd = active.map((t) => t.speed).filter(Boolean)[0] || "";
    const eta = active.map((t) => t.eta).filter(Boolean)[0] || "";
    $("tasksSpeed").textContent = spd ? `${spd}${eta ? ` · 剩余 ${eta}` : ""}` : "";
    $("tasksBar").style.width = `${Math.min(99.9, avg)}%`;
  } else {
    const done = tasks.filter((t) => t.status === "done").length;
    const bad = tasks.filter((t) => t.status === "error").length;
    $("tasksSummary").textContent = `已结束 · 完成 ${done}${bad ? ` · 失败 ${bad}` : ""}`;
    $("tasksSpeed").textContent = "";
    $("tasksBar").style.width = done ? "100%" : "0%";
  }
}

async function refreshTasks(force) {
  const wrap = $("tasks");
  let tasks = [];
  let ok = true;
  try {
    const res = await send({ type: "tasks" });
    tasks = (res && res.tasks) || [];
  } catch {
    ok = false;
  }
  if (!ok) {
    wrap.innerHTML = '<div class="empty">无法连接本地服务</div>';
    $("tasksHead").style.display = "none";
    return;
  }
  renderSummary(tasks);
  if (!tasks.length) {
    wrap.innerHTML = '<div class="empty">暂无下载任务</div>';
  } else {
    wrap.innerHTML = "";
    for (const t of tasks.slice(0, 12)) wrap.appendChild(makeTaskCard(t));
  }
  clearTimeout(taskTimer);
  taskTimer = setTimeout(refreshTasks, tasks.some(isActive) ? 1000 : 3000);
}

function makeTaskCard(t) {
  const card = document.createElement("div");
  card.className = "task";

  const head = document.createElement("div");
  head.className = "task-head";
  const dot = document.createElement("span");
  dot.className = `dot ${t.status}`;
  const title = document.createElement("span");
  title.className = "task-title";
  title.textContent = t.filename || t.url;
  title.title = t.url;
  head.append(dot, title);
  card.appendChild(head);

  const meta = document.createElement("div");
  meta.className = "task-meta";
  const fmtName = { best: "最佳", mp4: "MP4", audio: "MP3" }[t.format]
    || (String(t.format).startsWith("id:") ? "指定清晰度"
      : (String(t.format).endsWith("mp4") ? `MP4·${String(t.format).slice(0, -3)}p` : `${t.format}p`));
  const parts = [`[${t.status}] ${fmtName}`, t.created_at];
  if (isActive(t)) parts.push(`${(t.percent || 0).toFixed(1)}%`);
  if (t.size_mb) parts.push(`${t.size_mb} MB`);
  if (isActive(t) && t.speed) {
    const spd = document.createElement("span");
    spd.className = "spd";
    spd.textContent = `⚡ ${t.speed}${t.eta ? ` · 剩余 ${t.eta}` : ""}`;
    meta.append(...parts.map((p) => { const s = document.createElement("span"); s.textContent = p; return s; }), spd);
  } else {
    meta.textContent = parts.join(" · ");
  }
  card.appendChild(meta);

  if (t.error && t.status === "error") {
    const err = document.createElement("div");
    err.className = "error-text";
    err.textContent = `⚠ ${t.error}`;
    card.appendChild(err);
  }

  const path = taskPath(t);
  if (t.status === "done" && path) {
    const p = document.createElement("div");
    p.className = "task-path";
    p.textContent = `📁 ${path}`;
    p.title = "点击定位到该文件";
    p.addEventListener("click", () => openFile(path));
    card.appendChild(p);
  }

  if (isActive(t)) {
    const bar = document.createElement("div");
    bar.className = "bar";
    const fill = document.createElement("div");
    fill.style.width = `${t.percent || 0}%`;
    bar.appendChild(fill);
    card.appendChild(bar);
  }

  const actions = document.createElement("div");
  actions.className = "task-actions";
  if (t.status === "done") {
    actions.appendChild(miniBtn("🎯 定位文件", () => openFile(path), "btn-primary"));
    actions.appendChild(miniBtn("📂 打开文件夹", () => openFolder(t.dir)));
  } else if (t.status === "error") {
    actions.appendChild(miniBtn("↻ 重试", async (b) => {
      b.disabled = true;
      await send({ type: "retry", taskId: t.id });
      refreshTasks();
    }, "btn-primary"));
    actions.appendChild(miniBtn("📂 打开文件夹", () => openFolder(t.dir)));
  } else {
    actions.appendChild(miniBtn("📂 打开文件夹", () => openFolder(t.dir || null)));
  }
  actions.appendChild(miniBtn("🔗 复制链接", async () => {
    await navigator.clipboard.writeText(t.url).catch(() => {});
    hint("链接已复制");
  }));
  card.appendChild(actions);
  return card;
}

function miniBtn(text, onClick, cls) {
  const b = document.createElement("button");
  b.className = `${cls || "btn-ghost"}`;
  b.textContent = text;
  b.addEventListener("click", () => onClick(b));
  return b;
}

async function openFile(path) {
  const res = await send({ type: "openFolder", path });
  hint(res && res.ok ? "✅ 已在资源管理器中定位该文件" : `打开失败：${(res && res.error) || ""}`);
}

async function openFolder(dir) {
  const res = await send({ type: "openFolder", dir: dir || undefined });
  hint(res && res.ok ? `✅ 已打开 ${res.dir || ""}` : `打开失败：${(res && res.error) || ""}`);
}

// ---------- 目录浏览器（仅本次下载，除非勾选「设为默认」） ----------
let browsingPath = null;

async function openDirBrowser(startPath) {
  browsingPath = startPath || "";
  $("dirBrowser").classList.add("show");
  await loadDirList();
}

async function loadDirList() {
  const pathEl = $("dirPath");
  const listEl = $("dirList");
  pathEl.textContent = browsingPath || "选择磁盘";
  listEl.innerHTML = '<div class="dir-empty">加载中…</div>';
  try {
    const res = await send({ type: "listDir", path: browsingPath });
    if (!res || !res.ok) throw new Error((res && res.error) || "目录读取失败");
    $("dirUp").disabled = !res.parent;
    if (res.kind === "drives") {
      pathEl.textContent = "选择磁盘";
      listEl.innerHTML = "";
      res.items.forEach((d) => listEl.appendChild(makeDirItem(d.name, d.path, true)));
    } else {
      listEl.innerHTML = "";
      if (!res.items.length) {
        listEl.innerHTML = '<div class="dir-empty">（空目录）</div>';
      }
      res.items.forEach((d) => listEl.appendChild(makeDirItem(d.name, d.path, false)));
    }
  } catch (e) {
    pathEl.textContent = "读取失败";
    listEl.innerHTML = `<div class="dir-empty">${e.message}</div>`;
  }
}

function makeDirItem(name, path, isDrive) {
  const item = document.createElement("div");
  item.className = "dir-item";
  const ico = document.createElement("span");
  ico.className = "ico";
  ico.textContent = isDrive ? "💽" : "📁";
  const label = document.createElement("span");
  label.textContent = name;
  item.append(ico, label);
  item.addEventListener("click", async () => {
    browsingPath = path;
    await loadDirList();
  });
  return item;
}

async function pickDir() {
  if (!browsingPath) { hint("请先进入一个文件夹"); return; }
  $("saveDir").value = browsingPath;
  await rememberDir(browsingPath);
  updateDirMode();
  if ($("setAsDefault").checked) {
    const res = await send({ type: "setDefaultDir", dir: browsingPath });
    if (res && res.ok) {
      defaultDir = res.download_dir;
      $("setAsDefault").checked = false;
      hint(`✅ 已设为默认下载目录：${res.download_dir}`);
    } else {
      hint(`⚠️ 默认目录设置失败：${(res && res.error) || ""}（本次仍会保存到该文件夹）`);
    }
  } else {
    hint(`本次下载将保存到：${browsingPath}（勾选「设为默认」可记住）`);
  }
  updateDirMode();
  closeDirBrowser();
}

function closeDirBrowser() {
  $("dirBrowser").classList.remove("show");
}

// ---------- 事件绑定 ----------
$("previewBtn").addEventListener("click", doPreview);
$("fmtSelect").addEventListener("change", updateSizeLabel);
$("browseDir").addEventListener("click", () => openDirBrowser(currentDir()));
$("openSaveDir").addEventListener("click", () => openFolder(currentDir() || defaultDir));
$("dirUp").addEventListener("click", async () => {
  const res = await send({ type: "listDir", path: browsingPath });
  if (res && res.parent) { browsingPath = res.parent; await loadDirList(); }
});
$("dirCancel").addEventListener("click", closeDirBrowser);
$("dirSelect").addEventListener("click", pickDir);
$("saveDir").addEventListener("input", updateDirMode);
$("saveDir").addEventListener("change", () => {
  const v = currentDir();
  if (v) { rememberDir(v); updateDirMode(); }
});
$("setAsDefault").addEventListener("change", () => {
  if ($("setAsDefault").checked) hint("勾选后，本次下载同时把该文件夹设为默认保存位置");
});
$("url").addEventListener("input", () => { $("preview").classList.remove("show"); queuePreview(); });
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") doPreview(); });
$("download").addEventListener("click", doDownload);
$("refresh").addEventListener("click", () => { refreshHealth(); refreshTasks(true); });
$("launchBtn").addEventListener("click", launchServer);
$("openTasks").addEventListener("click", () => {
  chrome.tabs.create({ url: chrome.runtime.getURL("tasks.html") });
  window.close();
});
$("syncCookie").addEventListener("click", async () => {
  const url = pickUrl($("url").value);
  if (!url) { hint("请先填入链接"); return; }
  const btn = $("syncCookie");
  btn.disabled = true;
  hint("正在同步浏览器 Cookie…");
  const res = await send({ type: "syncCookies", url });
  btn.disabled = false;
  hint(res && res.ok
    ? `🍪 已同步 ${res.site} Cookie ${res.count} 条（${res.domain}）`
    : `🍪 ${(res && res.error) || "该站点不需要 Cookie"}`);
});
$("copyUrl").addEventListener("click", async () => {
  const url = pickUrl($("url").value);
  if (!url) return;
  await navigator.clipboard.writeText(url).catch(() => {});
  hint("链接已复制");
});
$("openFolder").addEventListener("click", () => openFolder(currentDir() || defaultDir));

// 初始化
chrome.storage.local.get(["ytdlpSaveDir", "ytdlpRecentDirs"], (r) => {
  recentDirs = Array.isArray(r.ytdlpRecentDirs) ? r.ytdlpRecentDirs : [];
  renderRecentDirs();
  if (r.ytdlpSaveDir && !currentDir()) $("saveDir").value = r.ytdlpSaveDir;
  if (currentDir()) chrome.storage.local.set({ ytdlpSaveDir: currentDir() });
  updateDirMode();
});
fillFromTab();
refreshHealth();
refreshTasks();
refreshCandidates();
