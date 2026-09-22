# -*- coding: utf-8 -*-
"""
启动器 / 服务自检脚本
=====================
模拟 Chrome 扩展的调用方式，验证「一键启动服务」是否可用：

    python test_launcher.py            # Native Messaging 协议 + 健康检查
    python test_launcher.py --kill     # 先结束已有服务再测试冷启动
"""
import json
import os
import struct
import subprocess
import sys
import time
import urllib.request

for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
HOST_EXE = os.path.join(BASE, "ytdlp_host.exe")
PORT = int(os.environ.get("YTDLP_SERVER_PORT", "8787"))
HEALTH = f"http://127.0.0.1:{PORT}/health"


def health():
    try:
        with urllib.request.urlopen(HEALTH, timeout=2) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def kill_server():
    """结束占用端口的进程（仅用于测试冷启动）"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True).stdout
        pids = {line.split()[-1] for line in out.splitlines()
                if f":{PORT}" in line and "LISTENING" in line}
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, text=True)
            print(f"已结束进程 {pid}")
    except Exception as exc:
        print("kill 失败:", exc)


def native_call(msg, timeout=20):
    """按 Chrome Native Messaging 协议调用 ytdlp_host.exe（模拟扩展）"""
    env = dict(os.environ, YTDLP_HOST_IDLE_TIMEOUT="6")
    t0 = time.time()
    proc = subprocess.Popen([HOST_EXE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    data = json.dumps(msg).encode("utf-8")
    proc.stdin.write(struct.pack("<I", len(data)) + data)
    proc.stdin.flush()
    raw = proc.stdout.read(4)
    if len(raw) < 4:
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.kill()
        raise RuntimeError("启动器没有返回数据: " + err[-500:])
    n = struct.unpack("<I", raw)[0]
    payload = proc.stdout.read(n)
    elapsed = time.time() - t0
    reply = json.loads(payload.decode("utf-8"))
    reply["_elapsed"] = round(elapsed, 1)
    proc.stdin.close()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
    return reply


def lingering():
    """统计残留的 Native Messaging Host 进程（服务本体 --serve 不算）"""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='ytdlp_host.exe'\" | "
          "Where-Object { $_.CommandLine -notlike '*--serve*' } | "
          "ForEach-Object { $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True).stdout
    return len([l for l in out.split() if l.strip().isdigit()])


def main():
    if "--kill" in sys.argv:
        kill_server()
        time.sleep(1)

    print("=" * 56)
    print("1) 运行前健康检查:", "运行中" if health() else "未运行")
    print("2) 通过 Native Messaging 请求 {type:start} …")
    res = native_call({"type": "start"})
    print("   返回:", json.dumps(res, ensure_ascii=False))
    print(f"   响应耗时: {res.get('_elapsed')}s（含冷启动）")
    print("3) 再次健康检查:")
    h = health()
    if not h:
        print("   !! 服务未启动")
        return 1
    print("   OK 版本", h.get("version"), "| yt-dlp", h.get("yt_dlp"), "| 目录", h.get("download_dir"))
    print("   Cookie 站点:", h.get("cookies"))
    print("4) 重复调用（应返回 already_running）:")
    r2 = native_call({"type": "start"})
    print("  ", json.dumps(r2, ensure_ascii=False), f"{r2.get('_elapsed')}s")
    time.sleep(7)   # 等残留的 host 进程按空闲超时退出
    left = lingering()
    print("5) 残留 Host 进程数:", left, "（应为 0；服务本体 --serve 不计入）")
    ok = bool(res.get("ok")) and bool(h) and left == 0
    print("=" * 56)
    print("结果:", "✅ 通过" if ok else "❌ 失败")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
