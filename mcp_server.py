#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcp_server.py — 视频下载 MCP 服务器（stdio，零第三方依赖）
==========================================================

把本仓库的 `vdl.py` 包装成 MCP 工具，任何支持 MCP 的客户端
（Claude Desktop / Codex / WorkBuddy / Cursor …）都能直接调用：

    download_video   给一个视频链接就下载（默认 720p MP4）
    preview_video    只解析标题/时长/可用清晰度，不下载
    list_downloads   列出已下载的文件
    get_task_status  查询后台下载任务的进度
    open_folder      在资源管理器里定位文件

设计要点
--------
* **纯标准库**：手写 JSON-RPC 2.0 over stdio（换行分隔），不需要 pip install mcp。
* **stdout 只能出现协议消息**：所有日志走 stderr，否则客户端解析会崩。
* **不重复实现下载逻辑**：一律通过子进程调 vdl.py，单一内核。

配置示例（客户端 side）见 docs/API.md。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid

SERVER_NAME = "ytdlp-video-downloader"
SERVER_VERSION = "1.0"
PROTOCOL_VERSION = "2024-11-05"

HERE = os.path.dirname(os.path.abspath(__file__))
VDL = os.path.join(HERE, "vdl.py")


def _python() -> str:
    """优先用当前解释器；PyInstaller 打包场景下回退到能找到的 python"""
    if sys.executable and os.path.basename(sys.executable).lower().startswith("python"):
        return sys.executable
    import shutil
    for name in ("python", "python3", "py"):
        p = shutil.which(name)
        if p:
            return p
    return sys.executable


def log(msg: str) -> None:
    """日志只能写 stderr：stdout 是 MCP 协议通道"""
    sys.stderr.write(f"[mcp] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- CLI 调用

def run_cli(args: list, timeout: int = 3600) -> tuple:
    """调用 vdl.py，返回 (returncode, stdout, stderr)"""
    cmd = [_python(), VDL, *args]
    log("exec: " + " ".join(cmd[:2] + ["..."] + [a for a in args if a.startswith("-")][:6]))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=HERE)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"调用超时（>{timeout}s）"
    except Exception as exc:
        return 1, "", f"调用失败: {exc}"


def parse_result(rc: int, out: str, err: str) -> dict:
    """vdl.py --json 会在 stdout 输出一个 JSON 对象；解析失败则退化成文本摘要"""
    text = (out or "").strip()
    if text:
        try:
            return json.loads(text)
        except Exception:
            pass
    return {"ok": False, "error": "无法解析 vdl.py 输出", "raw_stdout": text[:800],
            "raw_stderr": (err or "").strip()[-800:], "returncode": rc}


# ---------------------------------------------------------------- 后台任务

TASKS: dict = {}
TASKS_LOCK = threading.Lock()


def start_background(args: list) -> str:
    task_id = uuid.uuid4().hex[:10]
    out_path = os.path.join(HERE, "yt-dlp-server", f"mcp-task-{task_id}.log")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fh = open(out_path, "w", encoding="utf-8")
    proc = subprocess.Popen([_python(), VDL, *args], stdout=fh, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", cwd=HERE)
    with TASKS_LOCK:
        TASKS[task_id] = {"id": task_id, "args": args, "pid": proc.pid, "proc": proc,
                          "log": out_path, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                          "fh": fh}
    return task_id


def task_status(task_id: str) -> dict:
    with TASKS_LOCK:
        t = TASKS.get(task_id)
    if not t:
        return {"ok": False, "error": f"任务不存在: {task_id}"}
    proc: subprocess.Popen = t["proc"]
    rc = proc.poll()
    state = "running" if rc is None else ("done" if rc == 0 else "error")
    log_tail = ""
    try:
        with open(t["log"], "r", encoding="utf-8", errors="replace") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        # 进度行是覆盖式的，取最后几条有信息的
        log_tail = "\n".join(lines[-4:])[:600]
    except Exception:
        pass
    out = {"ok": True, "task_id": task_id, "state": state, "returncode": rc,
           "started": t["started"], "pid": t["pid"], "log": t["log"]}
    if state != "running":
        t["fh"].close()
        # 后台任务的结果 JSON 在日志最后一行
        for line in reversed(log_tail.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    out["result"] = json.loads(line)
                    break
                except Exception:
                    continue
        out["progress_tail"] = log_tail
    else:
        out["progress_tail"] = log_tail
    return out


# ---------------------------------------------------------------- 工具定义

def _q(schema_extra=None, default=None, desc=None):
    return schema_extra or {}


TOOLS = [
    {
        "name": "download_video",
        "description": (
            "下载视频到本地（默认 720p MP4，优先 H.264 以保证兼容性；不写死 height，"
            "横屏/竖屏都能选对档位）。支持 YouTube / 抖音 / B站 / TikTok / X 等上千个站点，"
            "也接受粘贴来的分享文案（会自动提取链接）。"
            "自动使用已同步的站点 Cookie；网络不通时自动在「直连 / 系统代理」之间切换重试。"
            "返回 JSON：ok / file_path / title / size_mb / resolution / duration_sec / error …"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "视频链接或含链接的分享文案"},
                "quality": {
                    "type": "string",
                    "description": "清晰度：720(默认) / 1080 / 480 / best / audio / id:<格式ID>",
                    "default": "720",
                },
                "dir": {"type": "string", "description": "保存目录（绝对路径，可省略用默认目录）"},
                "audio_only": {"type": "boolean", "description": "只要音频（MP3）", "default": False},
                "subs": {"type": "boolean", "description": "同时下载字幕（转 srt）", "default": False},
                "wait": {
                    "type": "boolean",
                    "description": "true=等下载完再返回（默认）；false=立即返回 task_id，用 get_task_status 轮询",
                    "default": True,
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "preview_video",
        "description": "只解析视频元数据（标题/作者/时长/所有可用清晰度与预估大小），不下载。"
                       "在下载前确认是不是目标视频时用。",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "视频链接"}},
            "required": ["url"],
        },
    },
    {
        "name": "list_downloads",
        "description": "列出默认下载目录里已有的媒体文件（按时间倒序），用于确认之前下过什么。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "最多返回几条", "default": 20}},
        },
    },
    {
        "name": "get_task_status",
        "description": "查询后台下载任务状态（配合 download_video 的 wait=false 使用）。",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "download_video 返回的 task_id"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "open_folder",
        "description": "在文件管理器里定位某个已下载文件（Windows 会选中该文件）。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "文件的绝对路径"}},
            "required": ["path"],
        },
    },
]

MEDIA_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".flv", ".m4a", ".mp3", ".opus", ".aac", ".wav")


def default_download_dir() -> str:
    """默认下载目录：与插件/vdl.py 共用同一份 config.json"""
    try:
        sys.path.insert(0, os.path.join(HERE, "yt-dlp-server"))
        import download_server as ds
        ds.load_config()
        return ds.DOWNLOAD_DIR
    except Exception:
        return os.path.join(HERE, "downloads")


# ---------------------------------------------------------------- 工具实现

def tool_download_video(a: dict) -> dict:
    url = (a.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "url 不能为空"}
    args = [url, "--json"]
    if a.get("quality"):
        args += ["--quality", str(a["quality"])]
    if a.get("audio_only"):
        args += ["--audio"]
    if a.get("subs"):
        args += ["--subs"]
    if a.get("dir"):
        args += ["--dir", str(a["dir"])]

    if a.get("wait", True):
        rc, out, err = run_cli(args)
        res = parse_result(rc, out, err)
        res["returncode"] = rc
        return res

    tid = start_background(args)
    return {"ok": True, "task_id": tid, "state": "running",
            "hint": "用 get_task_status 查询进度"}


def tool_preview_video(a: dict) -> dict:
    url = (a.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "url 不能为空"}
    rc, out, err = run_cli([url, "--info", "--json"], timeout=300)
    res = parse_result(rc, out, err)
    res["returncode"] = rc
    return res


def tool_list_downloads(a: dict) -> dict:
    limit = int(a.get("limit") or 20)
    d = default_download_dir()
    items = []
    try:
        for name in os.listdir(d):
            if not name.lower().endswith(MEDIA_EXTS):
                continue
            p = os.path.join(d, name)
            st = os.stat(p)
            items.append({"filename": name, "path": p,
                          "size_mb": round(st.st_size / 1048576, 1),
                          "modified": time.strftime("%Y-%m-%d %H:%M",
                                                    time.localtime(st.st_mtime))})
        items.sort(key=lambda x: x["modified"], reverse=True)
    except Exception as exc:
        return {"ok": False, "error": f"读取目录失败: {exc}", "dir": d}
    return {"ok": True, "dir": d, "total": len(items), "files": items[:limit]}


def tool_open_folder(a: dict) -> dict:
    path = (a.get("path") or "").strip()
    if not path or not os.path.exists(path):
        return {"ok": False, "error": f"路径不存在: {path}"}
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select," + os.path.abspath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "opened": path}


HANDLERS = {
    "download_video": tool_download_video,
    "preview_video": tool_preview_video,
    "list_downloads": tool_list_downloads,
    "get_task_status": lambda a: task_status((a.get("task_id") or "").strip()),
    "open_folder": tool_open_folder,
}


# ---------------------------------------------------------------- MCP 协议

def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(mid, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    send(msg)


def handle(msg: dict) -> None:
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        reply(mid, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    elif method in ("notifications/initialized", "initialized"):
        pass                                   # 通知无需应答
    elif method == "ping":
        reply(mid, {})
    elif method == "tools/list":
        reply(mid, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if not fn:
            reply(mid, {"content": [{"type": "text", "text": f"未知工具: {name}"}],
                        "isError": True})
            return
        try:
            res = fn(args)
        except Exception as exc:
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        reply(mid, {
            "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}],
            "isError": not bool(res.get("ok")),
        })
    elif mid is not None:
        reply(mid, error={"code": -32601, "message": f"Method not found: {method}"})
    # id 为空的其它通知直接忽略


def main() -> int:
    for stream in ("stdout", "stdin"):
        try:
            getattr(sys, stream).reconfigure(encoding="utf-8")
        except Exception:
            pass
    try:
        sys.stdout.reconfigure(newline="\n")   # 别让 Windows 把 \n 变 \r\n，破坏换行分帧
    except Exception:
        pass

    log(f"{SERVER_NAME} v{SERVER_VERSION} 启动，vdl.py = {VDL}")
    if not os.path.isfile(VDL):
        log(f"警告：找不到 {VDL}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as exc:
            log(f"非法 JSON: {exc}")
            continue
        if isinstance(msg, list):           # 批量请求
            for m in msg:
                handle(m)
        else:
            handle(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
