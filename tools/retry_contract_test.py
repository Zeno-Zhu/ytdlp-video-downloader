# -*- coding: utf-8 -*-
"""回归测试：降级重试成功后，error / error_code / hint 必须被清空

背景：`--proxy auto` 是「先直连，网络类失败再退回系统代理」。曾经出过这个 bug：
第 1 条链路失败 → 写入了 error/error_code/hint；第 2 条链路成功 → 只把 ok 置 true，
旧错误没清掉，于是出现「`ok: true` 却带着 `error`」的自相矛盾结果，
按 `error` 判断的消费方会误判成失败。

本测试不依赖网络：直接替掉 `vdl._run_ytdlp`，模拟「失败 → 成功」两条链路。

⚠️ 同上：所有产物都在系统临时目录里，绝不写真实 downloads
（本机「删除目录」会被宿主安全删除层拦截并移进回收站隔离区，见 tools/dir_test.py 顶部说明）。

跑法：python tools/retry_contract_test.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vdl  # noqa: E402

TMPDIR = tempfile.mkdtemp(prefix="vdl_retry_test_")
FAKE = os.path.join(TMPDIR, "Fake Video [abc123].mp4")
with open(FAKE, "wb") as f:
    f.write(b"\0" * 1024)

calls = {"n": 0}


def fake_run(base_cmd, dl_dir, before, started, used_proxy):
    calls["n"] += 1
    if calls["n"] == 1:
        # 模拟直连失败（网络类错误 → 会触发降级重试）
        return 1, None, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol", {}
    return 0, FAKE, "", {"title": "Fake Video", "duration_sec": 12,
                         "extractor": "fake", "resolution": "1280x720"}


vdl._run_ytdlp = fake_run
vdl.ARGS = vdl.build_parser().parse_args(
    ["https://example.com/v", "--dir", TMPDIR.replace("\\", "/"), "--json"]
)
vdl.ARGS.quality = vdl.normalize_quality(vdl.ARGS.quality)   # main() 里会做这一步
vdl._QUIET = True

r = vdl.cmd_download_one("https://example.com/v")

checks = [
    ("attempts == 2（确实降级重试过）", r["attempts"] == 2, r["attempts"]),
    ("ok is True",                      r["ok"] is True,  r["ok"]),
    ("error is None",                   r["error"] is None, r["error"]),
    ("error_code is None",              r["error_code"] is None, r["error_code"]),
    ("hint is None",                    r["hint"] is None, r["hint"]),
    ("file_path 指向产物",               r["file_path"] == FAKE, r["file_path"]),
    ("proxy_used 是成功那条链路",         r["proxy_used"] is not None, r["proxy_used"]),
]

ok = True
for label, passed, got in checks:
    if not passed:
        ok = False
    print(f"{'OK ' if passed else '!! '}{label:28} → {got}")

print()
print("结果:", "通过" if ok else "不通过")

# 清理：只删自己建的临时根
real_tmp = os.path.abspath(tempfile.gettempdir()).lower()
if os.path.abspath(TMPDIR).lower().startswith(real_tmp):
    shutil.rmtree(TMPDIR, ignore_errors=True)
    print("已清理隔离目录")
else:
    print("拒绝清理非临时目录:", TMPDIR)

sys.exit(0 if ok else 1)
