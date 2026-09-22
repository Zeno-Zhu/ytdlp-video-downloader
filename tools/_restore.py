# -*- coding: utf-8 -*-
"""临时：断点续传反复重试，直到把误删的视频补回来（网络抖动较大时用）
用完后可直接删除本文件。"""
import json
import os
import subprocess
import sys
import time

ROOT = r"E:\AI软件\视频下载"
URL = "https://www.youtube.com/watch?v=_VK3zG14gIE"   # 之前被误删的那个视频
TRIES = 12

PY = sys.executable
log_path = os.path.join(ROOT, "downloads", "_restore.log")

for i in range(1, TRIES + 1):
    cmd = [PY, os.path.join(ROOT, "vdl.py"), URL, "--quality", "1080", "--json", "--quiet"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT, timeout=3600)
    text = (p.stdout or "").strip()
    try:
        res = json.loads(text)
    except Exception:
        res = {"ok": False, "error": "输出无法解析", "raw": text[:300]}
    line = f"[{time.strftime('%H:%M:%S')}] 第 {i}/{TRIES} 次 ok={res.get('ok')} " \
           f"err={res.get('error_code') or '-'} {res.get('error') or ''}"[:220]
    print(line, flush=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if res.get("ok"):
        print("完成:", res.get("file_path"), flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("完成: %s\n" % res.get("file_path"))
        break
    # 非网络类错误不用硬重试
    if res.get("error_code") not in ("network", "timeout", None, "unknown"):
        print("非网络错误，停止重试", flush=True)
        break
    time.sleep(5)
