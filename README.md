# yt-dlp 视频下载助手

基于 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 的视频下载工具，四层入口共用同一个下载内核：
**浏览器插件**（点着用）、**命令行 `vdl.py`**（一行命令）、**MCP 服务器**（给 AI 直接调用）、
**Skill / AGENTS.md**（让 AI 知道怎么用）。

> **v3.0 重点更新（给 AI 用 + 修两个硬伤）**
> 1. **一行命令下载**：`python vdl.py "<链接>" --quality 720 --json` —— AI 助手（WorkBuddy / Codex）
>    拿到链接即可直接下载，stdout 输出稳定的 JSON 契约，不用去碰插件；
> 2. **零依赖 MCP 服务器**：`mcp_server.py`，暴露 `download_video` / `preview_video` /
>    `list_downloads` / `get_task_status` / `open_folder`，纯标准库实现，不需要 `pip install mcp`；
> 3. **修掉「竖屏视频选错分辨率」**：以前用 `-f "[height<=720]"` 表达 720p，
>    竖屏视频（**抖音** / YouTube Shorts）的 720p 档是 `720x1280`，会被命中成 `360x640`。
>    改用 yt-dlp 的 `-S res:720`（按**短边**最接近排序），横屏 → `1280x720`、
>    竖屏 → `720x1280`，实测两种画幅都正确；
> 4. **修掉「代理帮倒忙」**：以前无条件把系统代理塞给 yt-dlp。在 Clash **TUN 模式**的机器上
>    属于二次代理，反而把 `youtube.com` / `x.com` 打死（TikTok 却必须走代理）。
>    现在默认 `auto`：**先直连，遇到网络/超时类失败再自动退回系统代理**（结果里 `attempts=2`）；
> 5. 新增 `setup.py` 一键初始化自检（Python / yt-dlp / ffmpeg / 配置 / 联网），以及
>    `docs/API.md` 完整接口文档。

> **v2.5 重点更新（体验优化）**
> 1. **按次指定保存文件夹**：弹窗「保存位置」可随便填/浏览选择，只对本次下载生效；想记住再勾选「同时设为默认」，并记住最近用过的 6 个文件夹；
> 2. **下载进度随时可见**：工具栏图标角标显示实时百分比 → 弹窗汇总条 → **「📊 任务中心」标签页**（关掉弹窗也能一直看，1 秒刷新）；页面右下角悬浮按钮也会变成 `⏬ 45%`；
> 3. **一键定位文件**：完成通知点一下、任务卡片/任务中心的「🎯 定位文件」都会打开资源管理器并**选中该文件**（旧任务也能用）。

> **v2.4 重点更新**
> 1. **抖音解析修复**：抖音需要"新鲜 Cookie"才能解析，插件现在会**自动把浏览器里的登录态 Cookie 同步给本地 yt-dlp**（抖音 / TikTok / B站），不用再手工导出 `cookies.txt`；
> 2. **真正的"一键启动"**：双击一次 `install_host.bat` 即可（**自动识别扩展 ID，无需手工复制**），之后打开插件弹窗时**服务会自动被拉起**，并且 Windows 登录后服务自动运行，不用再先开程序；
> 3. 修复中文标题乱码、下载文件名识别、清晰度选择等一批解析问题（详见文末「本次修复清单」）。

## 目录结构

```
E:\AI软件\视频下载\
├── vdl.py                          # ★ 命令行 / AI 入口（一行命令下载，返回 JSON）
├── install_ai.py                   # ★ 一条命令把上面这层接入 AI（Skill / MCP / AGENTS.md）
├── mcp_server.py                   # ★ MCP stdio 服务器（零依赖，给 AI 当工具用，可选）
├── setup.py                        # ★ 新机器一键初始化 + 自检
├── AGENTS.md                       # ★ 给 Codex 等编码 AI 看的操作说明（仓库内自动生效）
├── skills\video-download\SKILL.md.in  # ★ Skill 模板（含占位符，由 install_ai.py 渲染后安装）
├── docs\API.md                     # ★ 接口说明（CLI / MCP / HTTP / 配置 / 排错）
├── yt-dlp\                        # yt-dlp 官方源码（git clone，不入库）
├── yt-dlp-server\
│   ├── download_server.py         # 下载内核 + 本地 HTTP 服务（纯标准库，零依赖）
│   ├── native_host.py             # Native Messaging Host 源码（一键启动 + 服务本体）
│   ├── ytdlp_host.exe             # 打包好的启动器/服务（不入库，用 build_host.ps1 生成）
│   ├── build_host.ps1             # 重新打包 ytdlp_host.exe
│   ├── test_launcher.py           # 一键启动协议自检
│   ├── start_hidden.vbs           # 静默启动（备用）
│   ├── cookies\                   # 插件自动同步的站点 Cookie（每站点一个文件）
│   └── config.json / history.json # 配置 / 任务历史
├── yt-dlp-chrome-extension\       # Chrome 插件（Manifest V3）
│   ├── manifest.json
│   ├── background.js              # 自动拉起服务 / Cookie 同步 / 进度角标 + 通知
│   ├── content.js                 # 页面视频识别 / 悬浮按钮（带进度）/ 直链提取
│   ├── popup.html / popup.js      # 弹窗：预览 / 清晰度 / 保存位置 / 任务列表
│   └── tasks.html / tasks.js      # 📊 任务中心（常驻标签页进度面板）
│   └── icons\
├── tools\
│   ├── e2e_test.py                # 真实案例端到端测试（Chrome + 插件 + 抖音）
│   └── mcp_selftest.py            # MCP 协议自测（initialize / tools/list / tools/call）
├── downloads\                     # 视频下载目录
├── install_host.bat               # 【一键安装】插件启动服务 + 开机自启
└── start-server.bat               # 手动启动服务（备用）
```

## 给 AI / 命令行用（v3.0 新增）

不想开浏览器、或者想让 AI 助手直接帮你下，用这一层。

### 一行命令

```bash
python vdl.py "<视频链接>" --quality 720 --json
```

默认就是 **720p MP4（优先 H.264）**，`--quality` 可省略。加 `--json` 后 stdout 只有一个
JSON 对象（进度/日志走 stderr），AI 直接取 `file_path` 就行：

```json
{"ok": true, "title": "What's The Most Expensive Thing Ever?",
 "file_path": "E:\\AI软件\\视频下载\\downloads\\What's ... [jXwOcpkMQAA].mp4",
 "size_mb": 128.1, "resolution": "1280x720", "duration_sec": 556,
 "extractor": "youtube", "cookies_used": null, "proxy_used": null, "attempts": 1}
```

常用变体：

| 需求 | 命令 |
|---|---|
| 1080p | `python vdl.py "<url>" --quality 1080 --json` |
| 只要音频 MP3 | `python vdl.py "<url>" --audio --json` |
| 顺便下字幕 | `python vdl.py "<url>" --subs --json` |
| 先看看是什么视频 | `python vdl.py "<url>" --info --json` |
| 存到指定目录 | `python vdl.py "<url>" --dir "D:\Videos" --json` |
| 抖音分享文案直接粘 | `python vdl.py "9.92 复制打开抖音… https://v.douyin.com/xxxx/ …"` |

### 一条命令接入 AI（推荐）

```bash
python install_ai.py          # 装 Skill（默认，最省）
python install_ai.py --all    # 顺带注册 MCP + 写 ~/.codex/AGENTS.md
```

跑完这一步，以后直接把链接丢给 AI 说「下载这个 720p」就行，不用再交代任何背景。

### 该选哪个：CLI / Skill / MCP ？

三者不是三选一，而是**一个内核 + 三层入口**。`download_server.py` 是唯一内核，
`vdl.py` 直接 import 它，`mcp_server.py` 调 `vdl.py`——逻辑永远只有一份。

| | 上下文开销 | 什么时候用 |
|---|---|---|
| **CLI**（`vdl.py`） | **0** | 内核。自己敲、脚本调、AI 直接跑，都走它 |
| **Skill**（`install_ai.py` 默认） | 描述 **~180 字符常驻**，正文 3.9k 字符**按需加载** | ★ 最省。想让 AI「看到链接就知道怎么办」就装它 |
| **MCP**（`--mcp`） | 工具定义 **~1.8k 字符全量常驻**，每次请求都带 | 跨客户端要标准化工具形态、或不想让 AI 读文档时 |

**结论：Skill 是性价比最高的接入方式，开销约为 MCP 的 1/10。** MCP 不是必须的——
它唯一的优势是「客户端自带工具面板、AI 不用读说明也能调」，代价是那 1.8k 字符的
schema 会进入**每一次**请求。所以默认只装 Skill，MCP 用 `--mcp` 显式打开。

装完 MCP 后**不会自动生效**：去「连接器 → 自定义连接器」，对 `ytdlp-video-downloader`
点一下**信任**。

### 其它客户端

```bash
python install_ai.py --target claude   # 装到 ~/.claude/skills/
python install_ai.py --target both     # workbuddy + claude 都装
python install_ai.py --agents          # 只写 ~/.codex/AGENTS.md（Codex 全局兜底）
python install_ai.py --status          # 看现在装了什么
python install_ai.py --uninstall       # 卸载（只删自己装的，陌生文件会拒绝）
```

换电脑或挪了目录，**重新跑一次 `install_ai.py` 即可**——所有绝对路径会被刷新。

### 新机器初始化

```bash
git clone <本仓库> "E:\AI软件\视频下载"
cd "E:\AI软件\视频下载"
python setup.py --test        # 检查 Python / yt-dlp / ffmpeg / 配置，并联网验证一次
python install_ai.py --all    # 接入 AI
```

## 工作原理

```
Chrome 插件 ──(页面 URL + 浏览器 Cookie)──▶ 本地服务 127.0.0.1:8787 ──▶ yt-dlp ──▶ downloads\
```

* 插件不下载视频，只负责把视频地址（以及需要的登录态 Cookie）交给本地服务，由 yt-dlp 完成实际下载（支持上千个站点：YouTube、B站、**抖音**、TikTok、Twitter/X、微博等）。
* 服务未启动时，插件会通过 **Native Messaging** 自动把服务拉起来——不需要你先手动打开程序。

## 使用方法

### 1. 一次性安装（只需做一次）

1. 打开 `chrome://extensions` → 右上角打开**开发者模式** → **加载已解压的扩展程序** → 选择 `E:\AI软件\视频下载\yt-dlp-chrome-extension`
2. 双击 `install_host.bat`（无需输入任何东西，脚本会**自动识别扩展 ID**，同时注册 Chrome/Edge 并设置开机自启）
3. 看到 `安装完成！` 即可。以后：
   * 打开插件弹窗时，服务会**自动启动**（也可以点弹窗里的 **「⚡ 启动服务」**）；
   * Windows 登录后服务也会自动运行，不用再手动点任何 bat。

> 若以后扩展 ID 变了（例如换了文件夹重新加载），重跑一次 `install_host.bat` 即可。
> 卸载：`install_host.bat --uninstall`。

### 2. 三种使用方式

**方式 A：弹窗下载（推荐）** — 点击工具栏红色图标
- 自动填入当前页面链接，**自动解析视频信息**：标题 / 频道 / 时长 / 各清晰度（含编码、水印提示）与预计大小
- **保存位置按次选择**：直接改路径、点「浏览…」挑文件夹，或从最近目录下拉选；不勾选「同时设为默认」时**只影响这次下载**
- 抖音/TikTok/B站会**自动同步浏览器 Cookie**（弹窗会提示"🍪 已同步… 35 条"）
- 选择清晰度 → 点击下载，随后进度会显示在**图标角标**上，随时可点「📊 任务中心」看详细进度
- 下载完成后，任务卡片上点 **「🎯 定位文件」** 直接打开资源管理器并选中该文件

**方式 B：页面悬浮按钮** — 打开任意视频页面，右下角出现「⬇ yt-dlp」按钮，一键加入下载队列；下载中按钮会实时显示 `⏬ 45% · 3.2MiB/s`

**方式 C：右键菜单** — 在视频/链接上右键 →「用 yt-dlp 下载此视频」

**看进度 / 找文件**
- 工具栏图标角标：`45%`（下载中）/ `2↓`（多个任务）/ `✓`（完成）/ `!`（失败）
- **📊 任务中心**（弹窗底部按钮，或点击完成通知里的「任务中心」）：常驻标签页，1 秒刷新，显示总进度、速度、ETA、每个任务的保存路径
- 完成通知**点一下**＝在资源管理器中选中刚下好的文件；任务卡片/任务中心里的「📂 打开文件夹」＝打开所在目录

> 抖音分享文案（如 `9.92 复制打开抖音… https://v.douyin.com/xxxx/ …`）可以直接整段粘贴到弹窗链接框，插件会自动提取其中的链接。

## 功能特性

| 功能 | 说明 |
|------|------|
| 下载前预览 | 标题/频道/时长/分辨率列表/预计大小，确认目标视频后再下载 |
| 按次选保存文件夹 | 手输 / 浏览 / 最近目录；默认只影响本次下载，勾选后才设为默认 |
| 进度随时可见 | 图标角标百分比 + 弹窗汇总条 + 📊 任务中心页 + 页面按钮 `⏬ 45%` |
| 一键定位文件 | 完成通知点击、任务卡片「🎯 定位文件」→ 资源管理器中选中文件；「📂 打开文件夹」→ 打开目录 |
| 抖音支持 | 自动同步浏览器 Cookie，`v.douyin.com` 短链/分享文案直接解析；同时识别水印版本并默认避开 |
| 精确清晰度 | 预览列表精确到"某个格式"（如 `1080p · H.264`、`720p · HEVC`），下载时按格式 ID 拉取，避免 yt-dlp 自己挑错 |
| Cookie 自动同步 | 浏览器登录态（含 httpOnly）自动写入 `yt-dlp-server\cookies\<站点>.txt`，抖音/B站大会员/TikTok 免手工导出 |
| 一键启动服务 | 弹窗自动/手动拉起本地服务（Native Messaging），Windows 开机自启 |
| 自动走代理 | 自动读取 Windows 系统代理（Clash 等），TikTok/YouTube 等被墙站点无需手动配置 |
| 页面多视频选择 | 弹窗列出页面所有视频，点击选择并在网页中红框高亮定位 |
| 广告识别 | 检测 YouTube 广告播放状态，明确告知"下载的是视频本体非广告" |
| 默认策略 | 默认 **MP4 容器 + 720p 优先**（无 720 自动回退），不默认最高画质 |
| 实时速度 | 任务卡片显示 ⚡ 下载速度 + 剩余时间 ETA |
| 任务管理 | 队列排队、进度条、失败原因与中文修复提示、一键重试 |
| 历史记录 | 服务重启后任务历史不丢失（history.json） |
| 完成通知 | 系统通知（可点开文件位置）+「任务中心」按钮 |
| 格式转换 | MP3 音频提取（适合音乐/播客） |

## 常见问题

- **弹窗显示「本地服务未启动」**：正常情况下会自动启动。若提示"启动器未安装"，双击 `install_host.bat` 一次即可（自动识别扩展 ID）。
- **抖音解析失败 / 提示需要 Cookie**：插件依赖浏览器里已有的抖音 Cookie。请先在浏览器打开一次抖音视频页（浏览器会自动生成 `ttwid`、`s_v_web_id` 等），再回到插件点「🔍 解析视频信息」；必要时点弹窗的 **「🍪 同步」** 手动同步。
- **抖音 720p 是 HEVC(H.265) 播不了**：抖音的 720p 一般是 H.265（体积小），部分播放器需要额外解码器。弹窗里选 **`576p · H.264`** 即可获得通用兼容版本；H.264 版本体积通常更大。
- **B站 1080P 以上清晰度失败**：需要登录 Cookie，插件会自动同步（前提是浏览器已登录 B站）。
- **TikTok 下载失败**：先在浏览器打开一次目标视频页让 Cookie 生效，再解析；必要时使用「页面视频」列表里的直链。
- **YouTube 不显示文件大小**：YouTube 流式格式不给精确大小，服务会按码率估算并显示"约 xx MB"。
- **保存位置怎么用**：弹窗里直接改路径 / 点「浏览…」挑文件夹 / 从最近目录下拉选，**默认只对这次下载生效**（标签显示"本次下载"）；想以后都用它，勾选「同时设为默认」（标签会变成"默认位置"）。
- **下载中在哪里看进度**：① 工具栏图标角标百分比 ② 弹窗顶部汇总条 + 任务卡片 ③ 「📊 任务中心」常驻页面 ④ 视频页右下角悬浮按钮 `⏬ 45%`。
- **怎么快速找到下载好的文件**：完成通知**点一下**＝在资源管理器里选中该文件；弹窗/任务中心的「🎯 定位文件」同理；「📂 打开文件夹」只打开目录。
- **修改端口/下载目录**：环境变量 `YTDLP_SERVER_PORT` / `YTDLP_DOWNLOAD_DIR`；默认目录也可在弹窗里勾选「同时设为默认」写入 `config.json`（优先于环境变量）。
- **想让 yt-dlp 直接读浏览器 Cookie**（Chrome 127+ 的 DPAPI 加密通常读不了）：在 `config.json` 里加 `"cookies_from_browser": "firefox"` 等（插件同步 Cookie 是更推荐的方式）。
- **服务日志**：`yt-dlp-server\server.log`（服务）与 `yt-dlp-server\launcher.log`（启动器）。
- **服务安全**：仅绑定 127.0.0.1，不暴露到局域网。

## 修复清单（v2.4 / v2.5 / v3.0）

| 问题 | 原因 | 修复 |
|------|------|------|
| **竖屏视频选错分辨率**：抖音/Shorts 要 720p 却下到 360p | 用 `-f "[height<=720]"` 表达 720p，而竖屏视频的 720p 档是 `720x1280`（`height=1280`），`height<=720` 只能命中 `360x640` | 改用 yt-dlp 的 `-S res:N` 按**短边**最接近排序；实测横屏→`1280x720`、竖屏→`720x1280` 均正确（`resolve_format()`） |
| **代理帮倒忙**：YouTube/X 直连正常，套上系统代理反而 SSL 中断 | 无条件把系统代理写进 yt-dlp 子进程环境；Clash TUN 模式下属于二次代理（而 TikTok 又必须走代理） | 默认 `auto`：先直连，遇 `network`/`timeout` 类失败自动退回系统代理重试（`proxy_candidates()`，结果里 `attempts=2`）；可用 `--proxy none/system/<url>` 强制 |
| 环境变量里被注入的代理污染下载（IDE/沙箱常注入临时端口） | 之前会把 `http_proxy` 当作系统代理 | Windows 只认注册表里的系统代理；且决定直连时会把继承来的 `http_proxy/https_proxy/ALL_PROXY` **清掉** |
| 下载失败时错误信息是空的，无法归因 | yt-dlp 有时只打一行空的 `ERROR:`，真正原因在相邻行 | 增加「最近非进度行」环形缓冲做归因兜底，再交给错误分类器 |
| 结果里标题带 ` [视频ID]` 后缀、没有时长/来源 | 只从输出里抓文件名 | 用 `--print after_move:...` 让 yt-dlp 回吐精确元数据（标题/时长/来源/分辨率），`--progress` 保证进度条不被静默 |
| AI 无法直接下载（只能靠人点插件） | 缺少命令行入口与接口文档 | 新增 `vdl.py`（JSON 契约）+ `mcp_server.py`（零依赖 MCP）+ Skill / `AGENTS.md` + `docs/API.md` |
| 抖音解析报 `Fresh cookies (not necessarily logged in) are needed` | 抖音 API 要求浏览器 Cookie，而服务只支持手工放 `cookies.txt` | 插件用 `chrome.cookies` 读取浏览器 Cookie（含 httpOnly）→ Netscape 格式 → `POST /api/cookies`，服务按域名自动匹配使用 |
| 不能指定下载文件夹 | 弹窗的「浏览」直接改全局默认目录，没有"仅本次"的概念 | 保存位置改为**按次生效**（手输/浏览/最近目录），勾选「同时设为默认」才写回全局；下载提示里回显目标路径 |
| 下载中看不到进度 | 弹窗一关进度就没了 | 后台全局轮询 → **图标角标百分比**、**📊 任务中心页**（常驻、1 秒刷新）、页面悬浮按钮 `⏬ 45%`、1 秒刷新的弹窗汇总条 |
| 找不到下载好的文件 | 只能打开全局下载目录 | 任务记录新增 `file_path`；完成通知点击、任务卡片/任务中心的「🎯 定位文件」用 `explorer /select` **选中该文件**，另有「📂 打开文件夹」 |
| 抖音报错看不懂 | 直接把 yt-dlp 英文报错抛给用户 | 服务端错误分类（`need_cookies/timeout/network/forbidden/...`）+ 中文提示 + 针对性的处理建议 |
| 下载完中文标题变 `���ٷ�` | Windows 下 yt-dlp 用 GBK 写管道，服务按 UTF-8 解码 | 子进程强制 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`，并兜底扫描目录确认文件名 |
| 任务显示已完成但没有文件名 | 只从 yt-dlp 输出里抓 `Destination:` | 结束后按时间戳兜底查找产物文件；兼容"已下载过"提示 |
| 清晰度选择偶发挑错格式 | 用高度表达式 `bv*[height<=720]+ba` 让 yt-dlp 自己挑 | 预览返回精确 `format_id`，下载走 `-f "<id>+ba/<id>"`，所见即所得 |
| 抖音默认下到带水印版本 | `download_addr-*` 是水印版 | 默认链路排除 `format_id^=download_addr`，预览中标注"含水印" |
| 每次用插件都要先手动开服务 | 需要手工把扩展 ID 粘贴进 `install_host.bat`，很多人没装 | 安装脚本自动从 Chrome/Edge 配置反查扩展 ID 并注册；插件在服务未启动时自动拉起（还带 `/health` 兜底）；登录后服务自启 |
| 启动器残留进程 | PyInstaller 单文件 + 管道句柄导致读取阻塞 | 应答后确定性退出（`os._exit`），空闲超时兜底，自检脚本断言 0 残留 |
| 分享文案粘贴无法解析 | 弹窗只接受纯 URL | 弹窗与服务端都会从分享文案里提取链接 |
| 任意网页可调本地服务 | CORS 允许 `*` | 服务端校验 `Origin`，只接受浏览器扩展来源（网页脚本调用返回 403） |

## 技术要点

- **四层入口一个内核**：插件（HTTP）/ `vdl.py`（import）/ `mcp_server.py`（子进程调 CLI）全部复用
  `yt-dlp-server/download_server.py`，格式选择、Cookie、代理、错误分类只有一份实现
- 插件 Manifest V3，权限：`activeTab` + 右键菜单 + 通知 + storage + `cookies` + `nativeMessaging` + `alarms` + 站点 host 权限
- 本地服务为 Python 标准库（`http.server`），无第三方依赖；预览带 10 分钟缓存、下载并发上限 2
- `vdl.py` / `mcp_server.py` / `setup.py` 同样**零第三方依赖**，用任意 Python 3.8+ 即可跑
- **分辨率选择**用 yt-dlp 的 `-S res:N` 排序（按短边最接近），H.264 优先，兼容横竖屏
- **代理**按「先直连 → 失败退回系统代理」决策，不预先猜（`proxy_candidates()`）
- Cookie 每站点独立文件（`cookies/<域名>.txt`），每次运行使用私有副本，避免 yt-dlp 回写覆盖同步数据
- 后台 Service Worker 全局轮询（下载中 1.2s / 空闲 8s，另有 30s `chrome.alarms` 兜底）驱动图标角标与完成通知
- `ytdlp_host.exe` 由 PyInstaller 打包（内含服务代码），既可作 Native Messaging Host，也可 `--serve` 直接跑服务，**不依赖系统 Python**
- 自检：
  - `python setup.py --test`（环境 + 联网）
  - `python tools\mcp_selftest.py`（MCP 协议：initialize / tools/list / tools/call）
  - `python yt-dlp-server\test_launcher.py`（一键启动协议）
  - `python tools\e2e_test.py`（真实 Chrome + 插件 + 抖音端到端，覆盖按次目录/进度角标/任务中心/定位文件）

