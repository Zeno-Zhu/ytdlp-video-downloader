# skills/

## `video-download/SKILL.md.in` 是**模板**，不是能直接用的 Skill

它里面有三个占位符：

| 占位符 | 会被替换成 |
|---|---|
| `{{PY}}` | 本机可用的 python 解释器绝对路径 |
| `{{REPO}}` | 本仓库在本机的绝对路径 |
| `{{VDL}}` | `<仓库>/vdl.py` |

**为什么不用纯静态的 SKILL.md**：Skill 正文里要让 AI 直接拿到「可以照抄就能跑」的命令，
就必须包含本机绝对路径——而每台电脑的路径不同（`E:\AI软件\视频下载` / `D:\tools\...`）。
所以这里存机器无关的模板，安装时由脚本渲染成机器相关的成品。

安装 / 刷新：

```bash
python install_ai.py            # 渲染 → ~/.workbuddy/skills/video-download/SKILL.md
python install_ai.py --all      # 顺便注册 MCP + 写 ~/.codex/AGENTS.md
```

**要改说明就改这个模板**，不要去改 `~/.workbuddy/skills/` 里那份渲染结果——重跑
`install_ai.py` 会把它覆盖掉。
