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
| 指定保存目录 | `--dir "D:\\Videos"` |
| 整个播放列表 | `--playlist`（默认只下单个视频） |
| 下完在资源管理器选中文件 | `--open` |
| 指定精确格式 | `--quality id:<format_id>`（id 从 `--info` 里取） |

## 输出契约

```json
{"ok": true, "title": "...", "file_path": "E:\\...\\xxx.mp4", "filename": "xxx.mp4",
 "size_mb": 128.1, "resolution": "1280x720", "duration_sec": 556, "extractor": "youtube",
 "cookies_used": null, "proxy_used": null, "attempts": 1, "elapsed_sec": 20.6,
 "error": null, "error_code": null, "hint": null}
```

退出码：`0` 成功 / `1` 下载或解析失败 / `2` 参数错误。

## 不要动的几处（踩过坑）

1. **分辨率选择必须用 `-S res:N`**（见 `yt-dlp-server/download_server.py` 的 `resolve_format`）。
   写成 `-f "bv*[height<=720]"` 在**竖屏视频**（抖音 / Shorts）上会选到 360x640，
   因为竖屏 720p 档是 720x1280。改回去等于引入 bug。
2. **代理必须是 `auto`（先直连，失败再退回系统代理）**。Clash 的 TUN 模式下直连已被分流，
   无条件套系统代理会把 YouTube 打死；反过来 TikTok 又必须走代理。见 `proxy_candidates()`。
3. **子进程环境要强制 UTF-8**（`PYTHONUTF8=1`），否则 Windows 下中文标题会变乱码。

## 更省事的路径：MCP

`mcp_server.py` 是零依赖的 MCP stdio 服务器，工具：
`download_video` / `preview_video` / `list_downloads` / `get_task_status` / `open_folder`。
配置示例见 `docs/API.md`。

## 其它

- 本地 HTTP 服务（浏览器插件用）：`yt-dlp-server/download_server.py`，默认 `127.0.0.1:8787`，
  接口清单见 `docs/API.md`。
- 新机器初始化：`powershell -ExecutionPolicy Bypass -File setup.ps1`
