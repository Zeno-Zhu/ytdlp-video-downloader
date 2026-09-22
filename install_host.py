# -*- coding: utf-8 -*-
"""
yt-dlp 插件「一键启动服务」安装器
=================================
一次双击完成全部配置，**不需要手工复制扩展 ID**：

  1. 自动从 Chrome / Edge / Chromium / Brave / Vivaldi 的配置里找出本扩展的 ID
  2. 注册 Native Messaging Host（扩展弹窗「⚡ 启动服务」即可直接拉起本地服务）
  3. 设置开机自启（登录后服务自动运行，无窗口）
  4. 立即启动一次服务并校验

用法：
    install_host.bat                  # 安装（推荐直接双击）
    install_host.bat --uninstall      # 卸载（移除注册表项，保留服务文件）
    install_host.bat --ext-id <ID>    # 手工指定扩展 ID（自动识别失败时）
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

# 控制台/管道编码兜底：避免中文或符号在非 UTF-8 代码页下抛 UnicodeEncodeError
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(BASE, "yt-dlp-server")
HOST_EXE = os.path.join(SERVER_DIR, "ytdlp_host.exe")
VBS = os.path.join(SERVER_DIR, "start_hidden.vbs")
MANIFEST = os.path.join(SERVER_DIR, "native_manifest.json")
HOST_NAME = "com.ytdlp.server"
EXT_DIR = os.path.abspath(os.path.join(BASE, "yt-dlp-chrome-extension"))
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ytdlp-server"

# 浏览器 -> NativeMessagingHosts 注册表路径
BROWSERS = {
    "Chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "Chrome Beta": r"Software\Google\Chrome Beta\NativeMessagingHosts",
    "Chrome Dev": r"Software\Google\Chrome Dev\NativeMessagingHosts",
    "Edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
    "Edge Beta": r"Software\Microsoft\Edge Beta\NativeMessagingHosts",
    "Chromium": r"Software\Chromium\NativeMessagingHosts",
    "Brave": r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
    "Vivaldi": r"Software\Vivaldi\NativeMessagingHosts",
}

PROFILE_ROOTS = {
    "Chrome": ["Google/Chrome/User Data"],
    "Chrome Beta": ["Google/Chrome Beta/User Data"],
    "Edge": ["Microsoft/Edge/User Data"],
    "Chromium": ["Chromium/User Data"],
    "Brave": ["BraveSoftware/Brave-Browser/User Data"],
    "Vivaldi": ["Vivaldi/User Data"],
}

EXT_ID_RE = re.compile(r'"([a-p]{32})"\s*:\s*\{')


def out(msg=""):
    print(msg, flush=True)


def find_extension_ids():
    """从各浏览器配置中找出加载了本扩展（路径匹配）的扩展 ID"""
    # Preferences 里存的是原始 UTF-8 中文路径，且反斜杠已转义；
    # json.dumps 会带上外层引号，这里去掉，拼成 "path":"..." 再匹配
    escaped = json.dumps(EXT_DIR, ensure_ascii=False)[1:-1]
    variants = {escaped, escaped.replace("\\\\", "\\\\\\\\"), EXT_DIR}
    local = os.environ.get("LOCALAPPDATA") or ""
    roaming = os.environ.get("APPDATA") or ""
    found = {}                              # id -> 说明

    for browser, rels in PROFILE_ROOTS.items():
        for rel in rels:
            for root in (os.path.join(local, rel.replace("/", os.sep)),
                         os.path.join(roaming, rel.replace("/", os.sep))):
                if not os.path.isdir(root):
                    continue
                for name in ("Secure Preferences", "Preferences"):
                    for path in glob.glob(os.path.join(root, "*", name)):
                        try:
                            if os.path.getsize(path) > 80 * 1024 * 1024:
                                continue
                            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                                txt = f.read()
                        except OSError:
                            continue
                        for v in variants:
                            needle = '"path":"%s"' % v
                            idx = txt.find(needle)
                            while idx > 0:
                                head = txt[max(0, idx - 8000):idx]
                                m = None
                                for m in EXT_ID_RE.finditer(head):
                                    pass
                                if m:
                                    prof = os.path.basename(os.path.dirname(path))
                                    found.setdefault(m.group(1), []).append(
                                        f"{browser}/{prof}")
                                idx = txt.find(needle, idx + 1)
    return found


def write_manifest(ids):
    origins = ["chrome-extension://%s/" % i for i in sorted(ids)]
    data = {
        "name": HOST_NAME,
        "description": "yt-dlp 本地下载服务启动器",
        "path": HOST_EXE,
        "type": "stdio",
        "allowed_origins": origins,
    }
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return origins


def register(ids):
    import winreg
    done = []
    for browser, key_path in BROWSERS.items():
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path + "\\" + HOST_NAME,
                                    0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, MANIFEST)
            done.append(browser)
        except OSError as exc:
            out(f"  !! {browser} 注册失败: {exc}")
    return done


def set_autostart(enable=True):
    import winreg
    cmd = '"%s" --serve' % (HOST_EXE if os.path.isfile(HOST_EXE)
                           else os.path.join(SERVER_DIR, "download_server.py"))
    if not os.path.isfile(HOST_EXE):
        cmd = 'wscript.exe "%s"' % VBS
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_WRITE) as key:
        if enable:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except OSError:
                pass
    return cmd


def unregister():
    import winreg
    for key_path in BROWSERS.values():
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path + "\\" + HOST_NAME)
        except OSError:
            pass
    set_autostart(False)
    out("[卸载完成] 已移除 Native Messaging 注册与开机自启（服务文件保留）")


def start_service():
    """立即启动一次服务（优先用打包好的 exe，否则用 python）"""
    if os.path.isfile(HOST_EXE):
        try:
            p = subprocess.run([HOST_EXE, "--start"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            return p.returncode == 0, (p.stdout or "").strip()
        except Exception as exc:
            return False, str(exc)
    py = sys.executable
    try:
        subprocess.Popen([py, os.path.join(SERVER_DIR, "download_server.py")],
                         cwd=SERVER_DIR, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        time.sleep(3)
        return True, "已通过 Python 启动服务"
    except Exception as exc:
        return False, str(exc)


def main(argv):
    if "--uninstall" in argv:
        unregister()
        return 0

    out("=" * 52)
    out("  yt-dlp 插件「一键启动服务」安装器")
    out("=" * 52)
    out()

    if not os.path.isdir(EXT_DIR):
        out(f"!! 未找到插件目录: {EXT_DIR}")
        return 1
    if not os.path.isfile(HOST_EXE):
        out(f"!! 未找到启动器 {HOST_EXE}（可运行 build_host.ps1 重新打包）")

    # 1. 扩展 ID
    ids = set()
    for i, arg in enumerate(argv):
        if arg == "--ext-id" and i + 1 < len(argv):
            ids.add(argv[i + 1].strip())
    detected = find_extension_ids()
    for eid in detected:
        ids.add(eid)
    ids = {i for i in ids if re.fullmatch(r"[a-p]{32}", i)}

    if detected:
        for eid, where in detected.items():
            out(f"[1/4] 已自动识别扩展 ID: {eid}  ({', '.join(sorted(set(where)))})")
    elif ids:
        out(f"[1/4] 使用手工指定的扩展 ID: {', '.join(sorted(ids))}")
    else:
        out("[1/4] 未能自动识别扩展 ID（插件可能未加载）。")
        out("      请先在 chrome://extensions 加载插件后重跑本脚本，")
        out("      或执行: install_host.bat --ext-id <你的扩展ID>")
        out("      不填写也能安装（开机自启 + 自动拉起仍可用），继续…")

    # 2. 写 native manifest + 注册
    write_manifest(ids)
    out(f"[2/4] native manifest 已生成: {MANIFEST}")
    regs = register(ids)
    out(f"      已注册到: {', '.join(regs) if regs else '（无）'}")

    # 3. 开机自启
    cmd = set_autostart(True)
    out(f"[3/4] 开机自启已设置: {cmd}")

    # 4. 立即启动
    ok, detail = start_service()
    out(f"[4/4] {'服务已就绪' if ok else '服务启动失败'}: {detail}")
    out()
    out("=" * 52)
    if ok:
        out("  安装完成！")
        out("  * 扩展弹窗现在会显示「服务已连接」，未启动时点「⚡ 启动服务」即可")
        out("  * 重启电脑后服务也会自动运行，无需再手动开程序")
    else:
        out("  配置已完成，但服务未能启动，请检查：")
        out("  * pip install -U yt-dlp")
        out("  * 查看 yt-dlp-server\\launcher.log 与 server.log")
    out("=" * 52)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
