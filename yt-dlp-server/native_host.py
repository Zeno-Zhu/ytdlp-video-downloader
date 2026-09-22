# -*- coding: utf-8 -*-
"""
yt-dlp 本地服务 Native Messaging Host / 启动器
==============================================
供 Chrome / Edge 扩展通过 chrome.runtime.connectNative 一键启动本地下载服务。

协议（stdio，4 字节小端长度前缀 + UTF-8 JSON）：
    {type: "start"}  -> {"ok": true, "status": "started" | "already_running", ...}
    {type: "ping"}   -> {"ok": true, "status": "running"|"stopped", "version": ...}

命令行用法（打包成 ytdlp_host.exe 后可直接运行，无需安装 Python）：
    ytdlp_host.exe --serve    在本进程内直接运行下载服务（开机自启用，无窗口）
    ytdlp_host.exe --start    后台拉起下载服务进程后退出
    双击 exe                 等同 --start
"""
import json
import os
import queue as _queue
import struct
import subprocess
import sys
import threading
import time
import urllib.request

if getattr(sys, "frozen", False):
    BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

LOG_FILE = os.path.join(BASE, "launcher.log")

try:
    import download_server as ds
    PORT = ds.PORT
    SERVER_VERSION = ds.VERSION
except Exception as _exc:  # pragma: no cover - 理论上不会发生
    ds = None
    PORT = int(os.environ.get("YTDLP_SERVER_PORT", "8787"))
    SERVER_VERSION = "?"
    _IMPORT_ERROR = str(_exc)
else:
    _IMPORT_ERROR = None

HEALTH_URL = f"http://127.0.0.1:{PORT}/health"
SERVER_SCRIPT = os.path.join(BASE, "download_server.py")


# ---------------- 日志 ----------------

def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass
    try:
        if sys.stderr:
            sys.stderr.write(str(msg) + "\n")
            sys.stderr.flush()
    except Exception:
        pass


# ---------------- 健康检查 ----------------

def health(timeout=1.5):
    """返回 (是否运行, health 字典)"""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as r:
            return True, json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return False, None


def server_running():
    return health(1.5)[0]


def port_in_use():
    import socket
    s = socket.socket()
    try:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    except Exception:
        return False
    finally:
        s.close()


# ---------------- 启动服务 ----------------

def _spawn_detached(cmd):
    """后台启动进程：与浏览器/本进程完全脱离，无窗口"""
    flags = 0
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    devnull = open(os.devnull, "rb+")
    return subprocess.Popen(cmd, cwd=BASE, env=env, creationflags=flags,
                            stdin=devnull, stdout=devnull, stderr=devnull,
                            close_fds=True)


def start_server():
    ok, h = health(2.0)
    if ok:
        ver = (h or {}).get("version")
        status = "already_running" if ver == SERVER_VERSION else "running_other_version"
        return {"ok": True, "status": status, "version": ver,
                "download_dir": (h or {}).get("download_dir"),
                "yt_dlp": (h or {}).get("yt_dlp")}

    if _IMPORT_ERROR:
        return {"ok": False, "status": "error", "error": f"服务模块加载失败: {_IMPORT_ERROR}"}

    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--serve"]          # 同一个 exe，无需 Python 环境
    else:
        py = ds.find_python()
        if not py:
            return {"ok": False, "status": "no_python",
                    "error": "未找到可用的 Python 解释器，无法启动服务"}
        cmd = [py, SERVER_SCRIPT]

    log("启动服务: " + " ".join(cmd))
    try:
        _spawn_detached(cmd)
    except Exception as exc:
        log(f"启动失败: {exc}")
        return {"ok": False, "status": "error", "error": f"无法启动服务: {exc}"}

    for _ in range(40):                            # 最多等 20 秒
        time.sleep(0.5)
        ok, h = health(1.5)
        if ok:
            log("服务已启动")
            return {"ok": True, "status": "started", "version": (h or {}).get("version"),
                    "download_dir": (h or {}).get("download_dir"),
                    "yt_dlp": (h or {}).get("yt_dlp")}

    if port_in_use():
        msg = f"端口 {PORT} 已被其它程序占用，请关闭后重试"
    else:
        msg = ("服务启动超时：请检查 yt-dlp 是否已安装（pip install -U yt-dlp），"
               f"日志见 {LOG_FILE}")
    log("启动失败: " + msg)
    return {"ok": False, "status": "timeout", "error": msg}


def serve_forever():
    """在本进程内运行下载服务（--serve）"""
    log(f"以服务模式启动 (v{SERVER_VERSION}, port {PORT})")
    if ds is None:
        log("服务模块加载失败: " + str(_IMPORT_ERROR))
        return 1
    ds.main()
    return 0


# ---------------- Native Messaging ----------------

def send(obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    out = sys.stdout.buffer if getattr(sys.stdout, "buffer", None) else None
    if out is None:
        return
    out.write(struct.pack("<I", len(data)) + data)
    out.flush()


def read():
    stdin = sys.stdin
    if stdin is None or getattr(stdin, "buffer", None) is None:
        return None
    raw = stdin.buffer.read(4)
    if not raw or len(raw) < 4:
        return None
    n = struct.unpack("<I", raw)[0]
    payload = stdin.buffer.read(n)
    try:
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return None


# 空闲超时：扩展发完消息就断开，但 PyInstaller 单文件模式的父进程可能一直持有
# 管道句柄、导致 EOF 迟迟不来。加一个空闲退出，避免残留僵尸进程。
IDLE_TIMEOUT = float(os.environ.get("YTDLP_HOST_IDLE_TIMEOUT", "20") or 20)
_TIMEOUT = object()


class _StdinReader(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__(name="stdin-reader")
        self.q = _queue.Queue()

    def run(self):
        while True:
            try:
                msg = read()
            except Exception:
                msg = None
            self.q.put(msg)
            if msg is None:
                break


def read_msg(timeout):
    try:
        return _READER.q.get(timeout=timeout)
    except _queue.Empty:
        return _TIMEOUT


_READER = None


def handle(msg):
    t = (msg or {}).get("type")
    if t == "start":
        return start_server()
    if t == "ping":
        ok, h = health(1.5)
        return {"ok": True, "status": "running" if ok else "stopped",
                "version": (h or {}).get("version"), "port": PORT}
    if t == "version":
        return {"ok": True, "version": SERVER_VERSION, "port": PORT, "frozen": getattr(sys, "frozen", False)}
    return {"ok": False, "error": "unknown message type"}


def main(argv):
    # PyInstaller --noconsole 下 stdin/stdout 可能为 None，需从文件描述符恢复
    if sys.stdin is None:
        try:
            sys.stdin = os.fdopen(0, "rb")
        except Exception:
            pass
    if sys.stdout is None:
        try:
            sys.stdout = os.fdopen(1, "wb")
        except Exception:
            pass

    if "--serve" in argv:
        return serve_forever()
    if "--start" in argv:
        res = start_server()
        log("--start 结果: " + json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok") else 1

    # 无参数：被扩展通过 Native Messaging 调用则走协议；
    # 被用户双击（没有 stdin 管道）则当作“启动服务”
    if sys.stdin is None:
        res = start_server()
        log("双击启动结果: " + json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok") else 1

    global _READER
    _READER = _StdinReader()
    _READER.start()
    started_at = time.time()
    while True:
        msg = read_msg(IDLE_TIMEOUT)
        if msg is None:
            break                      # 扩展已断开
        if msg is _TIMEOUT:
            log(f"空闲 {IDLE_TIMEOUT:.0f}s 无消息，退出")
            break
        try:
            send(handle(msg))
        except Exception as exc:
            log(f"处理消息出错: {exc}")
            try:
                send({"ok": False, "error": str(exc)})
            except Exception:
                break
        # 扩展收到应答后会立即 disconnect()；这里硬退出，避免在 Chrome 下因管道
        # 句柄/PyInstaller 父子进程纠缠而残留（扩展侧有 /health 兜底，不依赖这行是否送达）
        if (msg or {}).get("type") in ("start", "ping"):
            log(f"已应答 {msg.get('type')}（耗时 {time.time() - started_at:.1f}s），退出")
            exit_now()
    return 0


def exit_now():
    """确保进程立刻结束（os._exit 跳过解释器收尾，避免被阻塞的管道读挂住）"""
    try:
        sys.stdout.flush()
    except Exception:
        pass
    try:
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
