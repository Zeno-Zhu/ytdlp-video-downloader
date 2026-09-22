// yt-dlp 下载助手 - 内容脚本 v3
// 职责：① 识别页面视频信息（含 YouTube 广告状态）② 注入悬浮下载按钮
//       ③ 扫描页面视频候选列表 ④ 高亮对应视频位置供用户确认
(() => {
  if (window.__ytdlpContentLoaded) return;
  window.__ytdlpContentLoaded = true;

  // ---------- 视频信息采集（原逻辑） ----------
  function getVideoInfo() {
    const v = document.querySelector("video");
    const info = {
      pageTitle: document.title,
      videoSrc: v && (v.currentSrc || v.src) ? (v.currentSrc || v.src) : null,
      videoDuration: v && v.duration && isFinite(v.duration) ? Math.round(v.duration) : null,
      yt: null,
      isVideoPage: false,
    };

    try {
      const p = window.ytInitialPlayerResponse;
      if (p && p.videoDetails && /youtube\.com\/watch|youtu\.be/.test(location.href)) {
        const vd = p.videoDetails;
        const hasAdSlots = !!(p.adPlacements && p.adPlacements.length) || !!(p.playerAds && p.playerAds.length);
        const adActive = hasAdSlots && v && !v.paused && v.duration > 0 && v.duration < 120
          && (vd.lengthSeconds === undefined || v.duration < vd.lengthSeconds * 0.5);
        info.yt = {
          title: vd.title, author: vd.author, videoId: vd.videoId,
          lengthSeconds: vd.lengthSeconds, hasAdSlots, adActive,
        };
        info.isVideoPage = true;
      }
    } catch (e) { /* ignore */ }

    if (!info.isVideoPage && v && v.duration > 3) {
      info.isVideoPage = true;
    }
    return info;
  }

  function report() {
    try {
      chrome.runtime.sendMessage({ type: "pageInfo", info: getVideoInfo() });
    } catch (e) { /* ignore */ }
  }

  // ---------- 视频候选收集 + 高亮 ----------
  const candidates = [];   // {id, el, url, title, thumb}
  const seenUrls = new Set();
  let activeHighlight = null;

  function highlightEl(el, on) {
    if (!el) return;
    if (on) {
      el.style.outline = "3px solid #ff0033";
      el.style.outlineOffset = "2px";
      el.style.boxShadow = "0 0 0 4px rgba(255,0,51,.25)";
      el.style.borderRadius = "4px";
    } else {
      el.style.outline = "";
      el.style.outlineOffset = "";
      el.style.boxShadow = "";
      el.style.borderRadius = "";
    }
  }

  function clearHighlights() {
    if (activeHighlight) highlightEl(activeHighlight, false);
    activeHighlight = null;
  }

  function addCandidate(el, url, title, thumb, referer) {
    if (!url || seenUrls.has(url)) return;
    seenUrls.add(url);
    const id = "c" + (candidates.length + 1);
    const item = { id, el, url, title: (title || "").trim() || url, thumb: thumb || "", referer: referer || "" };
    candidates.push(item);
    if (el) {
      el.setAttribute("data-ytdlp-cand", id);
      el.addEventListener("mouseenter", () => { if (activeHighlight !== el) highlightEl(el, true); });
      el.addEventListener("mouseleave", () => { if (activeHighlight !== el) highlightEl(el, false); });
    }
  }

  // 深度查找 TikTok 页面数据中的视频直链
  function findTikTokPlayAddr(obj) {
    if (!obj || typeof obj !== "object") return null;
    if (typeof obj.playAddr === "string" && obj.playAddr.startsWith("http")) return obj.playAddr;
    if (typeof obj.downloadAddr === "string" && obj.downloadAddr.startsWith("http")) return obj.downloadAddr;
    for (const k of Object.keys(obj)) {
      if (k === "playAddr" || k === "downloadAddr") continue;
      const r = findTikTokPlayAddr(obj[k]);
      if (r) return r;
    }
    return null;
  }

  function collectCandidates() {
    candidates.length = 0;
    seenUrls.clear();
    const host = location.hostname.replace(/^www\./, "");

    // --- YouTube：视频卡片/搜索结果/推荐 ---
    if (host.includes("youtube.com")) {
      const seenCards = new Set();
      document.querySelectorAll(
        'a#video-title, a#thumbnail[href*="/watch?v="], ytd-video-renderer a#thumbnail, ytd-rich-item-renderer a#thumbnail'
      ).forEach((a) => {
        const href = a.href || "";
        if (!href.includes("/watch?v=") || seenCards.has(href)) return;
        seenCards.add(href);
        let title = "";
        const t = a.querySelector("#video-title, .title, .ytd-video-renderer");
        if (t) title = t.textContent.trim();
        if (!title) title = a.getAttribute("title") || a.textContent.trim();
        const img = a.querySelector("img") ? a.querySelector("img").src : "";
        addCandidate(a, href, title, img);
      });
      // 当前正在播放的视频（主播放器）
      const cur = getVideoInfo();
      if (cur.yt) {
        addCandidate(null, location.href, `▶ 正在播放：${cur.yt.title}`, "");
      }
    }

    // --- B站：分P列表 / 视频卡片 ---
    else if (host.includes("bilibili.com")) {
      document.querySelectorAll(
        '.multi-page .list-box .item a, .video-page-card-small a[href*="/video/BV"], a[href*="/video/BV"]'
      ).forEach((a) => {
        const href = a.href || "";
        if (!/\/video\/BV[0-9A-Za-z]+/.test(href)) return;
        let title = "";
        const t = a.querySelector(".title, .name, .bpx-player-video-collect-list-title");
        if (t) title = t.textContent.trim();
        if (!title) title = a.getAttribute("title") || a.textContent.trim();
        if (!title || title.length > 80) title = title.slice(0, 80);
        const img = a.querySelector("img") ? a.querySelector("img").src : "";
        addCandidate(a, href, title, img);
      });
      // 当前播放中视频（分P场景的首P）
      const v = document.querySelector("video");
      if (v && v.duration > 3) {
        addCandidate(null, location.href, `▶ 正在播放：${document.title}`, "");
      }
    }

    // --- TikTok：从已登录页面提取视频直链（绕开反爬） ---
    else if (host.includes("tiktok.com")) {
      const pageReferer = location.href;
      // 1) 播放器直链（视频正在播放时最可靠）
      const v = document.querySelector("video");
      if (v && v.currentSrc && v.currentSrc.startsWith("http")) {
        addCandidate(null, v.currentSrc, `▶ TikTok 视频直链（播放器）`, "", pageReferer);
      }
      // 2) 页面数据 __UNIVERSAL_DATA_FOR_REHYDRATION__ 中的 playAddr
      try {
        const scr = document.getElementById("__UNIVERSAL_DATA_FOR_REHYDRATION__");
        if (scr) {
          const data = JSON.parse(scr.textContent);
          const addr = findTikTokPlayAddr(data);
          if (addr) {
            addCandidate(null, addr, `▶ TikTok 视频（页面数据直链）`, "", pageReferer);
          }
        }
      } catch (e) { /* ignore */ }
      // 3) 帖子页本身（兜底：走 yt-dlp + cookies.txt）
      if (/\/video\/\d+/.test(location.href)) {
        addCandidate(null, location.href, `▶ TikTok 帖子页（${(document.title || "").slice(0, 40)}）`, "");
      }
    }

    // --- 抖音：页面直链（播放器/网络请求）+ 视频页兜底（配合自动同步的 Cookie） ---
    else if (host.includes("douyin.com")) {
      const v = document.querySelector("video");
      if (v && v.currentSrc && !v.currentSrc.startsWith("blob:") && /^https?:/.test(v.currentSrc)) {
        addCandidate(null, v.currentSrc, "▶ 抖音视频直链（播放器）", "", location.href);
      }
      // 网络请求里找 CDN 直链（视频播放过之后一般都能找到）
      try {
        const hit = performance.getEntriesByType("resource").map((e) => e.name)
          .filter((u) => /douyinvod|365yg|\.mp4(\?|$)/.test(u))
          .sort((a, b) => b.length - a.length)[0];
        if (hit && !v) addCandidate(null, hit, "▶ 抖音 CDN 直链（页面请求）", "", location.href);
      } catch (e) { /* ignore */ }
      if (/\/video\/\d+/.test(location.href)) {
        addCandidate(null, location.href,
          `▶ 抖音视频页（${(document.title || "").slice(0, 40)}）`, "");
      } else if (/v\.douyin\.com$/.test(host)) {
        addCandidate(null, location.href, "▶ 抖音分享链接", "");
      }
    }

    // --- 通用兜底：页面里正在播放的 http(s) 直链 ---
    if (!candidates.length) {
      const v = document.querySelector("video");
      const src = v && (v.currentSrc || v.src);
      if (src && /^https?:/.test(src)) {
        addCandidate(null, src, `▶ 页面视频直链（${(document.title || "").slice(0, 30)}）`, "", location.href);
      }
    }

    return candidates.map(({ id, url, title, thumb, referer }) => ({ id, url, title, thumb, referer })).slice(0, 20);
  }

  // ---------- 消息：弹窗请求候选 / 高亮 / 后台通知有下载 ----------
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === "tasksChanged") {
      // 后台发现新的下载任务：开始/继续显示进度
      if (msg.active > 0) startProgressWatch();
      else stopProgressWatch();
      sendResponse({ ok: true });
      return true;
    }
    if (msg.type === "getCandidates") {
      sendResponse({ ok: true, candidates: collectCandidates() });
      return true;
    }
    if (msg.type === "highlightCandidate") {
      const target = candidates.find((c) => c.id === msg.id);
      if (!target || !target.el) {
        sendResponse({ ok: false, error: "找不到该视频位置（页面可能已刷新）" });
        return true;
      }
      clearHighlights();
      highlightEl(target.el, true);
      activeHighlight = target.el;
      try {
        target.el.scrollIntoView({ behavior: "smooth", block: "center" });
      } catch (e) { /* ignore */ }
      sendResponse({ ok: true });
      return true;
    }
    return false;
  });

  // ---------- 悬浮下载按钮（原逻辑） ----------
  let btnEl = null;
  let btnTimer = null;
  let progTimer = null;

  function setBtnText(text, disabled) {
    const btn = btnEl && btnEl.querySelector("button");
    if (!btn) return;
    btn.textContent = text;
    btn.disabled = !!disabled;
    btnEl.dataset.ytdlpState = text.trim();   // 便于排查/测试：把当前状态挂到 DOM 上
  }

  // 在页面上直接显示当前页面的下载进度（数据来自后台轮询本地服务）
  let progMisses = 0;

  function startProgressWatch() {
    if (progTimer) return;
    progMisses = 0;
    const tick = () => {
      try {
        chrome.runtime.sendMessage({ type: "pageProgress", url: location.href }, (res) => {
          if (chrome.runtime.lastError || !res || !res.ok) return;
          const t = res.task;
          if (!btnEl) return;
          if (!t) {
            // 连续几次都没有对应任务就停止轮询，避免在无关页面白跑
            if (++progMisses >= 3) stopProgressWatch();
            return;
          }
          progMisses = 0;
          if (t.status === "downloading" || t.status === "queued") {
            setBtnText(`⏬ ${(t.percent || 0).toFixed(0)}%${t.speed ? ` · ${t.speed}` : ""}`, true);
          } else if (t.status === "done") {
            setBtnText("✓ 下载完成", false);
            stopProgressWatch();
            setTimeout(() => setBtnText("yt-dlp", false), 4000);
          } else if (t.status === "error") {
            setBtnText("✗ 下载失败", false);
            stopProgressWatch();
            setTimeout(() => setBtnText("yt-dlp", false), 4000);
          }
        });
      } catch (e) { /* ignore */ }
    };
    tick();
    progTimer = setInterval(tick, 2000);
  }

  function stopProgressWatch() {
    clearInterval(progTimer);
    progTimer = null;
  }

  function ensureButton() {
    if (btnEl) return;
    btnEl = document.createElement("div");
    btnEl.id = "ytdlp-float-btn";
    btnEl.innerHTML = `
      <button title="用 yt-dlp 下载本页视频"><svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M5 20h14v-2H5v2zM19 9h-4V3H9v6H5l7 7 7-7z"/></svg> yt-dlp</button>`;
    btnEl.addEventListener("click", (e) => {
      e.stopPropagation();
      const url = location.href;
      setBtnText("已加入队列…", true);
      try {
        chrome.runtime.sendMessage({ type: "downloadFromPage", url }, (res) => {
          if (chrome.runtime.lastError || !res || !res.ok) {
            setBtnText("服务未启动！", false);
            setTimeout(() => setBtnText("yt-dlp", false), 2500);
          } else {
            setBtnText("✓ 已加入队列", false);
            startProgressWatch();   // 然后就跟着显示百分比
          }
        });
      } catch (err) {
        setBtnText("失败", false);
        setTimeout(() => setBtnText("yt-dlp", false), 2500);
      }
    });
    document.documentElement.appendChild(btnEl);

    const style = document.createElement("style");
    style.textContent = `
      #ytdlp-float-btn {
        position: fixed; right: 18px; bottom: 96px; z-index: 2147483647;
        font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
        filter: drop-shadow(0 2px 8px rgba(0,0,0,.45));
      }
      #ytdlp-float-btn button {
        display: inline-flex; align-items: center; gap: 6px;
        background: #ff0033; color: #fff; border: none; border-radius: 22px;
        padding: 9px 16px; font-size: 13px; font-weight: 600; cursor: pointer;
        box-shadow: 0 2px 10px rgba(255,0,51,.4); transition: transform .15s;
      }
      #ytdlp-float-btn button:hover { transform: scale(1.05); }
      #ytdlp-float-btn button:disabled { opacity: .6; cursor: default; transform: none; }
    `;
    document.documentElement.appendChild(style);
  }

  function refreshUi() {
    const info = getVideoInfo();
    if (info.isVideoPage) {
      ensureButton();
    } else if (btnEl) {
      btnEl.remove();
      btnEl = null;
    }
    clearTimeout(btnTimer);
    btnTimer = setTimeout(report, 300);
  }

  const obs = new MutationObserver(() => { clearTimeout(btnTimer); btnTimer = setTimeout(refreshUi, 600); });
  obs.observe(document.documentElement, { childList: true, subtree: true });

  document.addEventListener("play", refreshUi, true);
  document.addEventListener("pause", refreshUi, true);
  document.addEventListener("timeupdate", () => {
    clearTimeout(btnTimer);
    btnTimer = setTimeout(report, 1500);
  }, true);

  refreshUi();
  report();

  // 页面打开时如果已有本页/本站点的下载在跑，立刻开始显示进度
  setTimeout(() => {
    try {
      chrome.runtime.sendMessage({ type: "pageProgress", url: location.href }, (res) => {
        if (chrome.runtime.lastError || !res || !res.ok || !res.task) return;
        const st = res.task.status;
        if (st === "downloading" || st === "queued") startProgressWatch();
      });
    } catch (e) { /* ignore */ }
  }, 1500);

  let lastTitle = document.title;
  new MutationObserver(() => {
    if (document.title !== lastTitle) { lastTitle = document.title; refreshUi(); }
  }).observe(document.querySelector("title") || document.documentElement, { childList: true, subtree: true, characterData: true });
})();
