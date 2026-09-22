# AGENTS.md — 给 AI 编码助手的操作说明

本仓库是一个 yt-dlp 视频下载器（浏览器插件 + 本地服务 + 命令行 + MCP）。
**当用户给你一个视频链接说要下载时，直接用下面的命令，不要去看/改插件代码。**

## 一条命令搞定

```bash
python vdl.py "<视频链接>" --quality 720 --json
```

- 默认就是 **720p MP4（优先 H.264）**，`--quality` 可省略。
- `--json` 让 stdout 只输出一个 JSON 对象（进度/日志走 stderr），直接解析即可。
- 成功时把 `file_path` 告诉用户；失败时把 `error` + `hint` 转述给用户。

## 需要先确认目标视频时

```bash
python vdl.py "<视频链接>" --info --json     # 标题/作者/时长/全部清晰度，不下载
```

## 常用变体

| 需求 | 参数 |
|---|---|
| 1080p | `--quality 1080` |
| 只要音频 MP3 | `--audio` |
| 同时下字幕（srt） | `--subs` |
| 指定保存目录 | `--dir "D:\\视频\\教程"` |
| 指定默认目录下的子文件夹 | `--dir 教程` → `<默认目录>\\教程` |
| 整个播放列表 | `--playlist`（默认只下单个视频） |
| 下完在资源管理器选中文件 | `--open` |
| 指定精确格式 | `--quality id:<format_id>`（id 从 `--info` 里取） |

## `--dir` 规则（用户说"存到某个文件夹"时必读）

**相对路径相对「默认下载目录」，不是相对当前工作目录**——所以从任何 cwd 调用结果都一样。

| 用户说法 | 传什么 |
|---|---|
| "存到 D:\视频\教程" | `--dir "D:\视频\教程"` |
| "存到教程文件夹" | `--dir 教程`（会落到默认下载目录下的 `教程\`） |
| "就存当前目录" | `--dir ./` |
| "存到桌面" | `--dir "%USERPROFILE%\Desktop"` |

- 目录不存在会**自动创建**（含多级），不要先自己 mkdir。
- **不要把用户说的相对名字自己拼成绝对路径**，直接原样传给 `--dir`。
- 成功时 JSON 里的 `dir` 字段是解析后的绝对路径，跟用户回报时带上。
- 不合法时 `error_code="bad_dir"`、**退出码 2**（联网前就失败）。

## 输出契约

```json
{"ok": true, "title": "...", "dir": "E:\\...", "file_path": "E:\\...\\xxx.mp4",
 "filename": "xxx.mp4", "size_mb": 128.1, "resolution": "1280x720", "duration_sec": 556,
 "extractor": "youtube", "cookies_used": null, "proxy_used": null, "attempts": 1,
 "elapsed_sec": 20.6, "error": null, "error_code": null, "hint": null}
```

**判断成败只看 `ok`**：`ok=true` 时 `error`/`error_code`/`hint` 一定是 `null`（即使 `attempts>1`
说明中途换过代理链路，成功结果里也不会残留上一条链路的错误）。

退出码：`0` 成功 / `1` 下载或解析失败 / `2` 参数错误（含 `--dir` 不合法）。

## 不要动的几处（踩过坑）

1. **分辨率选择必须用 `-S res:N`**（见 `yt-dlp-server/download_server.py` 的 `resolve_format`）。
   写成 `-f "bv*[height<=720]"` 在**竖屏视频**（抖音 / Shorts）上会选到 360x640，
   因为竖屏 720p 档是 720x1280。改回去等于引入 bug。
2. **代理必须是 `auto`（先直连，失败再退回系统代理）**。Clash 的 TUN 模式下直连已被分流，
   无条件套系统代理会把 YouTube 打死；反过来 TikTok 又必须走代理。见 `proxy_candidates()`。
3. **子进程环境要强制 UTF-8**（`PYTHONUTF8=1`），否则 Windows 下中文标题会变乱码。
4. **排序键最后要留着 `proto:https`**（优先直连下载）。分片流（m3u8/dash）会产生几十个
   `-FragN` 临时文件，结束时逐个删除；若命令是由 AI 助手代跑的，宿主可能带「批量删除保护」，
   删到第 50 个被拦下 → 文件下完了却报失败。改成直连格式就能绕开。

## 收尾注意

- 下载目录里若出现 `.fXXXX.mp4.part` / `-FragN` 残留，那是失败任务的中间产物，
  只删这些明确带 `.part` / `.ytdl` / `-Frag` 的文件；**不要**对 `downloads\` 做通配批量删除。

> ⚠️ **本机环境的一个坑（实测踩到过，损失 2.2GiB 文件）**
> 在这里删除目录时，宿主的「安全删除」层会拦截：`os.rmdir()` / `shutil.rmtree()` 在目录
> **非空**时本该报错，实际却被当成删除请求**把整棵目录树移进回收站隔离区，并返回成功**。
> 于是「自底向上清理空目录」的循环会一路删到 `downloads` 本身——
> 你以为只删了 `downloads/a/b/c`，实际上整个 `downloads` 都没了（好在能在回收站找回）。
>
> **规矩**：任何测试 / 清理脚本都不要在真实的 `downloads\` 里建目录再删。
> 用 `tempfile.mkdtemp()` 建隔离环境（可临时替换 `download_server.DOWNLOAD_DIR`
> 并把 `load_config` 置空），只在系统临时目录里折腾。参考 `tools/dir_test.py` 的写法。
> 另外：删目录前先把进程的 cwd 切出去，否则 Windows 会留下空目录链。

## 更省事的路径：MCP

`mcp_server.py` 是零依赖的 MCP stdio 服务器，工具：
`download_video` / `preview_video` / `list_downloads` / `get_task_status` / `open_folder`。
配置示例见 `docs/API.md`。

## 其它

- 本地 HTTP 服务（浏览器插件用）：`yt-dlp-server/download_server.py`，默认 `127.0.0.1:8787`，
  接口清单见 `docs/API.md`。
- 新机器初始化：`python setup.py --test`
- 把本仓库接入 AI（Skill / MCP / 本机路径写入）：`python install_ai.py --all`
  —— 换机器或挪目录后重跑一次，所有绝对路径会刷新。
  Skill 模板在 `skills/video-download/SKILL.md.in`（含 `{{PY}}` / `{{REPO}}` / `{{VDL}}` 占位符），
  **改说明改模板，不要去改 `~/.workbuddy/skills/` 里那份渲染结果**。
