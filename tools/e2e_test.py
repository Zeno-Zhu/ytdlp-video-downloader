# -*- coding: utf-8 -*-
"""
真实案例端到端测试（抖音 + 新交互）
====================================
用真实的 Chrome + 真实扩展跑完整流程，验证：
  1. 服务未启动时，打开扩展弹窗会自动拉起本地服务（Native Messaging）
  2. 插件自动把浏览器里的抖音 Cookie 同步给本地服务（无需手工导出 cookies.txt）
  3. 解析抖音分享链接成功（标题/清晰度列表）
  4. **按次指定保存文件夹**：本次下载保存到指定目录，且默认目录不被改写
  5. **进度可见**：下载中弹窗汇总条/任务中心/图标角标都有进度
  6. **完成后一键定位文件**：任务卡片 / 任务中心 / 接口都能打开文件所在文件夹

用法：
    python tools/e2e_test.py [抖音链接]
前置：已运行 install_host.bat（注册好 Native Messaging Host）
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import websocket  # pip install websocket-client

for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT_DIR = os.path.join(ROOT, "yt-dlp-chrome-extension")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9334
SERVER = "http://127.0.0.1:8787"
DEFAULT_URL = "https://v.douyin.com/Twm2Lw7N6h0/"   # 星球研究所《中国地图，多了一点！》
VIDEO_PAGE = "https://www.douyin.com/video/7685946788875947279"
TARGET_SIZE = 85213370                              # 720p 版本的字节数


def api(path, method="GET", body=None, timeout=60):
    req = urllib.request.Request(SERVER + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def kill_server():
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                             text=True).stdout
        pids = {l.split()[-1] for l in out.splitlines()
                if ":8787" in l and "LISTENING" in l}
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
            print(f"  已结束服务进程 {pid}")
    except Exception as exc:
        print("  kill 失败:", exc)


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=180)
        self.id = 0

    def cmd(self, method, params=None):
        self.id += 1
        self.ws.send(json.dumps({"id": self.id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.id:
                if "error" in msg:
                    raise RuntimeError(f"{method} -> {msg['error']}")
                return msg.get("result", {})

    def js(self, expr, await_promise=True, timeout=180):
        r = self.cmd("Runtime.evaluate", {"expression": expr, "awaitPromise": await_promise,
                                          "returnByValue": True, "timeout": timeout * 1000})
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:400])
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def load_unpacked_extension(port):
    """Chrome 137+ 禁用了 --load-extension，改用 CDP 的 Extensions.loadUnpacked"""
    ver = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=10))
    cdp = CDP(ver["webSocketDebuggerUrl"])
    try:
        return cdp.cmd("Extensions.loadUnpacked", {"path": EXT_DIR}).get("id")
    except RuntimeError as exc:
        print("  Extensions.loadUnpacked 失败:", exc)
        return None
    finally:
        cdp.close()


def new_target(url, port=PORT):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe='')}",
        method="PUT")
    return json.load(urllib.request.urlopen(req, timeout=20))


def find_target(port, needle, kind=None):
    try:
        targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list",
                                                   timeout=10))
    except Exception:
        return None
    for t in targets:
        if needle in (t.get("url") or "") and (kind is None or t.get("type") == kind):
            return t
    return None


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    results = []

    def step(name, ok, detail=""):
        results.append((name, ok, detail))
        print(f"  [{'✅' if ok else '❌'}] {name} {detail}")

    print("=" * 66)
    print("抖音端到端测试（含新交互）:", url)
    print("=" * 66)

    out_dir = os.path.join(tempfile.gettempdir(), "ytdlp_e2e_out")
    original_default = None

    # 0) 清场
    print("\n0) 清场")
    kill_server()
    ck = os.path.join(ROOT, "yt-dlp-server", "cookies", "douyin.com.txt")
    if os.path.exists(ck):
        os.remove(ck)
        print("  已删除已有的 cookies/douyin.com.txt")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)

    profile = os.path.join(tempfile.gettempdir(), "ytdlp_e2e_profile")
    shutil.rmtree(profile, ignore_errors=True)
    args = [CHROME, f"--user-data-dir={profile}", f"--remote-debugging-port={PORT}",
            "--remote-allow-origins=*", "--no-first-run", "--no-default-browser-check",
            "--mute-audio", "--headless=new", "--window-size=1280,900", "about:blank"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # 1) 启动 Chrome + 加载扩展
        ext_id = None
        for _ in range(60):
            time.sleep(1)
            try:
                ext_id = load_unpacked_extension(PORT)
            except Exception:
                continue
            if ext_id:
                break
        step("Chrome 启动并加载扩展", bool(ext_id), f"扩展 ID = {ext_id}")
        if not ext_id:
            return 1

        # 2) 浏览器侧建立抖音 Cookie
        print("\n1) 打开抖音视频页，建立浏览器 Cookie")
        tab = new_target(VIDEO_PAGE)
        page = CDP(tab["webSocketDebuggerUrl"])
        page.cmd("Page.enable")
        page.cmd("Network.enable")
        time.sleep(15)
        title = page.js("document.title")
        cookies = page.cmd("Network.getAllCookies").get("cookies", [])
        douyin_ck = [c for c in cookies if "douyin" in (c.get("domain") or "")]
        step("抖音页面加载", bool(title and "抖音" in title), (title or "")[:32] + "…")
        step("浏览器已持有抖音 Cookie", len(douyin_ck) > 0, f"{len(douyin_ck)} 条")

        # 3) 打开弹窗（服务是停的 → 自动拉起）
        print("\n2) 打开扩展弹窗（验证自动拉起本地服务）")
        pop = new_target(f"chrome-extension://{ext_id}/popup.html")
        ui = CDP(pop["webSocketDebuggerUrl"])
        ui.cmd("Runtime.enable")
        time.sleep(12)
        try:
            health = api("/health")
            original_default = health.get("download_dir")
            step("扩展自动拉起本地服务", True, f"v{health.get('version')} pid={health.get('pid')}")
        except Exception as exc:
            health = None
            step("扩展自动拉起本地服务", False, str(exc))
        print(f"    弹窗状态: {ui.js('document.getElementById(\"health\").textContent')}")
        print(f"    提示: {ui.js('document.getElementById(\"hint\").textContent')[:70]}")

        # 4) 解析（自动同步 Cookie）
        print("\n3) 在弹窗里解析分享链接")
        ui.js(f"document.getElementById('url').value = {json.dumps(url)}; 'set'")
        t0 = time.time()
        ui.js("doPreview()", timeout=240)
        data = ui.js("previewData ? {ok:true,title:previewData.title,author:previewData.author,"
                     "duration:previewData.duration,n:(previewData.formats||[]).length,"
                     "ck:previewData.cookies_used,fmts:(previewData.formats||[]).map(f=>f.format_id+'|'+f.height+'p|'+f.vcodec)} : {ok:false}")
        step("解析成功", bool(data and data.get("ok")), (data or {}).get("title", "")[:28] if data else "")
        if data and data.get("ok"):
            print(f"    标题: {data['title'][:40]}…")
            print(f"    作者/时长: {data.get('author')} / {data.get('duration')}")
            print(f"    清晰度: {data.get('fmts')}")
            step("插件自动同步了 Cookie 文件", os.path.exists(ck),
                 f"cookies/douyin.com.txt {os.path.getsize(ck) if os.path.exists(ck) else 0} bytes")
        print(f"    解析耗时: {time.time() - t0:.1f}s")

        # 5) 按次指定文件夹 + 下载（新交互）
        print("\n4) 指定本次保存文件夹并下载（不改动默认目录）")
        ui.js(f"document.getElementById('saveDir').value = {json.dumps(out_dir)}; "
              "document.getElementById('setAsDefault').checked = false; updateDirMode(); 'set'")
        mode = ui.js("document.getElementById('dirMode').textContent")
        step("保存位置标记为「本次下载」", mode == "本次下载", f"显示 = {mode}")
        fmt = ui.js("document.getElementById('fmtSelect').value")
        print(f"    选定格式: {fmt} → {out_dir}")
        ui.js("doDownload()", timeout=120)
        time.sleep(1)
        hint = ui.js("document.getElementById('hint').textContent")
        step("提交后提示包含保存路径", out_dir in (hint or ""), (hint or "").replace("\n", " ")[:60])

        task = None
        progress_seen = False
        badge_seen = ""
        btn_seen = ""
        sw = find_target(PORT, "/background.js", "service_worker")
        sw_cdp = None
        if sw:
            try:
                sw_cdp = CDP(sw["webSocketDebuggerUrl"])
            except Exception as exc:
                print("    连接 service worker 失败:", exc)
        for i in range(400):        # 0.5s 一次，抖音 CDN 很快，必须抓瞬时进度
            tasks = api("/api/tasks").get("tasks", [])
            mine = [t for t in tasks if t["url"] == url]
            task = mine[0] if mine else None
            if task:
                if not badge_seen and sw_cdp:
                    v = sw_cdp.js("chrome.action.getBadgeText({})") or ""
                    if v:
                        badge_seen = v
                if not btn_seen:
                    v = page.js(
                        "(document.querySelector('#ytdlp-float-btn button')||{}).textContent||''") or ""
                    if "%" in v:
                        btn_seen = v
                if task["status"] == "downloading":
                    print(f"    … {task.get('percent')}% {task.get('speed') or ''} 角标={badge_seen!r} 按钮={btn_seen!r}")
                    if (task.get("percent") or 0) > 0:
                        progress_seen = True
            if task and task["status"] in ("done", "error"):
                break
            time.sleep(0.5)
        step("下载中能看到进度（服务端百分比）", progress_seen)
        if sw_cdp is not None:
            step("图标角标显示进度", bool(badge_seen and "%" in badge_seen), f"角标 = {badge_seen!r}")
        else:
            step("图标角标显示进度", False, "未连上 service worker（跳过）")
        step("视频页悬浮按钮显示进度", "%" in (btn_seen or ""), f"按钮 = {btn_seen!r}")

        ok_task = bool(task and task["status"] == "done")
        step("下载完成", ok_task, (task or {}).get("error") or f"{task.get('size_mb')} MB" if task else "")
        if ok_task:
            step("保存到了本次指定的文件夹", os.path.dirname(task.get("file_path") or "") == out_dir,
                 task.get("file_path") or "")
            exists = os.path.isfile(task.get("file_path") or "")
            step("文件真实存在且大小一致", exists and os.path.getsize(task["file_path"]) == TARGET_SIZE,
                 f"{os.path.getsize(task['file_path']) if exists else 0} bytes")
            step("默认下载目录未被改动", api("/health").get("download_dir") == original_default,
                 api("/health").get("download_dir"))

        # 6) 任务中心页面
        print("\n5) 打开发任务中心页面")
        ui.js("chrome.tabs.create({url: chrome.runtime.getURL('tasks.html')}); 'ok'")
        tgt = None
        for _ in range(10):
            time.sleep(1)
            tgt = find_target(PORT, "/tasks.html", "page")
            if tgt:
                break
        if tgt:
            tk = CDP(tgt["webSocketDebuggerUrl"])
            time.sleep(3)
            ov = tk.js("document.getElementById('ovTitle').textContent")
            cards = tk.js("document.querySelectorAll('.task').length")
            has_locate = tk.js("Array.from(document.querySelectorAll('.task button')).some(b=>b.textContent.includes('定位文件'))")
            step("任务中心页可打开且有任务卡片", bool(cards), f"{cards} 个卡片 · {ov}")
            step("任务卡片带「定位文件」按钮", bool(has_locate))
            # 页面上的悬浮按钮进度（当前页不是抖音页，这里只验证消息通道可用）
            tk.close()
        else:
            step("任务中心页可打开且有任务卡片", False, "页面未找到")

        # 7) 一键定位文件（真的会在资源管理器里选中该文件）
        print("\n6) 打开文件所在文件夹并选中文件")
        r = api("/api/open-folder", "POST", {"path": (task or {}).get("file_path")}, timeout=30)
        step("open-folder 定位文件", bool(r.get("ok")) and r.get("opened") == "file",
             f"{r.get('opened')} · {os.path.basename(r.get('path') or '')[:30]}")
        print(f"    （已在资源管理器中选中该文件；测试副本位于 {out_dir}，可自行删除）")

        ui.close()
        page.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()

    print("\n" + "=" * 66)
    bad = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(f"{'✅' if ok else '❌'} {name} {detail}")
    print("=" * 66)
    print("结果:", "✅ 全部通过" if not bad else f"❌ {len(bad)} 项失败")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
