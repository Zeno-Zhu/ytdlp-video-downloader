// yt-dlp 任务中心：常驻标签页里的进度面板（弹窗关掉也能一直看）
const $ = (id) => document.getElementById(id);
const send = (msg) => new Promise((resolve) => chrome.runtime.sendMessage(msg, resolve));
const round1 = (n) => Math.round(n * 10) / 10;

let tasks = [];
let filter = "all";
let timer = null;
let healthOk = false;

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 2200);
}

function taskPath(t) {
  if (t.file_path) return t.file_path;
  if (t.dir && t.filename) return t.dir.replace(/[\\/]$/, "") + "\\" + t.filename;
  return t.dir || "";
}

function isActive(t) { return t.status === "downloading" || t.status === "queued"; }

async function refreshHealth() {
  try {
    const h = await send({ type: "health" });
    if (h && h.ok) {
      healthOk = true;
      $("health").className = "badge ok";
      $("health").textContent = `服务已连接 · yt-dlp ${h.yt_dlp || ""}`;
      return;
    }
    throw new Error((h && h.error) || "服务未就绪");
  } catch (e) {
    healthOk = false;
    $("health").className = "badge bad";
    $("health").textContent = "本地服务未启动";
  }
}

function renderOverview() {
  const active = tasks.filter(isActive);
  const done = tasks.filter((t) => t.status === "done");
  const failed = tasks.filter((t) => t.status === "error");
  const fill = $("ovFill");
  const bar = $("ovBar");
  if (active.length) {
    const pcts = active.map((t) => t.percent || 0);
    const avg = pcts.reduce((a, b) => a + b, 0) / pcts.length;
    $("ovTitle").textContent = `下载中 ${active.length} 个`;
    const speed = active.map((t) => t.speed).filter(Boolean)[0] || "";
    const eta = active.map((t) => t.eta).filter(Boolean)[0] || "";
    $("ovMeta").innerHTML = `<span class="pct">${avg.toFixed(1)}%</span>` +
      (speed ? ` <span class="spd">⚡ ${speed}</span>` : "") +
      (eta ? ` · 剩余 ${eta}` : "") +
      ` · 已完成 ${done.length}${failed.length ? ` · 失败 ${failed.length}` : ""}`;
    bar.classList.remove("done");
    fill.style.width = `${Math.min(99.9, avg)}%`;
  } else {
    $("ovTitle").textContent = tasks.length ? "当前没有进行中的下载" : "暂无下载任务";
    $("ovMeta").textContent = `已完成 ${done.length} 个${failed.length ? ` · 失败 ${failed.length} 个` : ""}` +
      (done.length ? ` · 最近：${(done[0].filename || "").slice(0, 40)}` : "");
    bar.classList.add("done");
    fill.style.width = done.length ? "100%" : "0%";
  }
  $("updatedAt").textContent = `最后更新 ${new Date().toLocaleTimeString()}`;
  document.querySelectorAll(".chip").forEach((c) => {
    c.classList.toggle("active", c.dataset.f === filter);
  });
}

function makeTask(t) {
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
  const statusText = { queued: "排队中", downloading: "下载中", done: "已完成", error: "失败" }[t.status] || t.status;
  const fmt = { best: "最佳画质", audio: "MP3", mp4: "MP4" }[t.format]
    || (String(t.format).startsWith("id:") ? "指定清晰度" : String(t.format));
  const parts = [`${statusText} · ${fmt}`, t.created_at];
  if (isActive(t)) parts.push(`<span class="pct">${(t.percent || 0).toFixed(1)}%</span>`);
  if (t.size_mb) parts.push(`${t.size_mb} MB`);
  if (isActive(t) && t.speed) parts.push(`<span class="spd">⚡ ${t.speed}</span>`);
  if (isActive(t) && t.eta) parts.push(`剩余 ${t.eta}`);
  if (t.finished_at) parts.push(`完成于 ${t.finished_at}`);
  meta.innerHTML = parts.join(" · ");
  card.appendChild(meta);

  if (isActive(t)) {
    const wrap = document.createElement("div");
    wrap.className = "bar";
    const fill = document.createElement("div");
    fill.style.width = `${t.percent || 0}%`;
    wrap.appendChild(fill);
    card.appendChild(wrap);
  }

  if (t.error && t.status === "error") {
    const err = document.createElement("div");
    err.className = "error-text";
    err.textContent = `⚠ ${t.error}`;
    card.appendChild(err);
  }

  const p = taskPath(t);
  if (p) {
    const path = document.createElement("div");
    path.className = "path";
    path.textContent = `📁 ${p}`;
    path.title = "点击定位到该文件";
    path.addEventListener("click", () => openFile(t));
    card.appendChild(path);
  }

  const actions = document.createElement("div");
  actions.className = "actions";
  if (t.status === "done") {
    actions.appendChild(btn("🎯 定位文件", () => openFile(t), "btn-primary"));
    actions.appendChild(btn("📂 打开文件夹", () => openFolder(t.dir), "btn-ghost"));
  } else if (t.status === "error") {
    actions.appendChild(btn("↻ 重试", async () => {
      await send({ type: "retry", taskId: t.id });
      toast("已重新加入队列");
      refresh();
    }, "btn-primary"));
    actions.appendChild(btn("📂 打开文件夹", () => openFolder(t.dir), "btn-ghost"));
  } else {
    actions.appendChild(btn("📂 打开文件夹", () => openFolder(t.dir || null), "btn-ghost"));
  }
  actions.appendChild(btn("🔗 复制链接", async () => {
    await navigator.clipboard.writeText(t.url).catch(() => {});
    toast("链接已复制");
  }, "btn-ghost"));
  card.appendChild(actions);
  return card;
}

function btn(text, onClick, cls) {
  const b = document.createElement("button");
  b.className = `${cls} btn-mini`;
  b.textContent = text;
  b.addEventListener("click", onClick);
  return b;
}

async function openFile(t) {
  const p = taskPath(t);
  if (!p) { openFolder(t.dir); return; }
  const res = await send({ type: "openFolder", path: p });
  toast(res && res.ok ? "已在资源管理器中定位该文件" : `打开失败：${(res && res.error) || ""}`);
}

async function openFolder(dir) {
  const res = await send({ type: "openFolder", dir: dir || undefined });
  toast(res && res.ok ? `已打开 ${res.dir || ""}` : `打开失败：${(res && res.error) || ""}`);
}

function render() {
  const list = $("list");
  const shown = tasks.filter((t) => {
    if (filter === "active") return isActive(t);
    if (filter === "done") return t.status === "done";
    if (filter === "error") return t.status === "error";
    return true;
  });
  renderOverview();
  list.innerHTML = "";
  if (!shown.length) {
    list.innerHTML = `<div class="empty">${tasks.length ? "该分类下暂无任务" : "暂无下载任务"}</div>`;
    return;
  }
  for (const t of shown) list.appendChild(makeTask(t));
}

async function refresh() {
  try {
    const res = await send({ type: "tasks" });
    tasks = (res && res.tasks) || [];
  } catch (e) {
    tasks = [];
  }
  render();
  clearTimeout(timer);
  timer = setTimeout(refresh, tasks.some(isActive) ? 1000 : 4000);
}

document.querySelectorAll(".chip").forEach((c) => {
  c.addEventListener("click", () => { filter = c.dataset.f; render(); });
});
$("refresh").addEventListener("click", () => { refreshHealth(); refresh(); });
$("openDir").addEventListener("click", () => openFolder(null));

refreshHealth();
refresh();
setInterval(refreshHealth, 15000);
