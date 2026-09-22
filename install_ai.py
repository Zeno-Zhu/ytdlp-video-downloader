#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_ai.py —— 一条命令，把「视频下载」接入 AI。

设计结论（为什么长这样）：

    CLI  做内核   → 逻辑只有一份（vdl.py），谁都能调，**上下文开销 0**
    Skill 做接入  → 常驻上下文只有 frontmatter description（约 180 字符），
                    正文只在"用户真的要下视频"时才被读进来（约 3.8k 字符）
    MCP   做可选  → 工具定义**全量常驻**（约 1.8k 字符，每次请求都带），
                    只在需要"跨客户端 + 零文档 + 标准工具形态"时才开

所以默认只装 Skill。MCP 用 `--mcp` 显式打开。

用法
    python install_ai.py                 装 Skill（默认，最省）
    python install_ai.py --all            Skill + MCP + Codex AGENTS.md
    python install_ai.py --mcp            只额外注册 MCP
    python install_ai.py --agents         只额外写 ~/.codex/AGENTS.md
    python install_ai.py --target both    workbuddy + claude 两个 skill 目录都写
    python install_ai.py --status         看看现在装了什么
    python install_ai.py --dry-run        只打印计划，不落盘
    python install_ai.py --uninstall      卸载（Skill / MCP 条目 / AGENTS 块）

换机器或挪了目录 —— 重新跑一次本脚本即可，所有绝对路径会被刷新。
"""

import argparse
import json
import os
import re
import shutil
import sys
import time

# ---------------------------------------------------------------- 仓库自身

REPO = os.path.dirname(os.path.abspath(__file__))
SKILL_NAME = "video-download"
TEMPLATE = os.path.join(REPO, "skills", SKILL_NAME, "SKILL.md.in")
VDL = os.path.join(REPO, "vdl.py")
MCP_SERVER = os.path.join(REPO, "mcp_server.py")

MCP_KEY = "ytdlp-video-downloader"
MARK_BEGIN = "<!-- BEGIN ytdlp-video-downloader -->"
MARK_END = "<!-- END ytdlp-video-downloader -->"

HOME = os.path.expanduser("~")

SKILL_TARGETS = {
    "workbuddy": os.path.join(HOME, ".workbuddy", "skills", SKILL_NAME, "SKILL.md"),
    "claude": os.path.join(HOME, ".claude", "skills", SKILL_NAME, "SKILL.md"),
}
MCP_JSON = os.path.join(HOME, ".workbuddy", "mcp.json")
CODEX_AGENTS = os.path.join(HOME, ".codex", "AGENTS.md")

# ---------------------------------------------------------------- 小工具


class C:
    G = "\033[32m"
    Y = "\033[33m"
    R = "\033[31m"
    B = "\033[36m"
    D = "\033[90m"
    X = "\033[0m"

    @classmethod
    def off(cls):
        for k in ("G", "Y", "R", "B", "D", "X"):
            setattr(cls, k, "")


if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C.off()


def ok(msg):
    print(f"  {C.G}OK{C.X}   {msg}")


def warn(msg):
    print(f"  {C.Y}!{C.X}    {msg}")


def bad(msg):
    print(f"  {C.R}x{C.X}    {msg}")


def info(msg):
    print(f"  {C.D}·{C.X}    {msg}")


def head(msg):
    print(f"\n{C.B}{msg}{C.X}")


def fwd(p):
    """出到 bash / JSON 里一律用正斜杠，Windows 上也认，省掉转义地狱。"""
    return str(p).replace("\\", "/")


def read_text(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def backup(path):
    if not os.path.isfile(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.bak-{stamp}"
    shutil.copy2(path, dst)
    return dst


# ---------------------------------------------------------------- 解释器探测


def pick_python():
    """挑一个稳定的 python 解释器（要能在别的 cwd 下跑 vdl.py）。

    优先当前解释器；其次交给内核的 find_python()（它会跳过 Windows 商店别名）。
    """
    cands = []
    exe = sys.executable or ""
    if exe and os.path.isfile(exe):
        cands.append(exe)

    try:
        sys.path.insert(0, os.path.join(REPO, "yt-dlp-server"))
        import download_server as ds  # noqa

        found = ds.find_python(console=True)
        if found:
            cands.append(found)
    except Exception as exc:
        info(f"内核 find_python 不可用（{exc}），退回自带探测")

    for name in ("python.exe", "python3.exe", "python3", "python"):
        w = shutil.which(name)
        if w:
            cands.append(w)

    seen, ordered = set(), []
    for p in cands:
        key = os.path.abspath(p).lower()
        if key in seen or not os.path.isfile(p):
            continue
        if "windowsapps" in key:          # 商店别名，可能弹商店，不要
            continue
        seen.add(key)
        ordered.append(p)
    return ordered[0] if ordered else "python"


# ---------------------------------------------------------------- Skill


def render_skill(py):
    if not os.path.isfile(TEMPLATE):
        raise FileNotFoundError(f"找不到 Skill 模板：{TEMPLATE}")
    tpl = read_text(TEMPLATE)
    for token, value in (("{{PY}}", fwd(py)), ("{{REPO}}", fwd(REPO)), ("{{VDL}}", fwd(VDL))):
        tpl = tpl.replace(token, value)
    left = re.findall(r"\{\{[A-Z_]+\}\}", tpl)
    if left:
        raise RuntimeError(f"模板里还有没渲染的占位符：{sorted(set(left))}")
    return tpl


def install_skill(targets, py, dry):
    head("Skill（接入层 · 常驻上下文 ~180 字符）")
    content = render_skill(py)
    n_targets = 0
    for name in targets:
        dst = SKILL_TARGETS[name]
        old = read_text(dst) if os.path.isfile(dst) else None
        if old == content:
            ok(f"{name}: 已是最新 → {fwd(dst)}")
            n_targets += 1
            continue
        if dry:
            action = "覆盖" if old is not None else "新建"
            info(f"[dry-run] {name}: 将{action} {fwd(dst)}  ({len(content)} 字符)")
            n_targets += 1
            continue
        try:
            if old is not None:
                b = backup(dst)
                info(f"{name}: 旧版已备份 → {os.path.basename(b)}")
            write_text(dst, content)
            ok(f"{name}: {'更新' if old is not None else '写入'} → {fwd(dst)}")
            n_targets += 1
        except Exception as exc:
            bad(f"{name}: 写入失败 —— {exc}")
    if not n_targets:
        raise RuntimeError("没有任何 Skill 目标写入成功")
    return n_targets


# ---------------------------------------------------------------- MCP


def mcp_entry(py):
    return {
        "command": fwd(py),
        "args": [fwd(MCP_SERVER)],
        "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    }


def install_mcp(py, dry, quiet=False):
    head("MCP（可选层 · 工具定义常驻约 1.8k 字符）")
    entry = mcp_entry(py)

    data = {}
    if os.path.isfile(MCP_JSON):
        raw = read_text(MCP_JSON).strip()
        if raw:
            try:
                data = json.loads(raw)
            except Exception as exc:
                bad(f"{fwd(MCP_JSON)} 不是合法 JSON（{exc}）—— 为安全起见不动它")
                info("请先手工修好这个文件，或删掉它再重跑")
                return False
    if not isinstance(data, dict):
        bad(f"{fwd(MCP_JSON)} 顶层不是对象 —— 不动它")
        return False

    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        bad(f"{fwd(MCP_JSON)} 里的 mcpServers 不是对象 —— 不动它")
        return False

    if servers.get(MCP_KEY) == entry:
        ok(f"已注册 → {fwd(MCP_JSON)}")
        if not quiet:
            info("注意：新条目不会自动生效，需在连接器管理页右上角「自定义连接器」里点「信任」")
        return True

    existing_others = [k for k in servers if k != MCP_KEY]
    if existing_others and not quiet:
        info(f"已有 {len(existing_others)} 个 MCP 会保留：{', '.join(existing_others)}")

    if dry:
        info(f"[dry-run] 将在 mcpServers 写入 '{MCP_KEY}'：{json.dumps(entry, ensure_ascii=False)}")
        return True

    b = backup(MCP_JSON)
    if b:
        info(f"旧配置已备份 → {os.path.basename(b)}")
    servers[MCP_KEY] = entry
    try:
        write_text(MCP_JSON, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    except Exception as exc:
        bad(f"写入失败 —— {exc}")
        return False
    ok(f"已注册 → {fwd(MCP_JSON)}")
    if not quiet:
        info("新 MCP 不会自动生效：打开「连接器 → 自定义连接器」，对 ytdlp-video-downloader 点「信任」")
    return True


def uninstall_mcp(dry):
    if not os.path.isfile(MCP_JSON):
        info("没有 mcp.json，跳过")
        return
    try:
        data = json.loads(read_text(MCP_JSON).strip() or "{}")
    except Exception as exc:
        bad(f"mcp.json 解析失败（{exc}）—— 不动它")
        return
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or MCP_KEY not in servers:
        info(f"mcp.json 里没有 '{MCP_KEY}'，跳过")
        return
    if dry:
        info(f"[dry-run] 将从 mcp.json 移除 '{MCP_KEY}'")
        return
    b = backup(MCP_JSON)
    if b:
        info(f"备份 → {os.path.basename(b)}")
    servers.pop(MCP_KEY, None)
    write_text(MCP_JSON, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    ok(f"已从 mcp.json 移除 '{MCP_KEY}'")


# ---------------------------------------------------------------- Codex AGENTS.md


def agents_block(py):
    return f"""{MARK_BEGIN}
## 视频下载（本机已装）

要给用户下载视频时，直接跑这条命令（不要自己写下载逻辑，不要开浏览器插件）：

```bash
"{fwd(py)}" "{fwd(VDL)}" "<视频链接>" --quality 720 --json
```

- stdout 只有一个 JSON 对象：先看 `ok`，成功则把 `file_path` 给用户，`resolution` 用来确认清晰度。
- 默认 720p MP4 / H.264，横屏竖屏都能选对档位。
- 支持 YouTube / 抖音 / B站 / TikTok / X 等上千站点，粘贴分享文案（含中文）也能识别出链接。
- 只要音频加 `--audio`；先探清晰度用 `--info --json`；下整个合集加 `--playlist`。
- 失败时读 `error` / `error_code` / `hint`；抖音类报 `need_cookies` 说明要先在浏览器登录并同步 Cookie。
- 完整说明：`{fwd(REPO)}/docs/API.md`；本仓库还有 `AGENTS.md` 与 `README.md`。
{MARK_END}"""


def install_agents(py, dry):
    head("Codex AGENTS.md（全局兜底指针）")
    content = read_text(CODEX_AGENTS) if os.path.isfile(CODEX_AGENTS) else ""
    block = agents_block(py)

    if MARK_BEGIN in content and MARK_END in content:
        new = re.sub(re.escape(MARK_BEGIN) + r".*?" + re.escape(MARK_END), block, content, flags=re.S)
    else:
        sep = "" if (not content or content.endswith("\n\n")) else ("\n" if content.endswith("\n") else "\n\n")
        new = (content + sep + block + "\n") if content else (block + "\n")

    if new == content:
        ok(f"已是最新 → {fwd(CODEX_AGENTS)}")
        return True
    if dry:
        info(f"[dry-run] 将{'更新' if content else '新建'} {fwd(CODEX_AGENTS)}")
        return True
    try:
        if content:
            b = backup(CODEX_AGENTS)
            if b:
                info(f"旧文件已备份 → {os.path.basename(b)}")
        write_text(CODEX_AGENTS, new)
        ok(f"{'更新' if content else '写入'} → {fwd(CODEX_AGENTS)}")
        return True
    except Exception as exc:
        bad(f"写入失败 —— {exc}")
        return False


def uninstall_agents(dry):
    if not os.path.isfile(CODEX_AGENTS):
        info("没有 ~/.codex/AGENTS.md，跳过")
        return
    content = read_text(CODEX_AGENTS)
    if MARK_BEGIN not in content:
        info("AGENTS.md 里没有我们的标记块，跳过")
        return
    if dry:
        info("[dry-run] 将从 ~/.codex/AGENTS.md 移除标记块")
        return
    b = backup(CODEX_AGENTS)
    if b:
        info(f"备份 → {os.path.basename(b)}")
    new = re.sub(re.escape(MARK_BEGIN) + r".*?" + re.escape(MARK_END) + r"\n?", "", content, flags=re.S)
    write_text(CODEX_AGENTS, new.strip() + ("\n" if new.strip() else ""))
    ok("已移除标记块")


# ---------------------------------------------------------------- 状态 / 卸载


def status():
    head("当前安装状态")
    for name, path in SKILL_TARGETS.items():
        if os.path.isfile(path):
            txt = read_text(path)
            is_tpl = "{{" in txt
            mark = f"{C.Y}模板未渲染{C.X}" if is_tpl else f"{len(txt)} 字符"
            ok(f"Skill/{name}  {mark}  → {fwd(path)}")
        else:
            info(f"Skill/{name}  未安装  → {fwd(path)}")

    if os.path.isfile(MCP_JSON):
        try:
            data = json.loads(read_text(MCP_JSON).strip() or "{}")
            servers = data.get("mcpServers") or {}
            if MCP_KEY in servers:
                ok(f"MCP  {MCP_KEY} 已注册（共 {len(servers)} 个）  → {fwd(MCP_JSON)}")
            else:
                info(f"MCP  未注册（mcp.json 存在，{len(servers)} 个别的）")
        except Exception as exc:
            warn(f"MCP  mcp.json 解析失败：{exc}")
    else:
        info(f"MCP  无 mcp.json → {fwd(MCP_JSON)}")

    if os.path.isfile(CODEX_AGENTS) and MARK_BEGIN in read_text(CODEX_AGENTS):
        ok(f"Codex AGENTS.md 有标记块 → {fwd(CODEX_AGENTS)}")
    else:
        info("Codex AGENTS.md 未安装")

    head("内核体检")
    info(f"仓库      {fwd(REPO)}")
    info(f"CLI       {fwd(VDL)}  {'存在' if os.path.isfile(VDL) else '缺失！'}")
    info(f"MCP 服务  {fwd(MCP_SERVER)}  {'存在' if os.path.isfile(MCP_SERVER) else '缺失'}")
    info(f"模板      {fwd(TEMPLATE)}  {'存在' if os.path.isfile(TEMPLATE) else '缺失！'}")
    try:
        sys.path.insert(0, os.path.join(REPO, "yt-dlp-server"))
        import download_server as ds

        y = ds.find_ytdlp()
        info(f"yt-dlp    {' '.join(y) if y else '未找到（跑 python setup.py）'}")
        info(f"代理模式  {ds.PROXY_MODE}（系统代理 {ds.SYSTEM_PROXY or '无'}）")
    except Exception as exc:
        warn(f"内核不可用：{exc}")


def uninstall(targets, dry):
    head("卸载")
    for name in targets:
        dst = SKILL_TARGETS[name]
        if not os.path.isfile(dst):
            info(f"Skill/{name}: 没装，跳过")
            continue
        txt = read_text(dst)
        if "{{" in txt or f"name: {SKILL_NAME}" not in txt:
            bad(f"Skill/{name}: {fwd(dst)} 看起来不是我们装的，拒绝删除")
            continue
        if dry:
            info(f"[dry-run] 将删除 {fwd(dst)}")
            continue
        os.remove(dst)
        ok(f"Skill/{name}: 已删除 {fwd(dst)}")
        parent = os.path.dirname(dst)
        try:
            if not os.listdir(parent):
                os.rmdir(parent)
                info(f"空目录已清理 {fwd(parent)}")
        except OSError:
            pass
    uninstall_mcp(dry)
    uninstall_agents(dry)


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(
        description="一条命令把「视频下载」接入 AI（CLI 内核 + Skill 接入层 + 可选 MCP）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--skill", action="store_true", help="装 Skill（默认动作）")
    ap.add_argument("--mcp", action="store_true", help="额外注册 MCP 到 ~/.workbuddy/mcp.json")
    ap.add_argument("--agents", action="store_true", help="额外写 ~/.codex/AGENTS.md")
    ap.add_argument("--all", action="store_true", help="Skill + MCP + AGENTS 全装")
    ap.add_argument("--target", default="workbuddy", choices=["workbuddy", "claude", "both"],
                    help="Skill 装到哪个客户端目录（默认 workbuddy）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不落盘")
    ap.add_argument("--status", action="store_true", help="查看当前安装状态")
    ap.add_argument("--uninstall", action="store_true", help="卸载")
    ap.add_argument("--quiet", action="store_true", help="少说话（给脚本调用）")
    args = ap.parse_args()

    targets = {"workbuddy": ["workbuddy"], "claude": ["claude"], "both": ["workbuddy", "claude"]}[args.target]

    if args.status:
        status()
        return 0

    print(f"{C.B}视频下载 → AI 接入安装器{C.X}   {C.D}仓库：{fwd(REPO)}{C.X}")
    if args.dry_run:
        print(f"{C.Y}== dry-run，不会改任何文件 =={C.X}")

    if args.uninstall:
        uninstall(targets, args.dry_run)
        print(f"\n{C.G}卸载完成。{C.X}" if not args.dry_run else "\n[dry-run 结束]")
        return 0

    py = pick_python()
    print(f"{C.D}使用解释器：{fwd(py)}{C.X}")

    do_mcp = args.mcp or args.all
    do_agents = args.agents or args.all

    failed = []
    try:
        install_skill(targets, py, args.dry_run)
    except Exception as exc:
        bad(f"Skill 安装失败：{exc}")
        failed.append("skill")

    if do_mcp:
        if not install_mcp(py, args.dry_run, quiet=args.quiet):
            failed.append("mcp")

    if do_agents:
        if not install_agents(py, args.dry_run):
            failed.append("agents")

    # 顺手做个内核自检
    if not args.dry_run:
        head("自检")
        try:
            sys.path.insert(0, os.path.join(REPO, "yt-dlp-server"))
            import download_server as ds

            y = ds.find_ytdlp()
            if y:
                ok(f"yt-dlp 可用：{' '.join(y)}")
            else:
                warn("没找到 yt-dlp —— 跑一次 python setup.py")
            if os.path.isfile(os.path.join(REPO, "yt-dlp-server", "config.json")):
                ok("config.json 存在")
            else:
                warn("没有 config.json —— 跑一次 python setup.py（会生成默认配置）")
        except Exception as exc:
            warn(f"内核自检跳过：{exc}")

    # 收尾指引
    head("怎么用")
    ex = f'"{fwd(py)}" "{fwd(VDL)}" "https://www.youtube.com/watch?v=jXwOcpkMQAA" --json'
    if args.dry_run:
        info(ex)
    else:
        print(f"  {C.D}① 直接命令行（最省）{C.X}")
        print(f"     {ex}")
        print(f"  {C.D}② 交给 AI：把链接发过来直接说「下载这个」{C.X}")
        print(f"     Skill 已就位，AI 会自己读 {fwd(SKILL_TARGETS[targets[0]])}")
        if do_mcp:
            print(f"  {C.D}③ MCP（若已装）{C.X}")
            print(f"     记得去「连接器 → 自定义连接器」点「信任」才生效")

    if failed:
        print(f"\n{C.R}有 {len(failed)} 项失败：{', '.join(failed)}{C.X}")
        return 1
    print(f"\n{C.G}完成。{C.X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
