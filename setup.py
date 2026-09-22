#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup.py — 新机器一键初始化
============================

做四件事：
  1. 检查 Python 版本
  2. 确认 / 安装 yt-dlp（以及可选的 ffmpeg 检测）
  3. 生成 yt-dlp-server/config.json（默认下载目录指向 <仓库>/downloads）
  4. 自检：列出内核信息；可选联网跑一次真实解析

用法：
    python setup.py              # 初始化 + 自检
    python setup.py --test       # 额外联网解析一个视频（验证网络/代理）
    python setup.py --ffmpeg     # 顺带尝试用 winget/choco 安装 ffmpeg（Windows）
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(HERE, "yt-dlp-server")
CONFIG = os.path.join(SERVER_DIR, "config.json")
DOWNLOAD_DIR = os.path.join(HERE, "downloads")
MIN_PY = (3, 8)

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def ok(msg):
    print(f"  [OK]  {msg}", flush=True)


def warn(msg):
    print(f"  [!!]  {msg}", flush=True)


def info(msg):
    print(f"        {msg}", flush=True)


def step(n, title):
    print(f"\n[{n}] {title}", flush=True)


def check_python():
    v = sys.version_info
    if v[:2] < MIN_PY:
        warn(f"Python {v.major}.{v.minor} 太旧，需要 >= {MIN_PY[0]}.{MIN_PY[1]}")
        return False
    ok(f"Python {v.major}.{v.minor}.{v.micro}  ({sys.executable})")
    return True


def find_ytdlp():
    sys.path.insert(0, SERVER_DIR)
    try:
        import download_server as ds
        return ds.find_ytdlp()
    except Exception as exc:
        warn(f"加载内核失败: {exc}")
        return None


def ensure_ytdlp(auto_install=True):
    cmd = find_ytdlp()
    if cmd:
        try:
            p = subprocess.run(cmd + ["--version"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
            ok(f"yt-dlp 已就绪: {p.stdout.strip()}  ({' '.join(cmd)})")
            return True
        except Exception:
            pass

    warn("未找到可用的 yt-dlp")
    if not auto_install:
        info("请手动执行: pip install -U yt-dlp")
        return False

    info("正在安装 yt-dlp（pip install -U yt-dlp）…")
    try:
        p = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
        if p.returncode == 0:
            ok("yt-dlp 安装完成")
            return True
        warn("安装失败，请手动执行: pip install -U yt-dlp")
        info((p.stderr or "").strip()[-400:])
        return False
    except Exception as exc:
        warn(f"安装失败: {exc}")
        info("请手动执行: pip install -U yt-dlp")
        return False


def check_ffmpeg(try_install=False):
    p = shutil.which("ffmpeg")
    if p:
        ok(f"ffmpeg 已就绪: {p}")
        return True
    warn("未找到 ffmpeg —— 影响：音视频合并、MP3 提取、字幕转 srt")
    if try_install and platform.system() == "Windows":
        for mgr, args in (("winget", ["winget", "install", "-e", "--id", "Gyan.FFmpeg",
                                      "--accept-source-agreements",
                                      "--accept-package-agreements"]),
                          ("choco", ["choco", "install", "ffmpeg", "-y"])):
            if shutil.which(mgr):
                info(f"尝试用 {mgr} 安装 ffmpeg…")
                try:
                    subprocess.run(args, timeout=900)
                    if shutil.which("ffmpeg"):
                        ok("ffmpeg 安装完成（可能需要重开终端）")
                        return True
                except Exception as exc:
                    info(f"{mgr} 安装失败: {exc}")
    info("下载安装: https://www.gyan.dev/ffmpeg/builds/  （或 choco install ffmpeg）")
    return False


def ensure_config():
    os.makedirs(SERVER_DIR, exist_ok=True)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    if os.path.isfile(CONFIG):
        try:
            with open(CONFIG, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
            ok(f"config.json 已存在: {cfg.get('download_dir')}")
            if "proxy" not in cfg:
                cfg["proxy"] = "auto"
                with open(CONFIG, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=1)
                info("已补上 proxy=auto（先直连，网络失败自动退回系统代理）")
            return True
        except Exception as exc:
            warn(f"config.json 读取失败，将重建: {exc}")
    cfg = {"download_dir": DOWNLOAD_DIR, "proxy": "auto"}
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    ok(f"已生成 config.json → 下载目录 {DOWNLOAD_DIR}")
    return True


def show_kernel_info():
    sys.path.insert(0, SERVER_DIR)
    try:
        import download_server as ds
        ds.load_config()
        ok(f"下载目录 : {ds.DOWNLOAD_DIR}")
        ok(f"代理     : 模式 {ds.PROXY_MODE}；系统代理 {ds.SYSTEM_PROXY or '无'}")
        ok(f"Cookie   : {[c['host'] for c in ds.list_cookie_sites()] or '（还没有，插件会自动同步）'}")
        ok(f"内核版本 : v{ds.VERSION}")
        return True
    except Exception as exc:
        warn(f"内核自检失败: {exc}")
        return False


def network_test():
    url = "https://www.youtube.com/watch?v=jXwOcpkMQAA"
    info(f"联网解析: {url}")
    cmd = [sys.executable, os.path.join(HERE, "vdl.py"), url, "--info", "--json", "--quiet"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=240, cwd=HERE)
    except Exception as exc:
        warn(f"调用失败: {exc}")
        return False
    try:
        data = json.loads((p.stdout or "").strip())
    except Exception:
        warn("输出无法解析")
        info((p.stderr or "").strip()[-400:])
        return False
    if data.get("ok"):
        ok(f"解析成功: {data.get('title')}  {data.get('duration')}  "
           f"清晰度 {data.get('quality_options')}")
        return True
    warn(f"解析失败 [{data.get('error_code')}] {data.get('error')}")
    info(f"建议: {data.get('hint')}")
    info("可尝试: python vdl.py \"<url>\" --info --proxy none    （或 --proxy system）")
    return False


def main(argv):
    print("=" * 58)
    print("  yt-dlp 视频下载器 — 环境初始化 / 自检")
    print("=" * 58)

    step(1, "检查 Python")
    py_ok = check_python()

    step(2, "检查 yt-dlp（真正的下载引擎）")
    yt_ok = ensure_ytdlp()

    step(3, "检查 ffmpeg（合并/转码用）")
    check_ffmpeg(try_install="--ffmpeg" in argv)

    step(4, "生成配置")
    ensure_config()

    step(5, "内核自检")
    show_kernel_info()

    net_ok = None
    if "--test" in argv:
        step(6, "联网验证")
        net_ok = network_test()

    print("\n" + "=" * 58)
    if py_ok and yt_ok:
        print("  初始化完成！下一步：")
        print('    python vdl.py "<视频链接>" --quality 720 --json')
        print("  浏览器插件（可选）：")
        print("    chrome://extensions → 开发者模式 → 加载已解压的扩展程序 → yt-dlp-chrome-extension")
        print("    然后双击 install_host.bat")
        if net_ok is False:
            print("\n  注意：联网自检没通过，多半是代理策略问题，试试 --proxy none 或 --proxy system")
    else:
        print("  有步骤未通过，请按上面的 [!!] 提示处理后重跑本脚本")
    print("=" * 58)
    return 0 if (py_ok and yt_ok) else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
