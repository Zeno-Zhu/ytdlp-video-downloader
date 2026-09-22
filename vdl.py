#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vdl.py — 视频下载统一 CLI（AI / 脚本 / 命令行 唯一入口）
=========================================================

设计原则
--------
1. **单一内核**：格式选择、Cookie 匹配、代理、错误分类等全部复用
   `yt-dlp-server/download_server.py`，不复制逻辑（浏览器插件与本 CLI 行为一致）。
2. **零第三方依赖**：只用 Python 标准库；真正的下载由 yt-dlp 子进程完成。
3. **机器可读契约**：`--json` 时 stdout 只输出**一个** JSON 对象，进度与日志一律走 stderr，
   便于 AI 直接 `json.loads(stdout)`。
4. **一行可用**：

       python vdl.py "https://www.youtube.com/watch?v=XXXX" --quality 720

用法速查
--------
    python vdl.py <url> [<url> ...] [选项]

      --quality, -q   best | 720 | 1080 | 480 | mp4 | audio | id:<格式ID>   (默认 720)
      --dir, -d       保存目录（见下方「--dir 解析规则」）
      --info          只解析元数据，不下载（返回标题/时长/可用清晰度清单）
      --json          输出机器可读 JSON（AI 首选）
      --audio         仅提取音频 MP3（等价 --quality audio）
      --subs          额外下载字幕（含自动字幕，转 srt，需 ffmpeg）
      --playlist      允许下载整个播放列表/合集（默认只下一个视频）
      --referer       自定义 Referer（直链下载用）
      --proxy         代理策略（auto/none/system/显式地址），默认 auto：先直连，失败退回系统代理
      --name          自定义文件名模板（默认 "%(title).200B [%(id)s].%(ext)s"）
      --open          下载完成后在资源管理器中选中该文件
      --quiet         静默模式（只在结束时输出结果）

退出码
------
    0 成功    1 下载/解析失败    2 参数错误（含 --dir 不合法）

--dir 解析规则（重要）
---------------------
优先级：`--dir` > `config.json` 的 download_dir > 环境变量 `YTDLP_DOWNLOAD_DIR`
        > `<仓库>/downloads`

`--dir` 的取值按下面顺序解析（**相对路径不相对当前目录**，避免 AI 从别的 cwd 调用时落错地方）：

    1. 先展开 `~` 与环境变量：`~/Videos`、`%USERPROFILE%\\Videos`、`$HOME/Videos`
    2. 展开后是绝对路径          → 直接用            `--dir "D:\\视频"`
    3. 显式 `./` `../` `.` `..`  → 相对**当前目录**    `--dir ./out`
    4. 其余相对路径              → 相对**默认下载目录** `--dir 教程` → `<下载目录>\\教程`

结果一定会在 stderr 日志与 JSON 的 `dir` 字段里回显为**绝对路径**，不会产生歧义。
目录不存在会自动创建（含多级）。

示例
----
    python vdl.py "https://v.douyin.com/xxxx/"                  # 抖音短链，默认 720p
    python vdl.py "https://youtu.be/XXXX" -q 1080 --json        # 指定 1080p，输出 JSON
    python vdl.py "https://youtu.be/XXXX" --info                # 只看信息
    python vdl.py "https://youtu.be/XXXX" --audio               # 提取 MP3
    python vdl.py "https://youtu.be/XXXX" --dir "D:\\视频\\教程"  # 存到指定文件夹
    python vdl.py "https://youtu.be/XXXX" --dir 教程             # 存到 <下载目录>\\教程
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
import time
import uuid

CLI_VERSION = "1.1"

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.environ.get("YTDLP_SERVER_DIR") or os.path.join(HERE, "yt-dlp-server")

if not os.path.isdir(SERVER_DIR):
    sys.stderr.write(f"[vdl] 找不到服务目录: {SERVER_DIR}\n"
                     f"      可用环境变量 YTDLP_SERVER_DIR 指定 yt-dlp-server 所在目录\n")
    sys.exit(2)
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

try:
    import download_server as ds          # noqa: E402  （复用的下载内核）
except Exception as exc:                  # pragma: no cover
    sys.stderr.write(f"[vdl] 无法加载下载内核 download_server.py: {exc}\n")
    sys.exit(2)


# ---------------------------------------------------------------- 输出工具

_QUIET = False


def log(msg: str) -> None:
    """进度/日志一律写 stderr（保证 stdout 的 JSON 干净）"""
    if not _QUIET:
        sys.stderr.write(f"[vdl] {msg}\n")
        sys.stderr.flush()


def emit(obj: dict, as_json: bool) -> None:
    """最终结果：--json 输出单个 JSON 对象，否则输出一行人类可读文本"""
    if as_json:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    else:
        if obj.get("ok"):
            sys.stdout.write(
                f"OK  {obj.get('title') or ''}  {obj.get('size_mb') or '?'}MB\n"
                f"    {obj.get('file_path') or obj.get('dir') or ''}\n")
        else:
            sys.stdout.write(f"FAIL  {obj.get('error') or '未知错误'}"
                             + (f"  ({obj.get('hint')})" if obj.get("hint") else "") + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------- 质量归一化

# 用户/AI 可能写出的各种写法 → download_server.resolve_format 认识的标识
_QUALITY_ALIASES = {
    "best": "best", "max": "best", "highest": "best", "origin": "best", "raw": "best",
    "mp4": "mp4", "default": "720mp4",
    "audio": "audio", "mp3": "audio", "music": "audio", "sound": "audio",
    "720p": "720mp4", "1080p": "1080mp4", "480p": "480mp4", "360p": "360mp4",
    "2160p": "2160mp4", "1440p": "1440mp4", "4k": "2160mp4", "2k": "1440mp4",
}

DEFAULT_QUALITY = "720mp4"

# yt-dlp 下载完成后回吐元数据那一行的前缀（见 base_cmd 里的 --print after_move:...）
META_MARKER = "__VDLMETA__"


def normalize_quality(q: str | None) -> str | None:
    """把各种清晰度写法统一成 resolve_format 能识别的形式；非法返回 None"""
    q = (q or "").strip().lower().replace(" ", "")
    if not q:
        return DEFAULT_QUALITY
    if q in _QUALITY_ALIASES:
        return _QUALITY_ALIASES[q]
    if q.startswith("id:"):
        fid = q[3:].strip()
        return "id:" + fid if re.fullmatch(r"[A-Za-z0-9_.\-]+", fid) else None
    m = re.fullmatch(r"(\d{3,4})", q)                     # 720
    if m:
        return m.group(1) + "mp4"
    m = re.fullmatch(r"(\d{3,4})p", q)                    # 720p
    if m:
        return m.group(1) + "mp4"
    m = re.fullmatch(r"(\d{3,4})(mp4|webm|mkv)", q)       # 720mp4 / 720webm
    if m:
        return m.group(1) + "mp4" if m.group(2) == "mp4" else m.group(1)
    return None


def quality_label(q: str) -> str:
    """给人类看的清晰度标签"""
    if q in ("best", "mp4", "audio"):
        return {"best": "最佳画质", "mp4": "MP4 最佳", "audio": "音频 MP3"}[q]
    if q.startswith("id:"):
        return "格式 " + q[3:]
    m = re.match(r"^(\d+)mp4$", q)
    if m:
        return f"{m.group(1)}p MP4"
    if q.isdigit():
        return f"≤{q}p"
    return q


# ---------------------------------------------------------------- 目录 / 模板

DirError = ds.DirError          # 目录错误类型也由内核定义，保证各入口判定一致


def resolve_dir(cli_dir: str | None) -> str:
    """解析下载目录并确保可用（抛 ds.DirError）。

    唯一实现在内核 `download_server.resolve_download_dir()`，这里只做转发——
    CLI / MCP / HTTP 三处入口共用同一套规则，不要在别处再写一份。
    `--dir` 的解析规则见模块 docstring「--dir 解析规则」。
    """
    return ds.resolve_download_dir(cli_dir)


def find_produced_file(dl_dir: str, since: float, prefer: list[str] | None = None) -> str | None:
    """兜底：下载结束后在目录里找本次新产生、且没在下载前存在的媒体文件"""
    exts = (".mp4", ".mkv", ".webm", ".mov", ".flv", ".m4a", ".mp3", ".opus", ".aac", ".wav")
    best, best_mtime = None, 0.0
    try:
        for name in os.listdir(dl_dir):
            if name.endswith((".part", ".ytdl", ".temp")):
                continue
            if prefer and name in prefer:
                return os.path.join(dl_dir, name)
            if not name.lower().endswith(exts):
                continue
            p = os.path.join(dl_dir, name)
            try:
                mtime = os.path.getmtime(p)
            except OSError:
                continue
            if mtime >= since - 1 and mtime > best_mtime:
                best, best_mtime = p, mtime
    except OSError:
        pass
    return best


# ---------------------------------------------------------------- 元数据解析

def cmd_preview(url: str) -> int:
    """--info：解析元数据（复用服务端 run_preview，带 10 分钟缓存）"""
    try:
        info = ds.run_preview(ds.extract_url(url), ARGS.proxy)
    except ds.PreviewError as exc:
        emit({"ok": False, "url": url, "error": str(exc), "error_code": exc.code,
              "hint": exc.hint}, ARGS.json)
        return 1
    except Exception as exc:
        emit({"ok": False, "url": url, "error": str(exc), "error_code": "unknown"}, ARGS.json)
        return 1

    out = {
        "ok": True,
        "url": info.get("webpage_url") or url,
        "title": info.get("title"),
        "author": info.get("author"),
        "duration": info.get("duration"),
        "duration_sec": info.get("duration_sec"),
        "extractor": info.get("extractor"),
        "view_count": info.get("view_count"),
        "thumbnail": info.get("thumbnail"),
        "cookies_used": info.get("cookies_used"),
        "formats": info.get("formats") or [],
        "quality_options": sorted({f["height"] for f in (info.get("formats") or []) if f.get("height")},
                                  reverse=True),
    }
    emit(out, ARGS.json)

    if not ARGS.json:
        log(f"标题 : {out['title']}")
        log(f"作者 : {out['author']}    时长: {out['duration']}    来源: {out['extractor']}")
        log(f"清晰度: {', '.join(str(h) + 'p' for h in out['quality_options']) or '未知'}")
        for f in out["formats"][:12]:
            wm = " [含水印]" if f.get("watermarked") else ""
            sz = f"{f['size']}MB" if f.get("size") else "?"
            log(f"        {f['height']}p · {f['vcodec']} · {f['ext']} · {sz}{wm}  id={f['format_id']}")
    return 0


# ---------------------------------------------------------------- 实际下载

def _run_ytdlp(cmd: list, dl_dir: str, before: set, started: float, used_proxy):
    """执行一次 yt-dlp 下载，实时解析进度；返回 (returncode, final_file, err_msg, meta)"""
    tmp_files: list[str] = []
    cmd = list(cmd) + ds.extra_args_for(cmd[1], None, tmp_files)

    tty = sys.stderr.isatty()
    last_pct = -1.0
    final_file = None
    err_msg = None
    meta: dict = {}

    pct_re = re.compile(r"(\d+(?:\.\d+)?)%")
    speed_re = re.compile(r"at\s+([0-9.]+[KMGT]?i?B/s)")
    eta_re = re.compile(r"ETA\s+([0-9:]+)")
    dest_re = re.compile(r"\[download\] Destination: (.+)$")
    merge_re = re.compile(r'(?:Merger|VideoMerge).*?"(.+)"', re.IGNORECASE)
    exists_re = re.compile(r"\[download\] (.+) has already been downloaded", re.IGNORECASE)
    error_re = re.compile(r"ERROR[:\s]\s*(.+)", re.IGNORECASE)
    meta_re = re.compile(re.escape(META_MARKER) + r"\|(.*?)\|(.*?)\|(.*?)\|(.*?)\|(.*)$")
    # yt-dlp 有时只回一行 "ERROR:" 而正文在相邻行，或把原因写成 WARNING。
    # 这里留一个最近非进度行的环形缓冲，作为错误归因的兜底素材。
    tail: collections.deque = collections.deque(maxlen=10)

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=ds.subprocess_env(used_proxy))     # 强制 UTF-8 + 按策略决定代理
    except Exception as exc:
        ds.cleanup(tmp_files)
        return -1, None, f"启动 yt-dlp 失败: {exc}", {}

    for line in proc.stdout:
        line = line.rstrip("\r\n")
        if not line:
            continue

        m = merge_re.search(line)
        if m:
            final_file = m.group(1).strip()
        m = dest_re.search(line)
        if m:
            final_file = m.group(1).strip()
        m = exists_re.search(line)
        if m:
            final_file = m.group(1).strip()

        # 精确元数据行（下载完成后 yt-dlp 回吐）
        m = meta_re.search(line)
        if m:
            raw = [x.strip() for x in m.groups()]
            if raw[0] and raw[0] != "NA":
                meta["title"] = raw[0]
            if raw[1] and raw[1].isdigit():
                meta["duration_sec"] = int(raw[1])
            if raw[2] and raw[2] != "NA":
                meta["extractor"] = raw[2]
            if raw[3] and raw[3] != "NA":
                meta["video_id"] = raw[3]
            if raw[4] and raw[4] != "NA":
                meta["resolution"] = raw[4]

        if "[download]" in line:
            pct_m = pct_re.search(line)
            if pct_m and not _QUIET:
                pct = float(pct_m.group(1))
                if tty:
                    sp = speed_re.search(line)
                    et = eta_re.search(line)
                    sys.stderr.write(
                        f"\r[vdl] {pct:5.1f}%"
                        + (f"  {sp.group(1)}" if sp else "")
                        + (f"  ETA {et.group(1)}" if et else "") + "      ")
                    sys.stderr.flush()
                elif pct - last_pct >= 20:
                    last_pct = pct
                    log(f"进度 {pct:.0f}%")
        if "Merging formats" in line or "VideoMerge" in line:
            if tty and not _QUIET:
                sys.stderr.write("\r[vdl] 合并音视频…                                  \n")
                sys.stderr.flush()

        m = error_re.search(line)
        if m:
            text = m.group(1).strip()[:500]
            if text:
                err_msg = text
                if not _QUIET:
                    log(f"yt-dlp: {text}")

        # 收集非进度行，供失败时归因
        if "[download]" not in line and not line.startswith(META_MARKER):
            tail.append(line.strip()[:300])

    proc.wait()
    ds.cleanup(tmp_files)
    if tty and not _QUIET:
        sys.stderr.write("\r" + " " * 60 + "\r")

    if proc.returncode != 0 and not err_msg:
        # 归因兜底：yt-dlp 可能只打一行空的 "ERROR:"，真正的原因在相邻行
        cand = [l for l in tail
                if re.search(r"error|warning|failed|unable|refused|denied|throttl|reset|"
                             r"unavailable|not available", l, re.I)]
        err_msg = (cand[-1] if cand else (tail[-1] if tail else "")) or None
        if err_msg and not _QUIET:
            log(f"归因: {err_msg}")

    if proc.returncode == 0 and (not final_file or not os.path.exists(final_file)):
        new_names = [n for n in os.listdir(dl_dir) if n not in before]
        final_file = find_produced_file(dl_dir, started, new_names) or final_file
    return proc.returncode, final_file, err_msg, meta


def cmd_download_one(url: str) -> dict:
    """下载单个视频，返回结果 dict（约定：ok / file_path / error ...）"""
    url = ds.extract_url(url)
    started = time.time()
    result = {
        "ok": False, "url": url, "title": None, "quality": ARGS.quality,
        "quality_label": quality_label(ARGS.quality),
        "dir": None, "file_path": None, "filename": None, "size_mb": None,
        "extractor": None, "duration_sec": None, "resolution": None,
        "cookies_used": None, "proxy_used": None, "attempts": 0, "elapsed_sec": None,
        "error": None, "error_code": None, "hint": None,
    }

    ytdlp = ds.ensure_ytdlp()
    if not ytdlp:
        result.update(error="未找到 yt-dlp，请先执行: pip install -U yt-dlp",
                      error_code="no_ytdlp", hint="或在本仓库目录运行 python setup.py",
                      elapsed_sec=round(time.time() - started, 2))
        return result

    try:
        dl_dir = resolve_dir(ARGS.dir)     # 内部已 makedirs
    except DirError as exc:
        result.update(error=str(exc), error_code="bad_dir",
                      hint="换一个可写目录，例如 --dir \"D:/视频\"；相对路径相对默认下载目录，"
                           "要相对当前目录请写 ./xxx",
                      elapsed_sec=round(time.time() - started, 2))
        return result
    result["dir"] = dl_dir

    template = ARGS.name or "%(title).200B [%(id)s].%(ext)s"
    out_tpl = template if os.path.isabs(template) else os.path.join(dl_dir, template)

    base_cmd = [*ytdlp, url, "-o", out_tpl, "--newline", "--progress", "--no-warnings",
                "--retries", "10", "--fragment-retries", "10", "--file-access-retries", "3",
                # 让 yt-dlp 在下载完成后回吐一行精确元数据（--no-simulate 必须带，
                # 否则 --print 会变成"只打印不下载"；--progress 保证进度条不被静默）
                "--no-simulate",
                "--print", (f"after_move:{META_MARKER}"
                            "|%(title)s|%(duration)s|%(extractor)s|%(id)s|%(resolution)s"),
                *ds.resolve_format(ARGS.quality)]
    if not ARGS.playlist:
        base_cmd.append("--no-playlist")
    if ARGS.subs:
        base_cmd += ["--write-subs", "--write-auto-subs", "--sub-langs", "all,-live_chat",
                     "--convert-subs", "srt"]
    if ARGS.referer:
        base_cmd += ["--add-header", f"Referer: {ARGS.referer}"]

    before = set(os.listdir(dl_dir)) if os.path.isdir(dl_dir) else set()
    result["cookies_used"] = os.path.basename(ds.cookie_file_for(url) or "") or None

    cands = ds.proxy_candidates(url, ARGS.proxy)     # auto → [直连, 系统代理]
    log(f"目标: {quality_label(ARGS.quality)} → {dl_dir}")

    for idx, used_proxy in enumerate(cands):
        result["attempts"] = idx + 1
        result["proxy_used"] = used_proxy
        log(f"尝试 {idx + 1}/{len(cands)}｜代理: {used_proxy or '直连'}")

        rc, final_file, err_msg, meta = _run_ytdlp(base_cmd, dl_dir, before, started, used_proxy)

        if meta.get("title"):
            result["title"] = meta["title"]
        if meta.get("extractor"):
            result["extractor"] = meta["extractor"]
        if meta.get("duration_sec"):
            result["duration_sec"] = meta["duration_sec"]
        if meta.get("resolution"):
            result["resolution"] = meta["resolution"]

        if rc == 0:
            result["ok"] = True
            # 关键：降级重试成功时必须清掉上一次尝试留下的失败信息，
            # 否则 ok=true 却带着 error/error_code（消费方按 error 判断会误判为失败）
            result["error"] = None
            result["error_code"] = None
            result["hint"] = None
            if final_file:
                result["file_path"] = final_file
                result["filename"] = os.path.basename(final_file)
                if not result["title"]:
                    # 从文件名兜底：去掉 yt-dlp 加的 " [视频ID]" 后缀
                    stem = os.path.splitext(result["filename"])[0]
                    result["title"] = re.sub(r"\s*\[[^\[\]]{4,}\]$", "", stem).strip()
                try:
                    result["size_mb"] = round(os.path.getsize(final_file) / 1048576, 1)
                except OSError:
                    pass
            result["elapsed_sec"] = round(time.time() - started, 2)
            return result

        result["error"] = err_msg or f"yt-dlp 退出码 {rc}"
        code, hint = ds.classify_error(result["error"])
        result["error_code"] = code
        result["hint"] = hint or None

        # 网络/超时类失败 → 换下一种代理策略再试（多为代理与直连选错）
        if code in ds.RETRYABLE_ERRORS and idx + 1 < len(cands):
            nxt = cands[idx + 1]
            log(f"失败({code}) → 改用{'直连' if nxt is None else nxt}重试")
            continue
        break

    result["elapsed_sec"] = round(time.time() - started, 2)
    return result


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vdl.py",
        description="视频下载统一 CLI（默认 720p MP4，优先无水印，自动回退低清晰度）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("示例\n----")[-1].strip() if "示例" in __doc__ else "",
    )
    p.add_argument("urls", nargs="+", metavar="URL",
                   help="视频链接（可多个；支持抖音/YouTube/B站/TikTok/X 等上千站点，也支持粘贴分享文案）")
    p.add_argument("-q", "--quality", default=None,
                   help="清晰度：best | 720 | 1080 | 480 | mp4 | audio | id:<格式ID>（默认 720）")
    p.add_argument("-d", "--dir", default=None, metavar="PATH",
                   help="保存目录。绝对路径直接用；相对路径相对【默认下载目录】（--dir 教程 → "
                        "<默认目录>/教程）；显式 ./ 或 ../ 相对当前目录；支持 ~ 与 %VAR%/$VAR。"
                        "不存在会自动创建")
    p.add_argument("--info", action="store_true", help="只解析元数据，不下载")
    p.add_argument("--json", action="store_true", help="输出机器可读 JSON（AI 首选）")
    p.add_argument("--audio", action="store_true", help="仅提取音频 MP3")
    p.add_argument("--subs", action="store_true", help="额外下载字幕（转 srt）")
    p.add_argument("--playlist", action="store_true", help="允许下载整个播放列表（默认仅单个视频）")
    p.add_argument("--referer", default=None, help="自定义 Referer 头")
    p.add_argument("--proxy", default=None,
                   help="代理策略：auto(默认，先直连，网络类失败自动退回系统代理) | none(只直连) | "
                        "system(只用系统代理) | http://127.0.0.1:7897 指定代理")
    p.add_argument("--name", default=None, help="文件名模板（默认 \"%%(title).200B [%%(id)s].%%(ext)s\"）")
    p.add_argument("--open", dest="open_after", action="store_true",
                   help="下载完成后在资源管理器中选中文件")
    p.add_argument("--quiet", action="store_true", help="静默模式")
    p.add_argument("--version", action="version", version=f"vdl {CLI_VERSION} (yt-dlp 内核 {ds.VERSION})")
    return p


def main() -> int:
    global ARGS, _QUIET
    ARGS = build_parser().parse_args()
    _QUIET = ARGS.quiet

    if ARGS.audio:
        ARGS.quality = "audio"
    q = normalize_quality(ARGS.quality)
    if q is None:
        sys.stderr.write(f"[vdl] 无法识别的清晰度: {ARGS.quality}\n"
                         f"      可用: best | 720 | 1080 | 480 | mp4 | audio | id:<格式ID>\n")
        return 2
    ARGS.quality = q

    if ARGS.info:
        rc = 1
        if ARGS.json and len(ARGS.urls) > 1:
            items = []
            for u in ARGS.urls:
                got = _collect_info(u)
                items.append(got)
            sys.stdout.write(json.dumps({"ok": all(i.get("ok") for i in items),
                                         "items": items}, ensure_ascii=False) + "\n")
            return 0 if all(i.get("ok") for i in items) else 1
        for u in ARGS.urls:
            rc = cmd_preview(u)
        return rc

    results = []
    for u in ARGS.urls:
        results.append(cmd_download_one(u))

    ok_all = all(r["ok"] for r in results)
    if ARGS.json:
        payload = results[0] if len(results) == 1 else {"ok": ok_all, "items": results}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    else:
        for r in results:
            emit(r, False)
            if r["ok"] and r.get("file_path"):
                log(f"完成: {r['file_path']}")
            elif not r["ok"]:
                log(f"失败: {r['error']}" + (f"  → {r['hint']}" if r.get("hint") else ""))

    if ARGS.open_after and ok_all:
        for r in results:
            if r.get("file_path"):
                open_in_explorer(r["file_path"])

    # --dir 不合法属于参数错误（退出码 2），要能和"下载失败"（1）区分开
    if any(r.get("error_code") == "bad_dir" for r in results):
        return 2
    return 0 if ok_all else 1


def _collect_info(url: str) -> dict:
    try:
        info = ds.run_preview(ds.extract_url(url), ARGS.proxy)
        return {"ok": True, "url": info.get("webpage_url") or url, "title": info.get("title"),
                "author": info.get("author"), "duration": info.get("duration"),
                "extractor": info.get("extractor"), "formats": info.get("formats") or [],
                "cookies_used": info.get("cookies_used")}
    except ds.PreviewError as exc:
        return {"ok": False, "url": url, "error": str(exc), "error_code": exc.code, "hint": exc.hint}
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc), "error_code": "unknown"}


def open_in_explorer(path: str) -> None:
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select," + os.path.abspath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception as exc:
        log(f"打开文件夹失败: {exc}")


ARGS = None  # 由 main() 填充

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("\n[vdl] 已中断\n")
        sys.exit(130)
