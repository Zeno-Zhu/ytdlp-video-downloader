# -*- coding: utf-8 -*-
"""临时脚本：模拟 MCP 客户端，跑一遍 initialize / tools/list / tools/call"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

msgs = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "probe", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "list_downloads", "arguments": {"limit": 5}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "preview_video",
                "arguments": {"url": "https://www.youtube.com/watch?v=jXwOcpkMQAA"}}},
]

payload = "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n"

p = subprocess.run([PY, os.path.join(ROOT, "mcp_server.py")],
                   input=payload, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=300, cwd=ROOT)

print("exit:", p.returncode)
print("--- stderr ---")
print((p.stderr or "").strip()[:800])
print("--- responses ---")
for line in (p.stdout or "").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        m = json.loads(line)
    except Exception:
        print("NON-JSON:", line[:200])
        continue
    mid = m.get("id")
    if mid == 1:
        print("initialize ->", json.dumps(m.get("result", {}).get("serverInfo"), ensure_ascii=False),
              m.get("result", {}).get("protocolVersion"))
    elif mid == 2:
        tools = m.get("result", {}).get("tools", [])
        print(f"tools/list -> {len(tools)} 个工具:", [t["name"] for t in tools])
    elif mid is not None:
        content = m.get("result", {}).get("content", [{}])[0].get("text", "")
        print(f"tools/call id={mid} isError={m.get('result', {}).get('isError')}")
        print("   ", content[:420].replace("\n", " "))
