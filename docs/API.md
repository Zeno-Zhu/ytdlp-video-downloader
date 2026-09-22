# 接口说明（API Reference）

本仓库对外的三个入口，**共用同一套下载内核**（`yt-dlp-server/download_server.py`），
所以格式选择、Cookie 匹配、代理策略、错误分类的行为完全一致。

```
                     ┌─────────────────────────────────────┐
 浏览器插件 ──HTTP──▶ │                                     │
 vdl.py CLI ──import─▶│   download_server.py（下载内核）     │──▶ yt-dlp ──▶ downloads\
 MCP 客户端 ──CLI───▶ │                                     │
                     └─────────────────────────────────────┘
```

| 入口 | 文件 | 适合谁 |
|---|---|---|
| **CLI** | `vdl.py` | 命令行 / 脚本 / AI 助手（Codex、WorkBuddy 直接跑命令） |
| **MCP** | `mcp_server.py` | 支持 MCP 的客户端（Claude Desktop、Cursor、Codex…） |
| **HTTP** | `yt-dlp-server/download_server.py` | 浏览器插件（也可以自己写脚本调） |

---

## 0. 该选哪个入口 / 怎么装（先读这一节）

### 0.1 上下文开销对比

三个入口不是三选一，而是**一个内核 + 三层入口**。差别在于「AI 要为你付出多少常驻上下文」：

| 入口 | 常驻上下文 | 何时加载正文 | 结论 |
|---|---|---|---|
| CLI | **0** | — | 内核。任何 AI 只要能跑命令就能用 |
| Skill | **~180 字符**（仅 frontmatter 的 description） | 用户真的要下视频时，才读 3.9k 字符正文 | ★ **最省** |
| MCP | **~1.8k 字符**（工具名 + 描述 + inputSchema 全量） | 常驻，**每次请求都带** | 仅在需要跨客户端标准化时开 |

> 测法：Skill 只有 `name` + `description` 会进系统提示，正文是"按需加载"；
> MCP 的 `tools/list` 结果（5 个工具的 name + description + JSON Schema）会被客户端拼进每一次请求。

**所以默认装 Skill。** MCP 的唯一优势是「AI 不用读说明、客户端自带工具面板也能调」，
代价是那 1.8k 字符长期占位——只在"跨多个客户端、且不想维护说明文档"时才值得。

### 0.2 安装（一条命令）

```bash
python install_ai.py                 # 装 Skill 到 ~/.workbuddy/skills/（默认，最省）
python install_ai.py --all           # Skill + MCP + ~/.codex/AGENTS.md
python install_ai.py --mcp           # 只额外注册 MCP 到 ~/.workbuddy/mcp.json
python install_ai.py --agents        # 只额外写 ~/.codex/AGENTS.md
python install_ai.py --target claude # 装到 ~/.claude/skills/
python install_ai.py --target both   # workbuddy + claude
python install_ai.py --dry-run       # 只打印计划，不落盘
python install_ai.py --status        # 查看当前安装状态
python install_ai.py --uninstall     # 卸载（只删自己装的，陌生文件会拒绝）
```

脚本做的事：

1. 渲染 `skills/video-download/SKILL.md.in` 模板（把 `{{PY}}` / `{{REPO}}` / `{{VDL}}`
   替换成**本机绝对路径**），写到目标 Skill 目录；
2. `--mcp`：往 `~/.workbuddy/mcp.json` 的 `mcpServers` 里**合并**写入
   `ytdlp-video-downloader` 条目（不覆盖别的 MCP，写前自动备份 `.bak-<时间戳>`）；
3. `--agents`：往 `~/.codex/AGENTS.md` 追加一段带标记的说明（幂等，重复跑不会重复追加）。

**为什么用脚本装而不是手工复制**：Skill 正文里含仓库绝对路径，而每台机器的路径不同。
`install_ai.py` 在安装时把路径**写死**进渲染结果，AI 拿到的就是可直接执行的命令，不需要自己找路。
换机器或挪目录后重跑一次即可刷新。

### 0.3 各客户端配置位置

| 客户端 | Skill 目录 | MCP 配置 |
|---|---|---|
| WorkBuddy | `~/.workbuddy/skills/<name>/SKILL.md` | `~/.workbuddy/mcp.json` |
| Claude Code | `~/.claude/skills/<name>/SKILL.md` | 各自 mcp 配置 |
| Codex | 用 `AGENTS.md`（项目根或 `~/.codex/AGENTS.md`） | 各自 mcp 配置 |

WorkBuddy 写完 `mcp.json` **不会自动生效**：打开「连接器 → 自定义连接器」，
对 `ytdlp-video-downloader` 点**信任**才启用。

### 0.4 换机器 / 让 AI 自己装（一条指令）

真实场景里通常不是你敲命令，而是把一段话丢给 AI 让它照做。直接复制
**README 顶部的「把这一行发给 AI」**那段提示词即可——里面已经写明了克隆位置、
`setup.py --test`、`install_ai.py --all`、验证命令，以及最后必须回报什么。

> 提示词只放在 README 一处，避免两处内容漂移；本文档只解释机制。

---

## 1. CLI —— `vdl.py`

### 1.1 用法

```bash
python vdl.py <url> [<url> ...] [选项]
```

| 选项 | 说明 |
|---|---|
| `-q, --quality` | `best` \| `720`(默认) \| `1080` \| `480` \| `2160` \| `mp4` \| `audio` \| `id:<格式ID>`；也接受 `720p` / `720mp4` 写法 |
| `-d, --dir` | 保存目录。绝对路径直接用；**相对路径相对「默认下载目录」**（`--dir 教程` → `<默认目录>/教程`）；显式 `./` `../` 相对当前目录；支持 `~` 与 `%VAR%`/`$VAR`；不存在自动创建。不传则读 `config.json` → `YTDLP_DOWNLOAD_DIR` → `<仓库>/downloads`。详见 §4.2 |
| `--info` | 只解析元数据，不下载 |
| `--json` | stdout 输出机器可读 JSON（AI 首选） |
| `--audio` | 仅提取音频 MP3（等价 `--quality audio`） |
| `--subs` | 额外下载字幕（含自动字幕，转 srt，需 ffmpeg） |
| `--playlist` | 允许下载整个播放列表/合集（默认只下单个视频） |
| `--referer` | 自定义 `Referer` 头（直链下载用） |
| `--proxy` | `auto`(默认) \| `none` \| `system` \| `http://127.0.0.1:7897` |
| `--name` | 文件名模板，默认 `%(title).200B [%(id)s].%(ext)s` |
| `--open` | 下载完成后在资源管理器里选中该文件 |
| `--quiet` | 静默模式（只在结束输出结果） |

### 1.2 输出契约

`--json` 时 **stdout 只有一个 JSON 对象**，进度与日志一律走 stderr。

**单个链接：** 直接就是这个对象。

```jsonc
{
  "ok": true,
  "url": "https://www.youtube.com/watch?v=XXXX",
  "title": "What's The Most Expensive Thing Ever?",
  "quality": "720mp4",
  "quality_label": "720p MP4",
  "dir": "E:\\AI软件\\视频下载\\downloads",
  "file_path": "E:\\AI软件\\视频下载\\downloads\\What's The Most Expensive Thing Ever？ [jXwOcpkMQAA].mp4",
  "filename": "What's The Most Expensive Thing Ever？ [jXwOcpkMQAA].mp4",
  "size_mb": 128.1,
  "resolution": "1280x720",
  "duration_sec": 556,
  "extractor": "youtube",
  "cookies_used": null,
  "proxy_used": null,
  "attempts": 1,
  "elapsed_sec": 20.58,
  "error": null,
  "error_code": null,
  "hint": null
}
```

**多个链接：** `{"ok": <全部成功?>, "items": [ 上面的对象, ... ]}`

**`--info` 时**额外包含：

| 字段 | 说明 |
|---|---|
| `author` | 作者/频道 |
| `duration` | 人类可读时长，如 `9:16` |
| `view_count` / `thumbnail` | 播放量 / 封面图 |
| `formats[]` | 每个格式：`format_id` `width` `height` `ext` `fps` `vcodec` `acodec` `has_audio` `watermarked` `size`(MB) `note` |
| `quality_options` | 去重后的可选清晰度列表，如 `[2160, 1440, 1080, 720, 480, 360, 240, 144]` |

> ⚠️ `multipart/*` 的 `width`/`height` 是**按视频真实方向**给的：竖屏视频是 `720x1280`
> （height > width），别按 `height` 判断"多少 p"。

### 1.3 退出码

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 下载失败或解析失败（看 `error_code`） |
| `2` | 参数错误：清晰度写错、服务目录找不到、**`--dir` 不合法（`error_code=bad_dir`）** |

### 1.4 示例

```bash
python vdl.py "https://www.youtube.com/watch?v=jXwOcpkMQAA"                    # 默认 720p
python vdl.py "https://youtu.be/XXXX" -q 1080 --json                           # 1080p + JSON
python vdl.py "https://youtu.be/XXXX" --info --json                            # 只看信息
python vdl.py "https://youtu.be/XXXX" --audio                                  # 提取 MP3
python vdl.py "9.92 复制打开抖音… https://v.douyin.com/xxxx/ …"                  # 直接粘分享文案
python vdl.py "https://youtu.be/XXXX" --dir "D:\Videos" --subs --open          # 指定目录 + 字幕 + 打开
```

---

## 2. MCP 服务器 —— `mcp_server.py`

纯标准库实现（手写 JSON-RPC 2.0 over stdio，**换行分隔**），不需要 `pip install mcp`。

启动：`python mcp_server.py`（由客户端自动拉起，不用手动跑）

### 2.1 工具

| 工具 | 参数 | 说明 |
|---|---|---|
| `download_video` | `url`(必填)、`quality`(默认 `720`)、`dir`、`audio_only`、`subs`、`wait`(默认 `true`) | 下载视频。`wait=false` 时立即返回 `task_id`。`dir` 规则同 CLI `--dir`（见 §4.2） |
| `preview_video` | `url` | 只解析元数据，不下载 |
| `list_downloads` | `limit`(默认 20)、`dir`(省略=默认下载目录) | 列出某个下载目录里的媒体文件（按时间倒序）；`dir` 规则同 §4.2 |
| `get_task_status` | `task_id` | 查后台任务状态；完成后 `result` 字段里带完整下载结果 |
| `open_folder` | `path` | 在文件管理器里定位文件 |

返回值统一是 MCP 的 `content[0].text`，内容是上面 CLI 那个 JSON 的字符串（缩进后的）。
失败时 `isError=true`。

### 2.2 客户端配置

**推荐：自动写**（会合并进现有配置，不覆盖别的 MCP，并自动备份）：

```bash
python install_ai.py --mcp
```

**手工写**（等价于上面那条命令的产物）：

```json
{
  "mcpServers": {
    "ytdlp-video-downloader": {
      "command": "C:/Users/<你>/.workbuddy/binaries/python/versions/3.13.12/python.exe",
      "args": ["E:/AI软件/视频下载/mcp_server.py"],
      "env": { "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

- `command` 一定要是**解释器绝对路径**（不要写裸 `python`，客户端不一定继承你的 PATH）。
- `args` 指向 `mcp_server.py`；它按**自身位置**找同目录的 `vdl.py`，所以仓库挪了只要改这一行。
- WorkBuddy 的配置位置是 `~/.workbuddy/mcp.json`，写完**不会自动生效**：
  打开「连接器 → 自定义连接器」，对 `ytdlp-video-downloader` 点 **信任**。
- Codex / Claude Desktop / Cursor 写各自的 mcp 配置，结构同上。
- 手动自测协议：`python tools/mcp_selftest.py`（走一遍 initialize / tools/list / tools/call）。

---

## 3. HTTP 服务 —— `download_server.py`

仅监听 `127.0.0.1:8787`（可用 `YTDLP_SERVER_PORT` 改），纯标准库。
**CORS 只接受 `chrome-extension://` 来源**，网页脚本直接调会拿到 403。

### 3.1 接口清单

| 方法 | 路径 | 请求体 / 参数 | 返回 |
|---|---|---|---|
| GET | `/health` | — | `{ok, version, yt_dlp, yt_dlp_path, download_dir, port, pid, cookies[], cookies_from_browser, proxy, proxy_mode}` |
| POST | `/api/preview` | `{url, proxy?}` | 同 CLI `--info` 的字段（`ok/title/author/duration/formats[]/…`） |
| POST | `/api/download` | `{url, format?, dir?, referer?, proxy?}` | `{ok, task_id, status, cookies_used, proxy_used}` |
| GET | `/api/tasks` | — | `{tasks:[…]}` 含历史 |
| GET | `/api/tasks/<id>` | — | 单个任务：`{status, percent, speed, eta, size_mb, filename, file_path, error, error_code, error_hint}` |
| POST | `/api/retry` | `{task_id}` | `{ok, task_id}` 新任务 |
| GET | `/api/cookies` | — | `{ok, cookies:[{host, count, updated}]}` |
| POST | `/api/cookies` | `{host 或 url, content}` | `{ok, host, count, path}` |
| POST | `/api/delete-cookies` | `{host}` | `{ok}` |
| GET | `/api/list-dir` | `?path=` | `{ok, kind, path, parent, items[]}`；`path` 为空列出盘符 |
| POST | `/api/set-default-dir` | `{dir}` | `{ok, download_dir}`（持久化到 `config.json`） |
| POST | `/api/open-folder` | `{path?}` 或 `{dir?}` 或 `{}` | `{ok, opened}` |
| POST | `/api/clear-cache` | — | `{ok}` 清空解析缓存 |

### 3.2 `format` 取值（`/api/download`）

| 值 | 含义 |
|---|---|
| `best` | 最佳画质（不分容器） |
| `mp4` | 最佳 MP4 |
| `720` / `1080` / `480` … | 指定分辨率档（**横竖屏都会选对**，见下） |
| `720mp4` | 指定分辨率 + 优先 MP4 容器 + 优先 H.264 |
| `audio` | 提取 MP3 |
| `id:<format_id>` | 精确到某个格式（推荐，所见即所得） |

### 3.3 示例

```bash
curl -s http://127.0.0.1:8787/health

curl -s -X POST http://127.0.0.1:8787/api/preview \
     -H "Content-Type: application/json" \
     -d '{"url":"https://www.youtube.com/watch?v=jXwOcpkMQAA"}'

curl -s -X POST http://127.0.0.1:8787/api/download \
     -H "Content-Type: application/json" \
     -d '{"url":"https://www.youtube.com/watch?v=jXwOcpkMQAA","format":"720mp4"}'
# → {"ok":true,"task_id":"ab12cd34ef56","status":"queued","cookies_used":null,"proxy_used":null}

curl -s http://127.0.0.1:8787/api/tasks/ab12cd34ef56
```

---

## 4. 配置与约定

### 4.1 `yt-dlp-server/config.json`

```json
{
  "download_dir": "E:\\AI软件\\视频下载\\downloads",
  "proxy": "auto",
  "cookies_from_browser": "firefox",
  "update_ytdlp": false
}
```

| 键 | 说明 |
|---|---|
| `download_dir` | 默认下载目录（绝对路径）。优先级最高 |
| `proxy` | `auto`(默认，先直连、网络失败再退回系统代理) / `none`(只直连) / `system`(只用系统代理) / 显式地址 |
| `cookies_from_browser` | 让 yt-dlp 直接读浏览器 Cookie（`firefox` / `chrome` / `edge`…）。**不推荐**：Chrome 127+ 的 DPAPI 加密通常读不了，用插件同步更稳 |
| `update_ytdlp` | 为 `true` 时下载前自动更新 yt-dlp |

### 4.2 下载目录优先级与 `dir` 解析规则

优先级：

```
--dir / MCP 的 dir 参数  >  config.json 的 download_dir  >  环境变量 YTDLP_DOWNLOAD_DIR  >  <仓库>/downloads
```

**`dir` 取值解析规则**（唯一实现在 `download_server.resolve_download_dir()`，
CLI `--dir`、MCP `dir`、HTTP `dir` 三处共用，`vdl.py` 只做转发）：

| 输入 | 解析结果 |
|---|---|
| 空 / 不传 | 默认下载目录 |
| `D:\视频\教程`（绝对路径） | `D:\视频\教程` |
| `教程`、`a/b`（普通相对路径） | **相对默认下载目录** → `<默认目录>\教程`、`<默认目录>\a\b` |
| `./out`、`../out`、`.`、`..`（显式相对） | 相对**当前工作目录** |
| `~/Videos`、`%USERPROFILE%\Videos`、`$HOME/Videos` | 先展开 `~` 与环境变量，再按上面规则判定 |

> **为什么相对路径不相对 cwd**：AI / 脚本可能从任意工作目录调用本工具（比如 `C:\`），
> 若相对 cwd，「存到教程文件夹」会落到意想不到的地方。约定为「相对默认下载目录」后，
> 调用方在哪儿都能得到同一个结果；要相对当前目录必须**显式**写 `./`。
>
> 解析结果一律在 stderr 日志与 JSON 的 `dir` 字段里回显为**绝对路径**，调用方不必自己推算。

目录不存在会自动创建（含多级）。以下情况抛 `DirError`，CLI 对应 `error_code = "bad_dir"`
且**退出码 2**（在联网之前就失败）：

- 路径指向一个已存在的**文件**
- 路径含非法字符 / 盘符不存在，`makedirs` 失败
- 目录存在但没有写权限

### 4.3 Cookie

按域名匹配：`yt-dlp-server/cookies/<站点域名>.txt`（浏览器插件自动同步，Netscape 格式）
→ 都没命中时回退到 `cookies.txt`（手工放的全局文件）。

每次下载会把 Cookie 复制一份私有副本给 yt-dlp，避免 yt-dlp 回写覆盖同步数据、也避免并发互相改写。

### 4.4 代理策略（重要）

默认 `auto` = **先直连**，遇到 `network` / `timeout` 类失败自动**退回系统代理**重试一次
（`attempts` 字段会变成 `2`）。

为什么不是"一律用系统代理"：不少机器上 Clash 是 **TUN 模式**，分流由它自己完成，
再套一层系统代理属于二次代理，反而会把本可直连的站点打死（实测 `youtube.com`、`x.com` 都中过）；
而 `tiktok.com` 这类站点又必须走代理。所以交给"实际结果"来决定，而不是预先猜。

系统代理只在 Windows 读取注册表（`Internet Settings\ProxyEnable/ProxyServer`）；
非 Windows 用环境变量 `http_proxy`。

### 4.5 分辨率档位选择（重要）

内部用 yt-dlp 的 **`-S res:N`** 排序来选档，**不是** `-f "[height<=720]"`。

原因：竖屏视频（抖音 / YouTube Shorts）的 720p 档是 **720x1280**，
`[height<=720]` 会命中 360x640，等于白白降一大档。`-S res:720` 按「短边最接近 720」排序，
横屏 → 1280x720、竖屏 → 720x1280，两种画幅都正确（实测）。

排序键：`res:<N>,ext:mp4:m4a,vcodec:h264,proto:https`
（分辨率优先 → 容器偏好 → H.264 兼容性最好 → 直连优先）。

### 4.6 优先直连（`proto:https`）

排序键最后一项 `proto:https` 让 yt-dlp 优先挑**直连 https** 的格式，避开分片流
（`m3u8` / `dash`）。这不影响画质，但能避免一个很隐蔽的失败：

> 分片流会先落一地 `-FragN` 临时文件再合并，结束时逐个删除。
> 如果命令是**由 AI 助手代跑**的，宿主环境可能带「批量删除保护」，
> 删到第 50 个文件时被拦下 → **文件其实已经下完，却以失败收场**
> （错误里会出现 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` 之类的字样）。

遇到这种情况：换 `--quality 720`（同档位通常有直连格式）、或指定 `--quality id:<直连格式ID>`
（`--info` 里能看 `format_id`），或者干脆自己在本机终端里跑一次。

### 4.7 环境变量

| 变量 | 说明 |
|---|---|
| `YTDLP_SERVER_PORT` | HTTP 服务端口，默认 `8787` |
| `YTDLP_DOWNLOAD_DIR` | 默认下载目录（优先级低于 config.json） |
| `YTDLP_SERVER_DIR` | 指定 `yt-dlp-server/` 目录（CLI 找不到内核时用） |
| `http_proxy` / `https_proxy` | 非 Windows 下作为系统代理来源 |

### 4.8 错误码

| `error_code` | 含义 | 处理建议 |
|---|---|---|
| `need_cookies` | 站点要求登录态 | 先在浏览器打开一次该站点，用插件「🍪 同步」 |
| `unsupported` | yt-dlp 不支持该链接 | 换视频页地址（不要用嵌入页/短链） |
| `forbidden` | 403 被拒 | 多为需要 Cookie 或需要代理 |
| `timeout` | 网络超时 | 检查网络/代理；或换 `--proxy` 策略 |
| `network` | 连接/SSL 失败 | 同上；`auto` 模式下已自动重试过另一种链路 |
| `unavailable` | 视频不存在/已删除/私密 | 无解，换个链接 |
| `format` | 该清晰度不可用 | 用 `--info` 看可用档位后重选 |
| `no_ytdlp` | 找不到 yt-dlp | `pip install -U yt-dlp` 或在本仓库跑 `python setup.py` |
| `bad_dir` | `--dir` 不合法（指向文件 / 非法字符 / 不可写） | 换可写目录；规则见 §4.2。CLI 退出码为 **2** |
| `unknown` | 其它 | 看 `error` 原文 |

> 注意：`ok: true` 时 `error` / `error_code` / `hint` **一定是 `null`**。
> `auto` 代理模式下第一条链路失败、第二条成功的场景，成功结果里不会残留上一条链路的错误
> （回归测试见 `tools/retry_contract_test.py`）。判断成败请只看 `ok`。

---

## 5. 新机器上跑起来

```bash
# 1) 克隆仓库
git clone https://github.com/Zeno-Zhu/ytdlp-video-downloader.git "E:\AI软件\视频下载"

# 2) 初始化（检查 Python / yt-dlp / ffmpeg，生成 config.json，联网自检）
cd "E:\AI软件\视频下载"
python setup.py --test

# 3) 接入 AI（Skill 必装；想同时要 MCP 和 Codex 说明就加 --all）
python install_ai.py

# 4) 验证
python vdl.py "https://www.youtube.com/watch?v=jXwOcpkMQAA" --info --json
```

浏览器插件（可选）：`chrome://extensions` → 开发者模式 → 加载已解压的扩展程序 →
选 `yt-dlp-chrome-extension` → 双击 `install_host.bat`（自动识别扩展 ID）。

---

## 6. 排错

| 现象 | 原因 / 处理 |
|---|---|
| `未找到 yt-dlp` | 跑 `setup.ps1`，或 `pip install -U yt-dlp` |
| 中文标题乱码 | 子进程必须带 `PYTHONUTF8=1`（内核已处理，自己写脚本调时注意） |
| YouTube 报 SSL EOF | 换 `--proxy` 策略试（`none` ↔ `system`）；Clash TUN 模式下优先 `none` |
| 抖音报需要 Cookie | 插件「🍪 同步」，或往 `yt-dlp-server/cookies/douyin.com.txt` 放 Netscape 格式 Cookie |
| `竖屏视频下到了 360p` | 别用 `[height<=...]` 选择器，用 `-S res:N`（见 4.5） |
| 提示 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` / 临时分片删不掉，文件下完却报错 | 分片流的临时文件清理被宿主环境的批量删除保护拦了（常见于 AI 助手代跑）。换直连格式：`--quality 720` 或 `--quality id:<格式ID>`（见 4.6） |
| 下载到一半失败 | 看 `error_code`；`network`/`timeout` 会自动重试另一种链路 |
| 服务日志 | `yt-dlp-server/server.log`（服务）、`launcher.log`（启动器） |
| **整个 downloads 目录不见了** | 先翻**回收站**：本机宿主的「安全删除」层会把被删目录整棵移进回收站隔离区（`<盘>:\$Recycle.Bin\<SID>\$R*`），文件无损，拷回来即可 |

### 6.1 ⚠️ 写清理脚本前必读（实测踩到过）

**本机环境下删除目录会被拦截**：`os.rmdir()` / `shutil.rmtree()` 在目录**非空**时本该抛
`ENOTEMPTY`，实际却被安全删除层当成删除请求，**把整棵目录树移进回收站隔离区并返回成功**。

后果：一段"自底向上清理空目录"的循环会一路删到 `downloads` 本身——你以为只清了
`downloads/a/b/c`，实际上整个 `downloads` 都没了（2.2GiB）。而且 `returncode == 0`
和 `ignore_errors=True` 都掩盖了这件事。

写任何测试 / 清理脚本时：

1. **不要在真实 `downloads\` 里建目录再删**。用 `tempfile.mkdtemp()` 建隔离环境：
   临时替换 `download_server.DOWNLOAD_DIR`，并把 `ds.load_config` 置空
   （否则 `config.json` 会把它改回真实路径）。参考 `tools/dir_test.py`。
2. 清理前**校验目标路径确实在系统临时目录下**，不是就拒绝。
3. 删目录前先 `os.chdir()` 切出该目录，否则 Windows 删不掉进程的 cwd，留下空目录链。
4. 只删自己这一步创建的路径，不要"顺手清理"任何预先存在的东西。
