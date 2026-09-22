---
name: video-download
description: 给一个视频链接就下载到本地（默认 720p MP4、优先 H.264；横屏竖屏都能选对档位）。支持 YouTube / 抖音 / B站 / TikTok / X 等上千站点，粘贴分享文案也可以。当用户发来视频链接并说"下载 / 存下来 / 扒下来 / 保存这个视频 / 存到本地"，或需要批量下载、只要音频 MP3、顺便下字幕、先看看清晰度时使用。
---

# 视频下载（vdl）

内核是一个命令行脚本，**不要**让用户自己去开浏览器插件点按钮——直接跑命令，一句话拿到文件路径。

## 一行调用

```bash
PY="C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe"
[ -x "$PY" ] || PY=python          # 其它电脑：用任意 Python 3.8+，或仓库里的 setup.ps1
"$PY" "E:/AI软件/视频下载/vdl.py" "<视频链接>" --quality 720 --json
```

> 仓库位置可以换：`E:/AI软件/视频下载/` 是当前机器的路径；其它电脑上换成该仓库的实际路径即可，
> 脚本内部会自己找到同目录下的 `yt-dlp-server/`（也可用环境变量 `YTDLP_SERVER_DIR` 指定）。

**`--json` 必加**：这样 stdout 只有一个 JSON 对象，直接 `json.loads` 就能取字段；
进度和日志全都走 stderr，不会污染结果。

## 返回契约（`--json` 时 stdout）

```jsonc
{
  "ok": true,                       // 成功与否，先看这个
  "url": "https://...",
  "title": "视频标题",
  "file_path": "E:\\...\\downloads\\标题 [视频ID].mp4",   // ★ 直接把这个路径给用户
  "filename": "标题 [视频ID].mp4",
  "size_mb": 128.1,
  "resolution": "1280x720",         // 实际下到的分辨率，可用来向用户确认
  "duration_sec": 556,
  "quality": "720mp4", "quality_label": "720p MP4",
  "extractor": "youtube",
  "cookies_used": "douyin.com.txt", // 用了哪个站点的登录态（null=没用）
  "proxy_used": null,               // null=直连；有值=走了该代理
  "attempts": 1,                    // 试了几条链路（>1 说明降级重试过）
  "elapsed_sec": 20.6,
  "error": null, "error_code": null, "hint": null   // 失败时才有值
}
```

- 失败时 `ok=false`，读 `error`（中文可读）和 `error_code`（`need_cookies` / `unsupported` /
  `forbidden` / `timeout` / `network` / `unavailable` / `format` / `no_ytdlp`），
  `hint` 里是给用户看的处理建议。
- 退出码：`0` 成功、`1` 下载/解析失败、`2` 参数错误。

## 常用任务 → 命令

| 用户说法 | 命令 |
|---|---|
| 下载这个视频 / 存下来 | `"$PY" vdl.py "<url>" --json` |
| 要 1080p | `"$PY" vdl.py "<url>" --quality 1080 --json` |
| 只要音频 / 提取 MP3 | `"$PY" vdl.py "<url>" --audio --json` |
| 顺便要字幕 | `"$PY" vdl.py "<url>" --subs --json` |
| 先看看是什么视频 / 有哪些清晰度 | `"$PY" vdl.py "<url>" --info --json` |
| 存到指定文件夹 | `"$PY" vdl.py "<url>" --dir "D:\\Videos" --json` |
| 一次下多个 | `"$PY" vdl.py "<url1>" "<url2>" --json` |
| 下整个合集 / 播放列表 | `"$PY" vdl.py "<url>" --playlist --json`（默认只下单个视频） |
| 下完自动打开文件夹 | `"$PY" vdl.py "<url>" --open --json` |

`--quality` 可写：`720`(默认) / `1080` / `480` / `2160` / `best` / `audio` / `id:<格式ID>`。
`id:` 的值从 `--info` 的输出里挑（`formats[].format_id`），用于"就要这个格式"。

## 先说清楚再下（推荐流程）

链接标题和用户描述不一致时，别猜：

1. `--info --json` 拿到 `title` / `duration` / `author` / `quality_options`；
2. 跟用户确认是不是这个视频、要哪一档；
3. 再跑下载。

用户明确说"就是这个，720p 就行"时，直接下，不用多问。

## 关键坑（都踩过，别再踩）

- **横屏/竖屏都能选对**：内部用 `-S res:N` 而不是 `[height<=N]`。竖屏视频（抖音 / Shorts）
  的 "720p" 档是 720x1280，用 `height<=720` 会掉到 360x640。**不要**自己改回 `[height<=720]`。
- **代理是自动的**：先直连，网络/超时类失败再自动退回系统代理（`attempts` 会变成 2）。
  Clash 开 TUN 模式的机器上直连本来就已经分流，硬套代理反而会打死 YouTube。
  需要强制时用 `--proxy none` / `--proxy system` / `--proxy http://127.0.0.1:7897`。
- **抖音 / B站 / TikTok 需要登录态**：由浏览器插件同步到 `yt-dlp-server/cookies/<站点>.txt`。
  报 `need_cookies` 时，让用户先在浏览器打开一次该视频页，并点插件里的「🍪 同步」。
- **文件名里的 `？`**：Windows 不允许 `?`，yt-dlp 会用全角 `？` 替换，`file_path` 里的名字和
  标题不完全一致是正常的，别按标题去猜路径。
- **大文件要有耐心**：`--json` 会一直等到下载完成才输出；一个 1GB 的视频可能好几分钟。
  要立刻拿回控制权就自己 `run_in_background`，或改用 MCP 的 `wait=false`。
- **不要并发狂跑**：同一时刻最多跑 2~3 个下载，YouTube 会限流。

## 还想更省事：MCP

仓库里带了一个零依赖的 MCP 服务器 `mcp_server.py`，暴露
`download_video` / `preview_video` / `list_downloads` / `get_task_status` / `open_folder`。
在支持 MCP 的客户端里配好之后，可以直接"工具调用"，连命令行都不用敲。
配置方式见仓库 `docs/API.md`。
