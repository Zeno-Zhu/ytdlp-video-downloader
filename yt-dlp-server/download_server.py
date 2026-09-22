# -*- coding: utf-8 -*-
"""
yt-dlp 本地下载服务 v2.6
=========================
供 Chrome 插件 / vdl.py CLI / MCP 调用的本地 HTTP 服务（仅监听 127.0.0.1，纯标准库）：

  GET  /health                 -> 健康检查（yt-dlp 版本、下载目录、已保存的 Cookie 站点、代理模式）
  POST /api/preview            -> 解析视频元数据  {url, proxy?}
  POST /api/download           -> 加入下载队列  {url, format, dir?, referer?, proxy?}
                                   format: best | mp4 | 数字分辨率(如 2160/1080/720) | audio | id:<格式ID>
  GET  /api/tasks              -> 任务列表（含历史，重启不丢）
  GET  /api/tasks/<id>         -> 单个任务状态（含 percent/speed/eta/size）
  POST /api/retry              -> 重试失败任务 {task_id}
  GET  /api/cookies            -> 已保存的 Cookie 站点列表
  POST /api/cookies            -> 保存浏览器同步来的 Cookie {host|url, content}
  POST /api/delete-cookies     -> 删除某个站点的 Cookie {host}
  GET  /api/list-dir?path=     -> 浏览本机目录（path 为空列出盘符）
  POST /api/set-default-dir    -> 设置默认下载目录 {dir}（持久化到 config.json）
  POST /api/open-folder        -> 打开下载文件夹（Windows）
  POST /api/clear-cache        -> 清空解析缓存

下载目录优先级：config.json 中用户设置 > 环境变量 YTDLP_DOWNLOAD_DIR > 默认 <仓库>/downloads
Cookie 优先级：cookies/<站点>.txt（插件自动同步） > cookies.txt（手工放置的全局文件）
代理优先级：请求体 proxy 参数 > config.json 的 "proxy" > auto（默认按站点探测直连，能直连就不套代理）
"""
import json
import os
import queue
import re
import shutil
import string
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

VERSION = "2.6"

# 控制台/管道编码兜底：日志里的中文在非 UTF-8 代码页下不应导致异常
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _app_dir():
    """程序所在目录：源码运行时为脚本目录，PyInstaller 打包后为 exe 所在目录"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _app_dir()
DEFAULT_DOWNLOAD_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "downloads"))
PORT = int(os.environ.get("YTDLP_SERVER_PORT", "8787"))
MAX_CONCURRENT = 2
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
LOG_FILE = os.path.join(BASE_DIR, "server.log")
HISTORY_MAX = 100
PREVIEW_CACHE_TTL = 600          # 秒
COOKIES_DIR = os.path.join(BASE_DIR, "cookies")     # 插件同步的站点 Cookie（每站点一个文件）
LEGACY_COOKIES_FILE = os.path.join(BASE_DIR, "cookies.txt")  # 手工放置的全局 Cookie（可选）

# ---------------- 日志 ----------------

_LOG_LOCK = threading.Lock()


def log(msg):
    """写日志：控制台(stderr) + server.log（打包成无窗口 exe 后只剩文件日志）"""
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    try:
        with _LOG_LOCK:
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1024 * 1024:
                os.replace(LOG_FILE, LOG_FILE + ".1")
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


# ---------------- 定位 yt-dlp / python ----------------

def _python_install_dirs():
    """收集本机 Python 安装目录（注册表 + 常见路径 + 通配目录）"""
    dirs = []
    try:
        import winreg
        roots = [
            (winreg.HKEY_CURRENT_USER, r"Software\Python\PythonCore"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Python\PythonCore"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Python\PythonCore"),
        ]
        for hive, key_path in roots:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    i = 0
                    while True:
                        try:
                            ver = winreg.EnumKey(key, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            with winreg.OpenKey(hive, key_path + "\\" + ver + r"\InstallPath") as ik:
                                p = winreg.QueryValueEx(ik, "")[0]
                                if p:
                                    dirs.append(p)
                        except OSError:
                            pass
            except OSError:
                pass
    except Exception:
        pass

    local = os.environ.get("LOCALAPPDATA") or ""
    roaming = os.environ.get("APPDATA") or ""
    for pattern_root, pattern in (
        (os.path.join(local, "Programs", "Python"), "Python3*"),
        (os.path.join(local, "Python"), "pythoncore-*"),
        (roaming, "Python"),
        ("C:\\", "Python3*"),
    ):
        dirs.append(os.path.join(pattern_root, pattern))
        try:
            import glob
            dirs.extend(glob.glob(os.path.join(pattern_root, pattern)))
        except Exception:
            pass
    out, seen = [], set()
    for d in dirs:
        if not d or "*" in d:
            continue
        d = os.path.abspath(d)
        if d.lower() not in seen and os.path.isdir(d):
            seen.add(d.lower())
            out.append(d)
    return out


def _try_run(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=subprocess_env())
        return p.returncode == 0, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as exc:
        return False, "", str(exc)


def find_ytdlp():
    """返回可执行 yt-dlp 的命令列表（优先独立 exe，最后退回 python -m yt_dlp）"""
    cands = []
    for name in ("yt-dlp", "yt-dlp.exe"):
        p = shutil.which(name)
        if p:
            cands.append([p])
    for d in _python_install_dirs():
        for sub in ("Scripts", ""):
            for name in ("yt-dlp.exe", "yt-dlp"):
                p = os.path.join(d, sub, name) if sub else os.path.join(d, name)
                if os.path.isfile(p):
                    cands.append([p])

    seen = set()
    for cmd in cands:
        key = os.path.abspath(cmd[0]).lower()
        if key in seen:
            continue
        seen.add(key)
        ok, out, _ = _try_run(cmd + ["--version"], timeout=25)
        if ok and re.match(r"^\d{4}\.\d{2}\.\d{2}", out or ""):
            return cmd

    for py in find_python():
        ok, out, _ = _try_run([py, "-m", "yt_dlp", "--version"], timeout=25)
        if ok:
            return [py, "-m", "yt_dlp"]
    return None


YTDLP_BIN = None   # 惰性探测，见 ensure_ytdlp()


def ensure_ytdlp():
    """定位 yt-dlp（惰性执行，避免 import 阶段做耗时的进程探测）"""
    global YTDLP_BIN
    if not YTDLP_BIN:
        YTDLP_BIN = find_ytdlp()
        if YTDLP_BIN:
            log("yt-dlp: " + " ".join(YTDLP_BIN))
        else:
            log("未找到 yt-dlp（请先执行 pip install -U yt-dlp）")
    return YTDLP_BIN


def find_python(console=False):
    """找一个可用的 python 解释器（供 native_host 启动本服务使用）"""
    name = "python.exe" if console else "pythonw.exe"
    fallback_name = "python.exe" if not console else "pythonw.exe"
    cands = []
    exe = sys.executable or ""
    if exe and not getattr(sys, "frozen", False) and os.path.basename(exe).lower().startswith("python"):
        cands.append(exe)
    for n in (name, fallback_name):
        p = shutil.which(n)
        if p:
            cands.append(p)
    for d in _python_install_dirs():
        for n in (name, fallback_name):
            p = os.path.join(d, n)
            if os.path.isfile(p):
                cands.append(p)
    # Windows 商店别名放最后（可能弹商店，仅在没别的时候用）
    local = os.environ.get("LOCALAPPDATA") or ""
    for n in (name, fallback_name):
        p = os.path.join(local, "Microsoft", "WindowsApps", n)
        if os.path.isfile(p):
            cands.append(p)

    seen = set()
    ordered = []
    for p in cands:
        key = os.path.abspath(p).lower()
        if key in seen or not os.path.isfile(p):
            continue
        seen.add(key)
        ordered.append(p)
    # 商店别名排到最后
    ordered.sort(key=lambda p: ("windowsapps" in p.lower(), ))
    for p in ordered:
        ok, _, _ = _try_run([p, "-c", "print(1)"], timeout=25)
        if ok:
            return p
    return None


def get_system_proxy():
    """读取 Windows 系统代理设置（如 Clash 127.0.0.1:7897），用于 yt-dlp 子进程"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        if enable and server:
            if not server.startswith(("http://", "https://", "socks")):
                server = "http://" + server
            return server
    except Exception:
        pass
    return None


SYSTEM_PROXY = get_system_proxy() or (os.environ.get("http_proxy") if sys.platform != "win32" else None)
# 说明：Windows 下只认注册表里的系统代理。环境变量 http_proxy 常常被 IDE / 沙箱注入成
# 临时端口，把它当成用户代理会误伤（本次实测就踩到过）。

# ---------------------------------------------------------------- 代理决策
# 背景（本机真实踩坑，值得记一笔）：
#   1) Clash/ClashX 常以 **TUN 模式**运行，分流由它自己完成。此时再把系统代理塞给 yt-dlp
#      属于「二次代理」，本可直连的站点反而可能 SSL 中断（实测 youtube.com / x.com 都中过）。
#   2) 反过来，tiktok.com 这类站点在 TUN 规则没覆盖时必须走系统代理。
#   3) 本机 DNS 存在污染（www.youtube.com 会被解析到 31.13.92.37 这种 Facebook IP），
#      所以「预先探测直连」并不可靠——同一站点会时通时断，探测会给出假阴性。
# 结论：不要预探测，改成**按顺序尝试 + 失败自动降级**，用真实结果说话。
#
#   config.json  "proxy": "auto"   (默认) 先直连，网络类失败再退回系统代理
#                          "none"   只用直连（TUN 模式省事，推荐）
#                          "system" 只用系统代理
#                          "http://127.0.0.1:7897" / "socks5://..."  只用指定代理
PROXY_MODE = "auto"
_UNSET = object()
# 归到这几类错误说明「可能是代理/链路问题」，值得换一种代理策略再试一次
RETRYABLE_ERRORS = ("network", "timeout")


def proxy_candidates(url, mode=None):
    """
    返回按优先级尝试的代理列表（元素为代理 URL 或 None=直连）。
    auto 会返回 [None, SYSTEM_PROXY]，调用方在失败时依次降级重试。
    """
    mode = (mode or PROXY_MODE or "auto").strip()
    low = mode.lower()
    if low in ("none", "off", "no", "direct", "false", "0"):
        return [None]
    if low in ("system", "on", "true", "1", "proxy"):
        return [SYSTEM_PROXY] if SYSTEM_PROXY else [None]
    if "://" in mode:                      # 显式指定代理地址
        return [mode]
    # auto：直连优先（TUN 模式下直连本身已按规则分流），网络失败再退回系统代理
    if SYSTEM_PROXY:
        return [None, SYSTEM_PROXY]
    return [None]


def proxy_for_url(url, mode=None):
    """本次请求首选的代理（取候选列表第一项）；None 表示直连"""
    return proxy_candidates(url, mode)[0]


def subprocess_env(proxy=_UNSET):
    """
    子进程环境：
      * 强制 UTF-8（PYTHONUTF8 + PYTHONIOENCODING）—— 否则 Windows 下 yt-dlp 会用 GBK 输出，
        标题里的中文会变成乱码（历史记录/通知里显示成 “���ٷ�”）
      * 代理：由 proxy_for_url() 决策。传 None 表示直连——此时必须把继承来的
        http_proxy/https_proxy/ALL_PROXY **清掉**，否则 yt-dlp 仍会走代理。
    """
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    p = SYSTEM_PROXY if proxy is _UNSET else proxy
    if p:
        env["http_proxy"] = env["https_proxy"] = p
        env["HTTP_PROXY"] = env["HTTPS_PROXY"] = p
    else:
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
            env.pop(k, None)
    return env


TASKS = {}            # id -> task
TASKS_LOCK = threading.Lock()
JOB_QUEUE = queue.Queue()
ACTIVE_JOBS = 0
ACTIVE_LOCK = threading.Lock()
PREVIEW_CACHE = {}    # url -> (time, result)
PREVIEW_LOCK = threading.Lock()
CONFIG_LOCK = threading.Lock()

DOWNLOAD_DIR = os.environ.get("YTDLP_DOWNLOAD_DIR") or DEFAULT_DOWNLOAD_DIR
COOKIES_FROM_BROWSER = None   # config.json 可选：让 yt-dlp 直接读取浏览器 Cookie（firefox/chrome...）
UPDATE_YTDLP = False          # config.json 可选：下载前自动更新 yt-dlp

# 抖音等站点会额外返回带水印的 download_addr 格式，默认链路里排除
NO_WATERMARK = "[format_id!^=download_addr]"

# 通用视频选择器：排除水印版本；不写死分辨率，分辨率交给 -S 排序决定（见 resolve_format）
SAFE_VIDEO = "bv*" + NO_WATERMARK + "+ba/b"

# 「优先直连下载」排序键。
# 背景：分片流（m3u8 / dash）会先落一地 -FragN 临时文件再合并，结束时逐个删除；
# 在带「批量删除保护」的环境里（例如由 AI 助手代跑命令），这一步可能被拦住，
# 结果文件明明下完了却以失败收场。proto:https 让 yt-dlp 优先挑直连 https 的格式，
# 实测不影响画质（只在同档位之间取舍），但能显著减少临时文件。
PREFER_DIRECT = "proto:https"

FORMATS = {
    "best": ["-f", SAFE_VIDEO, "-S", PREFER_DIRECT],
    "mp4": ["-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b", "-S", PREFER_DIRECT],
    "audio": ["-x", "--audio-format", "mp3", "--audio-quality", "0"],
}


# ---------------- 配置 ----------------

def load_config():
    global DOWNLOAD_DIR, COOKIES_FROM_BROWSER, UPDATE_YTDLP, PROXY_MODE
    if not os.path.exists(CONFIG_FILE):
        return
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        d = cfg.get("download_dir")
        if d and isinstance(d, str) and os.path.isabs(d):
            d = os.path.normpath(d)      # 修正旧配置里重复的反斜杠（E:\\a\\b -> E:\a\b）
            os.makedirs(d, exist_ok=True)
            DOWNLOAD_DIR = d
        cb = cfg.get("cookies_from_browser")
        if cb and isinstance(cb, str):
            COOKIES_FROM_BROWSER = cb.strip()
        UPDATE_YTDLP = bool(cfg.get("update_ytdlp"))
        pm = cfg.get("proxy")
        if pm and isinstance(pm, str):
            PROXY_MODE = pm.strip()
    except Exception as exc:
        log(f"config.json 加载失败: {exc}")


def save_config():
    """持久化配置（下载目录等）"""
    with CONFIG_LOCK:
        cfg = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f) or {}
            except Exception:
                cfg = {}
        cfg["download_dir"] = DOWNLOAD_DIR
        if COOKIES_FROM_BROWSER:
            cfg["cookies_from_browser"] = COOKIES_FROM_BROWSER
        if UPDATE_YTDLP:
            cfg["update_ytdlp"] = True
        if PROXY_MODE and PROXY_MODE != "auto":
            cfg["proxy"] = PROXY_MODE
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CONFIG_FILE)


# ---------------- Cookie 管理 ----------------

def url_host(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _valid_cookie_lines(content):
    """统计 Netscape 格式 cookie 行数（含 #HttpOnly_ 前缀）"""
    n = 0
    for line in (content or "").splitlines():
        line = line.rstrip("\r")
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        if len(line.split("\t")) >= 7:
            n += 1
    return n


def list_cookie_sites():
    out = []
    if os.path.isdir(COOKIES_DIR):
        for name in sorted(os.listdir(COOKIES_DIR)):
            if not name.endswith(".txt"):
                continue
            path = os.path.join(COOKIES_DIR, name)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    count = _valid_cookie_lines(f.read())
                out.append({"host": name[:-4], "count": count,
                            "updated": time.strftime("%Y-%m-%d %H:%M:%S",
                                                     time.localtime(os.path.getmtime(path)))})
            except Exception:
                continue
    return out


def save_cookies(host, content):
    """保存插件同步来的 Cookie（原子写入 cookies/<host>.txt）"""
    host = (host or "").strip().lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.\-]{1,100}", host):
        raise ValueError("非法的站点域名")
    count = _valid_cookie_lines(content)
    if count == 0:
        raise ValueError("没有解析到有效的 Cookie 内容")
    os.makedirs(COOKIES_DIR, exist_ok=True)
    path = os.path.join(COOKIES_DIR, host + ".txt")
    body = content if content.endswith("\n") else content + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    os.replace(tmp, path)
    log(f"已保存 {count} 条 Cookie -> {path}")
    return path, count


def cookie_file_for(url):
    """按域名后缀匹配该站点最合适的 Cookie 文件"""
    host = url_host(url)
    if not host:
        return None
    best = None
    if os.path.isdir(COOKIES_DIR):
        for name in os.listdir(COOKIES_DIR):
            if not name.endswith(".txt"):
                continue
            dom = name[:-4].lower()
            if host == dom or host.endswith("." + dom):
                if best is None or len(dom) > len(best[0]):
                    best = (dom, os.path.join(COOKIES_DIR, name))
    if best:
        return best[1]
    if os.path.isfile(LEGACY_COOKIES_FILE):
        return LEGACY_COOKIES_FILE
    return None


def _private_cookie_copy(path):
    """
    yt-dlp 运行结束会回写 --cookies 文件（用来持久化轮换后的 Cookie）。
    这里给每次运行一个私有副本，避免覆盖插件同步进来的原始 Cookie，
    也避免并发任务互相改写同一个文件。
    """
    try:
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "ytdlp-server-cookies")
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, f"{os.path.basename(path)}.{os.getpid()}.{uuid.uuid4().hex[:6]}")
        shutil.copyfile(path, tmp)
        return tmp
    except Exception as exc:
        log(f"复制 Cookie 失败，直接使用原文件: {exc}")
        return path


def extra_args_for(url, referer=None, tmp_files=None):
    """附加参数：站点 Cookie、Referer 头（直链下载用）。
    tmp_files 传入 list 时，会把临时 Cookie 文件路径塞进去，供调用方清理。"""
    args = []
    cf = cookie_file_for(url)
    if cf:
        use = _private_cookie_copy(cf)
        if use != cf and tmp_files is not None:
            tmp_files.append(use)
        args += ["--cookies", use]
    elif COOKIES_FROM_BROWSER:
        args += ["--cookies-from-browser", COOKIES_FROM_BROWSER]
    if referer:
        args += ["--add-header", f"Referer: {referer}"]
    return args


def cleanup(files):
    for p in files or []:
        try:
            os.remove(p)
        except OSError:
            pass


# ---------------- 错误分类 ----------------

ERROR_RULES = [
    (re.compile(r"fresh cookies|not necessarily logged in|login required|sign in to confirm|"
                r"log in|login|cookies.*(needed|required)|failed to decrypt with dpapi|"
                r"dpapi|请先登录|需要登录", re.I),
     "need_cookies", "站点需要浏览器登录态（Cookie）。请先在浏览器里打开过一次该站点，插件会自动同步 Cookie"),
    (re.compile(r"unsupported url|no suitable extractor", re.I),
     "unsupported", "该链接暂不受 yt-dlp 支持（可尝试直接使用视频页地址）"),
    (re.compile(r"http error 40[13]|forbidden|access denied|403", re.I),
     "forbidden", "站点拒绝访问（403）：通常需要 Cookie，或该地区需要代理"),
    (re.compile(r"timed out|timeout|read operation timed out", re.I),
     "timeout", "网络超时：请检查网络/代理后重试"),
    (re.compile(r"unable to download webpage|urlopen error|connection|proxy|ssl|certificate",
                re.I),
     "network", "网络连接失败：请检查网络或系统代理设置"),
    (re.compile(r"private video|video unavailable|has been removed|deleted|not exist|"
                r"404|no longer available", re.I),
     "unavailable", "视频不存在或已被删除/设为私密"),
    (re.compile(r"requested format (is )?not available|no video formats found", re.I),
     "format", "该清晰度不可用，请重新解析后选择其它清晰度"),
]


def classify_error(msg):
    """把 yt-dlp 的英文报错归类，返回 (错误码, 中文提示)"""
    text = msg or ""
    for pat, code, hint in ERROR_RULES:
        if pat.search(text):
            return code, hint
    return "unknown", ""


# ---------------- 工具 ----------------

def extract_url(text):
    """从粘贴的分享文案里提取链接（抖音/快手等分享文本带一堆说明文字）"""
    text = (text or "").strip()
    m = re.search(r"https?://[^\s\"'<>，。、）)】\]]+", text)
    if m:
        return m.group(0).rstrip(".,;，。；、")
    return text


def resolve_format(fmt):
    """
    把格式标识转成 yt-dlp 参数。

    ⚠ 重要（真实踩坑）：**不要用 [height<=720] 来表达“720p”**。
      竖屏视频（抖音 / YouTube Shorts）的 720p 档是 720x1280，`[height<=720]` 会命中
      360x640（等于给你降了一大档）；横屏视频的 720p 档才是 1280x720。
      正确做法是交给 yt-dlp 排序：`-S res:720` 按「**短边**最接近 720」挑，
      实测横屏 → 1280x720、竖屏 → 720x1280 均正确。
    """
    fmt = (fmt or "best").strip()
    if fmt in FORMATS:
        return FORMATS[fmt]
    if fmt.startswith("id:"):
        fid = fmt[3:].strip()
        if re.fullmatch(r"[A-Za-z0-9_.\-]+", fid):
            # 精确到某个格式：优先“该格式 + 最佳音轨”，该格式自带音频时自动回退到它本身
            return ["-f", f"{fid}+ba/{fid}"]
        return FORMATS["best"]
    m = re.match(r"^(\d{3,4})(mp4)?$", fmt)   # "720" / "720p" 归一化后为 "720mp4"
    if m:
        height = int(m.group(1))
        # 排序键按优先级：分辨率最接近 > 容器偏好 > 编码偏好（H.264 兼容性最好）> 直连优先
        sort = f"res:{height},vcodec:h264,{PREFER_DIRECT}"
        if m.group(2):
            sort = f"res:{height},ext:mp4:m4a,vcodec:h264,{PREFER_DIRECT}"
        return ["-f", SAFE_VIDEO, "-S", sort]
    return FORMATS["best"]


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
        with TASKS_LOCK:
            for it in items:
                it.setdefault("status", "done")
                it.setdefault("percent", 100)
                TASKS[it["id"]] = it
    except Exception as exc:
        log(f"历史记录加载失败: {exc}")


def save_history():
    try:
        with TASKS_LOCK:
            items = sorted(TASKS.values(), key=lambda t: t.get("created_at", ""))
            recent = [t for t in items if t.get("status") in ("done", "error")][-HISTORY_MAX:]
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(recent, f, ensure_ascii=False, indent=1)
        os.replace(tmp, HISTORY_FILE)
    except Exception as exc:
        log(f"历史记录保存失败: {exc}")


def new_task(url, fmt, dl_dir=None, referer=None, proxy=None):
    task_id = uuid.uuid4().hex[:12]
    task = {
        "id": task_id, "url": url, "format": fmt,
        "dir": dl_dir or DOWNLOAD_DIR,
        "referer": referer,
        "proxy": proxy,
        "status": "queued", "percent": 0,
        "filename": None, "error": None, "error_code": None,
        "speed": None, "eta": None, "size_mb": None,
        "created_at": time.strftime("%H:%M:%S"),
        "started_at": None, "finished_at": None,
    }
    with TASKS_LOCK:
        TASKS[task_id] = task
    return task


# ---------------- 下载 ----------------

def worker():
    global ACTIVE_JOBS
    while True:
        task_id = JOB_QUEUE.get()
        with ACTIVE_LOCK:
            while ACTIVE_JOBS >= MAX_CONCURRENT:
                ACTIVE_LOCK.release()
                time.sleep(0.5)
                ACTIVE_LOCK.acquire()
            ACTIVE_JOBS += 1
        try:
            run_download(task_id)
        finally:
            with ACTIVE_LOCK:
                ACTIVE_JOBS -= 1
            JOB_QUEUE.task_done()


def _newest_file(dl_dir, since):
    """兜底：下载结束后在目录里找本次新产生的媒体文件"""
    best, best_mtime = None, 0
    try:
        for name in os.listdir(dl_dir):
            if name.endswith((".part", ".ytdl", ".temp")):
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


def run_download(task_id):
    with TASKS_LOCK:
        task = TASKS[task_id]
        url, fmt = task["url"], task["format"]
        dl_dir = task.get("dir") or DOWNLOAD_DIR
        task["status"] = "downloading"
        task["started_at"] = time.strftime("%H:%M:%S")
        task["error"] = None
        task["error_code"] = None

    tmp_files = []
    os.makedirs(dl_dir, exist_ok=True)
    started = time.time()
    out_tpl = os.path.join(dl_dir, "%(title).200B [%(id)s].%(ext)s")
    bin_ = ensure_ytdlp()
    if not bin_:
        with TASKS_LOCK:
            TASKS[task_id].update(status="error", error_code="no_ytdlp",
                                  error="未找到 yt-dlp，请先执行 pip install -U yt-dlp")
        save_history()
        return
    cmd = [*bin_, url, "-o", out_tpl, "--newline", "--no-playlist",
           "--progress", "--no-warnings",
           "--retries", "10", "--fragment-retries", "10", "--file-access-retries", "3",
           *resolve_format(fmt)]
    cmd += extra_args_for(url, task.get("referer"), tmp_files)

    log("开始下载: " + " ".join(cmd[:1] + ["<url>"] + cmd[2:]))
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=subprocess_env(proxy_for_url(url, task.get("proxy"))))  # UTF-8 + 按站点决策代理
    except Exception as exc:
        cleanup(tmp_files)
        with TASKS_LOCK:
            TASKS[task_id].update(status="error", error=f"启动 yt-dlp 失败: {exc}",
                                  error_code="launch")
        save_history()
        return

    pct_re = re.compile(r"(\d+(?:\.\d+)?)%")
    speed_re = re.compile(r"at\s+([0-9.]+[KMGT]?i?B/s)")
    eta_re = re.compile(r"ETA\s+([0-9:]+)")
    dest_re = re.compile(r"\[download\] Destination: (.+)$")
    merge_re = re.compile(r'(?:Merger|VideoMerge).*?"(.+)"', re.IGNORECASE)
    exists_re = re.compile(r"\[download\] (.+) has already been downloaded", re.IGNORECASE)
    total_re = re.compile(r"of\s+~?\s*([\d.]+)([KMGT]?)i?B")
    error_re = re.compile(r"ERROR:\s*(.+)")
    final_file = None

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
            with TASKS_LOCK:
                TASKS[task_id]["filename"] = os.path.basename(final_file)
        m = exists_re.search(line)
        if m:
            final_file = m.group(1).strip()
            with TASKS_LOCK:
                TASKS[task_id]["filename"] = os.path.basename(final_file)
        if "[download]" in line:
            with TASKS_LOCK:
                t = TASKS[task_id]
                m = pct_re.search(line)
                if m:
                    t["percent"] = min(99.9, float(m.group(1)))
                m = speed_re.search(line)
                if m:
                    t["speed"] = m.group(1)
                m = eta_re.search(line)
                if m:
                    t["eta"] = m.group(1)
                if t["size_mb"] is None:
                    m = total_re.search(line)
                    if m:
                        unit = {"": 1, "K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}[m.group(2)]
                        t["size_mb"] = round(float(m.group(1)) * unit, 1)
        if "Merging formats" in line or "VideoMerge" in line:
            with TASKS_LOCK:
                t = TASKS[task_id]
                t["speed"] = None
                t["percent"] = 99.9
                if final_file:
                    t["filename"] = os.path.basename(final_file)
        m = error_re.search(line)
        if m:
            with TASKS_LOCK:
                TASKS[task_id]["error"] = m.group(1).strip()[:500]

    proc.wait()
    cleanup(tmp_files)

    with TASKS_LOCK:
        task = TASKS[task_id]
        task["finished_at"] = time.strftime("%H:%M:%S")
        task["speed"] = None
        task["eta"] = None
        if proc.returncode == 0:
            if (not final_file or not os.path.exists(final_file)):
                guess = _newest_file(dl_dir, started)
                if guess:
                    final_file = guess
            task["status"] = "done"
            task["percent"] = 100
            if final_file:
                task["filename"] = os.path.basename(final_file)
                task["file_path"] = final_file
                if os.path.exists(final_file):
                    task["size_mb"] = round(os.path.getsize(final_file) / 1048576, 1)
        else:
            task["status"] = "error"
            if not task["error"]:
                task["error"] = f"yt-dlp 退出码 {proc.returncode}"
            code, hint = classify_error(task["error"])
            task["error_code"] = code
            if hint:
                task["error_hint"] = hint
    if task["status"] == "done":
        log(f"下载完成: {task.get('filename')}")
    else:
        log(f"下载失败: {task.get('error')}")
    save_history()


# ---------------- 预览 ----------------

def seconds_to_hms(s):
    s = int(s or 0)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _codec_family(vcodec):
    v = (vcodec or "").lower()
    if v.startswith(("avc", "h264")):
        return "h264"
    if v.startswith(("hev", "hvc", "h265", "bytevc1", "vvc")):
        return "h265"
    return v.split(".")[0] or "?"


def build_formats(data):
    """把 yt-dlp 的 formats 整理成插件用的清晰度列表（按高度+编码+水印去重）"""
    duration = data.get("duration") or 0
    out, seen = [], set()
    for f in data.get("formats") or []:
        vcodec = f.get("vcodec") or "none"
        if vcodec == "none":
            continue
        note = f.get("format_note") or ""
        fid = f.get("format_id") or ""
        watermarked = bool(re.search(r"watermark", note, re.I)) or fid.startswith("download_addr")
        height = f.get("height") or 0
        fam = _codec_family(vcodec)
        key = (height, fam, watermarked)
        if key in seen:
            continue
        seen.add(key)
        size = f.get("filesize") or f.get("filesize_approx") or 0
        if not size and f.get("tbr") and duration:
            size = float(f["tbr"]) * 1000 / 8 * duration
        out.append({
            "format_id": fid,
            "height": height,
            "width": f.get("width") or 0,
            "ext": f.get("ext", ""),
            "fps": f.get("fps"),
            "vcodec": fam,
            "acodec": f.get("acodec") or "none",
            "has_audio": (f.get("acodec") or "none") != "none",
            "watermarked": watermarked,
            "note": note,
            "tbr": f.get("tbr"),
            "size": round(size / 1048576, 1) if size else None,
        })
    # 清晰度高优先；同高度下优先无水印；再优先 H.264（兼容性最好）
    out.sort(key=lambda x: (-x["height"], x["watermarked"],
                            {"h264": 0, "h265": 1}.get(x["vcodec"], 2)))
    return out


def run_preview(url, proxy=None):
    """解析元数据：按代理候选依次尝试（auto 模式下直连失败会自动退回系统代理）"""
    url = extract_url(url)
    with PREVIEW_LOCK:
        cached = PREVIEW_CACHE.get(url)
        if cached and time.time() - cached[0] < PREVIEW_CACHE_TTL:
            return cached[1]

    cands = proxy_candidates(url, proxy)
    for idx, p in enumerate(cands):
        try:
            return _run_preview_once(url, p)
        except PreviewError as exc:
            if exc.code in RETRYABLE_ERRORS and idx + 1 < len(cands):
                nxt = cands[idx + 1]
                log(f"解析失败({exc.code}) → 改用{'直连' if nxt is None else nxt}重试")
                continue
            raise


def _run_preview_once(url, used_proxy):
    cf = cookie_file_for(url)
    bin_ = ensure_ytdlp()
    if not bin_:
        raise PreviewError("未找到 yt-dlp，请先执行 pip install -U yt-dlp", "no_ytdlp")
    tmp_files = []
    cmd = [*bin_, url, "--dump-single-json", "--no-warnings",
           "--no-playlist", "--skip-download"]
    cmd += extra_args_for(url, None, tmp_files)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120,
                              env=subprocess_env(used_proxy))
    except subprocess.TimeoutExpired:
        cleanup(tmp_files)
        raise PreviewError("解析超时（网络慢或站点反爬），请重试", "timeout")
    except Exception as exc:
        cleanup(tmp_files)
        raise PreviewError(f"调用 yt-dlp 失败: {exc}", "launch")
    cleanup(tmp_files)

    if proc.returncode != 0:
        out = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
        lines = [l for l in out.splitlines() if "ERROR:" in l]
        err = (lines[-1] if lines else (out.splitlines()[-1] if out.splitlines() else "解析失败"))
        err = err.replace("ERROR:", "").strip()[:300]
        code, hint = classify_error(err)
        if code == "unknown" and "dump-single-json" in err:
            err = "无法解析站点返回的数据"
        raise PreviewError(err, code, hint)

    try:
        data = json.loads(proc.stdout)
    except Exception:
        raise PreviewError("无法解析站点返回的数据", "parse")

    if isinstance(data, dict) and data.get("_type") == "playlist":
        entries = [e for e in (data.get("entries") or []) if e]
        if len(entries) == 1:
            data = entries[0]
        elif entries:
            raise PreviewError(
                f"这是一个包含 {len(entries)} 个视频的列表/主页，请选择单个视频链接", "playlist")

    formats = build_formats(data)

    audio_size = None
    for f in data.get("formats") or []:
        if (f.get("vcodec") or "none") == "none" and (f.get("acodec") or "none") != "none":
            size = f.get("filesize") or f.get("filesize_approx") or 0
            if size:
                audio_size = max(audio_size or 0, round(size / 1048576, 1))

    result = {
        "ok": True,
        "title": data.get("title"),
        "author": data.get("uploader") or data.get("channel"),
        "duration": seconds_to_hms(data.get("duration")),
        "duration_sec": data.get("duration"),
        "webpage_url": data.get("webpage_url") or url,
        "extractor": data.get("extractor"),
        "thumbnail": data.get("thumbnail"),
        "view_count": data.get("view_count"),
        "like_count": data.get("like_count"),
        "formats": formats,
        "audio_size": audio_size,
        "need_cookies": not bool(cf),
        "cookies_used": os.path.basename(cf) if cf else None,
        "proxy_used": used_proxy,
        "previewed_at": time.strftime("%H:%M:%S"),
    }
    with PREVIEW_LOCK:
        PREVIEW_CACHE[url] = (time.time(), result)
    return result


class PreviewError(Exception):
    def __init__(self, message, code="unknown", hint=""):
        super().__init__(message)
        self.code = code
        self.hint = hint


# ---------------- 目录浏览 ----------------

def list_dirs(path):
    """列出 path 下的子目录；path 为空时列出盘符"""
    if not path:
        drives = []
        for letter in string.ascii_uppercase:
            d = f"{letter}:\\"
            if os.path.exists(d):
                drives.append(d)
        return {"kind": "drives", "path": "", "parent": None,
                "items": [{"name": d, "path": d, "is_drive": True} for d in drives]}

    if not os.path.isabs(path):
        raise ValueError("必须是绝对路径")
    if not os.path.isdir(path):
        raise ValueError("目录不存在")
    items = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir() and not e.name.startswith(("$", ".")):
                        items.append({"name": e.name,
                                      "path": os.path.join(path, e.name),
                                      "is_drive": False})
                except OSError:
                    continue
    except PermissionError:
        raise PermissionError("无权限访问该目录")
    items.sort(key=lambda x: x["name"].lower())
    stripped = path.rstrip("\\/")
    is_root = len(stripped) == 2 and stripped[1] == ":"
    parent = os.path.dirname(stripped) if not is_root else None
    return {"kind": "dir", "path": path, "parent": parent, "items": items}


# ---------------- HTTP ----------------

class Handler(BaseHTTPRequestHandler):
    server_version = "ytdlp-server/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log("%s %s" % (self.address_string(), fmt % args))

    def _origin(self):
        return self.headers.get("Origin") or ""

    def _origin_ok(self):
        """
        只接受浏览器扩展发起的跨域请求：网页里的恶意脚本用 fetch 调本地服务时
        会带上站点的 Origin，这里直接拒绝（服务虽只监听 127.0.0.1，但仍有被
        任意网页当作跳板的风险）。
        """
        origin = self._origin()
        return (not origin) or origin.startswith("chrome-extension://") \
            or origin.startswith("chrome-extension-")

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self._origin()
        if origin.startswith("chrome-extension://"):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            return {}

    def do_OPTIONS(self):
        self._send(200, {"ok": True})

    def do_GET(self):
        path = self.path.split("?")[0]
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = {}
        for kv in query.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                params[k] = v

        if path != "/health" and not self._origin_ok():
            log(f"拒绝非扩展来源的请求 Origin={self._origin()} path={path}")
            self._send(403, {"error": "仅允许浏览器扩展调用本地服务"})
            return

        if path == "/health":
            self._send(200, {
                "ok": True, "version": VERSION,
                "yt_dlp": self._ytdlp_version(),
                "yt_dlp_path": " ".join(YTDLP_BIN or []),
                "download_dir": DOWNLOAD_DIR, "port": PORT,
                "pid": os.getpid(),
                "cookies": list_cookie_sites(),
                "cookies_from_browser": COOKIES_FROM_BROWSER,
                "proxy": SYSTEM_PROXY,
                "proxy_mode": PROXY_MODE,
            })
        elif path == "/api/tasks":
            with TASKS_LOCK:
                tasks = sorted(TASKS.values(), key=lambda t: t.get("created_at", ""), reverse=True)
            self._send(200, {"tasks": tasks})
        elif path.startswith("/api/tasks/"):
            tid = path.rsplit("/", 1)[-1]
            with TASKS_LOCK:
                task = TASKS.get(tid)
            self._send(200, task if task else {"error": "任务不存在"})
        elif path == "/api/cookies":
            self._send(200, {"ok": True, "cookies": list_cookie_sites()})
        elif path == "/api/list-dir":
            try:
                p = unquote(params.get("path", ""))
                result = list_dirs(p)
                result["ok"] = True
                self._send(200, result)
            except Exception as exc:
                self._send(200, {"ok": False, "error": str(exc)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        if not self._origin_ok():
            log(f"拒绝非扩展来源的请求 Origin={self._origin()} path={path}")
            self._send(403, {"error": "仅允许浏览器扩展调用本地服务"})
            return

        if path == "/api/preview":
            url = extract_url(body.get("url") or "")
            if not url.startswith(("http://", "https://")):
                self._send(400, {"error": "无效的 URL，必须以 http(s):// 开头"})
                return
            try:
                result = run_preview(url, body.get("proxy"))
                self._send(200, result)
            except PreviewError as exc:
                self._send(200, {"ok": False, "error": str(exc), "error_code": exc.code,
                                 "hint": exc.hint})
            except Exception as exc:
                self._send(200, {"ok": False, "error": str(exc)})

        elif path == "/api/download":
            url = extract_url(body.get("url") or "")
            if not url.startswith(("http://", "https://")):
                self._send(400, {"error": "无效的 URL，必须以 http(s):// 开头"})
                return
            fmt = body.get("format") or "best"
            dl_dir = body.get("dir") or None
            referer = body.get("referer") or None
            if dl_dir and not os.path.isabs(dl_dir):
                self._send(400, {"error": "保存目录必须是绝对路径"})
                return
            proc_hint = body.get("proxy")
            task = new_task(url, fmt, dl_dir, referer, proc_hint)
            JOB_QUEUE.put(task["id"])
            self._send(200, {"ok": True, "task_id": task["id"], "status": task["status"],
                             "cookies_used": os.path.basename(cookie_file_for(url) or "") or None,
                             "proxy_used": proxy_for_url(url, proc_hint)})

        elif path == "/api/cookies":
            host = (body.get("host") or "").strip()
            if not host and body.get("url"):
                host = url_host(extract_url(body["url"]))
            content = body.get("content") or ""
            try:
                p, count = save_cookies(host, content)
                self._send(200, {"ok": True, "host": host, "count": count,
                                 "path": os.path.relpath(p, BASE_DIR)})
            except Exception as exc:
                self._send(200, {"ok": False, "error": str(exc)})

        elif path == "/api/delete-cookies":
            host = (body.get("host") or "").strip().lower().lstrip(".")
            if not re.fullmatch(r"[a-z0-9][a-z0-9.\-]{1,100}", host or ""):
                self._send(400, {"error": "非法的站点域名"})
                return
            p = os.path.join(COOKIES_DIR, host + ".txt")
            try:
                if os.path.isfile(p):
                    os.remove(p)
                self._send(200, {"ok": True})
            except Exception as exc:
                self._send(500, {"error": str(exc)})

        elif path == "/api/set-default-dir":
            d = (body.get("dir") or "").strip()
            if not os.path.isabs(d):
                self._send(400, {"error": "必须是绝对路径，如 D:\\Videos"})
                return
            try:
                os.makedirs(d, exist_ok=True)
                global DOWNLOAD_DIR
                DOWNLOAD_DIR = d
                save_config()
                self._send(200, {"ok": True, "download_dir": d})
            except Exception as exc:
                self._send(500, {"error": f"设置失败: {exc}"})

        elif path == "/api/retry":
            tid = body.get("task_id")
            with TASKS_LOCK:
                old = TASKS.get(tid)
            if not old:
                self._send(404, {"error": "任务不存在"})
                return
            task = new_task(old["url"], old.get("format") or "best", old.get("dir"),
                            old.get("referer"), old.get("proxy"))
            JOB_QUEUE.put(task["id"])
            self._send(200, {"ok": True, "task_id": task["id"]})

        elif path == "/api/open-folder":
            try:
                # 三种用法：
                #   {path: "D:\\a\\b.mp4"}  -> 打开所在文件夹并选中该文件
                #   {dir:  "D:\\a"}         -> 打开该文件夹
                #   {}                      -> 打开默认下载目录
                target = (body.get("path") or body.get("file") or "").strip()
                d = (body.get("dir") or "").strip()
                if target:
                    target = os.path.abspath(target)
                    if os.path.isfile(target):
                        if sys.platform == "win32":
                            subprocess.Popen(["explorer", "/select," + target])  # noqa
                        else:
                            os.startfile(os.path.dirname(target))  # noqa
                        self._send(200, {"ok": True, "opened": "file", "path": target})
                        return
                    # 文件不在（被移走/删掉）→ 退回到它所在的目录
                    d = target if os.path.isdir(target) else os.path.dirname(target)
                    if not d:
                        d = DOWNLOAD_DIR
                if not d:
                    d = DOWNLOAD_DIR
                if not os.path.isdir(d):
                    raise ValueError(f"目录不存在: {d}")
                if sys.platform == "win32":
                    os.startfile(d)  # noqa
                self._send(200, {"ok": True, "opened": "folder", "dir": d})
            except Exception as exc:
                self._send(500, {"error": str(exc)})

        elif path == "/api/clear-cache":
            with PREVIEW_LOCK:
                PREVIEW_CACHE.clear()
            self._send(200, {"ok": True})

        else:
            self._send(404, {"error": "not found"})

    def _ytdlp_version(self):
        bin_ = ensure_ytdlp()
        if not bin_:
            return None
        ok, out, _ = _try_run(bin_ + ["--version"], timeout=20)
        return out if ok else None


def main():
    load_config()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(COOKIES_DIR, exist_ok=True)
    load_history()
    ensure_ytdlp()
    threading.Thread(target=worker, daemon=True).start()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as exc:
        log(f"端口 {PORT} 绑定失败: {exc}（可能已有服务在运行）")
        sys.exit(1)
    log(f"yt-dlp 下载服务已启动 v{VERSION}: http://127.0.0.1:{PORT}")
    log(f"下载目录: {DOWNLOAD_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("服务已停止")


if __name__ == "__main__":
    main()
