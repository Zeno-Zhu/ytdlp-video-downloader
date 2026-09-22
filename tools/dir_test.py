# -*- coding: utf-8 -*-
"""resolve_dir（--dir 解析规则）行为测试

⚠️ 重要教训（这个脚本原来踩过，见下）
--------------------------------------
本机环境下「删除目录」会被宿主的安全删除层拦截：`os.rmdir()` 在目录**非空**时
本该抛 ENOTEMPTY，实际却被当成删除请求把**整个目录树移进回收站隔离区**并返回成功。
结果：脚本里一句清理 `downloads/a/b/c` 的 `os.rmdir` 一路向上，把真实的 `downloads`
（含用户 2.2GiB 视频）整个移走了。

所以本测试的硬性规则：
  1. **绝不触碰真实的默认下载目录**——所有用例都在系统临时目录里跑
     （通过临时替换 `ds.DOWNLOAD_DIR` 并把 `load_config` 置空实现）；
  2. 清理时只删自己创建的临时根目录，且必须校验路径确实在系统临时目录下；
  3. 不创建真实的 `~/xxx`（把 `USERPROFILE` 指到临时目录来验证 `~` 展开）。

跑法：python tools/dir_test.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vdl  # noqa: E402

ds = vdl.ds

# ---------------- 隔离环境 ----------------
TMPROOT = tempfile.mkdtemp(prefix="vdl_dir_test_")
FAKE_DOWNLOADS = os.path.join(TMPROOT, "downloads")      # 假的「默认下载目录」
CWD = os.path.join(TMPROOT, "cwd_root", "child")         # 假的「当前目录」
TMPHOME = os.path.join(TMPROOT, "fake_home")             # 假的「用户主目录」
for d in (FAKE_DOWNLOADS, CWD, TMPHOME):
    os.makedirs(d, exist_ok=True)

_orig_down, _orig_load = ds.DOWNLOAD_DIR, ds.load_config
_orig_cwd, _orig_userprofile = os.getcwd(), os.environ.get("USERPROFILE")
ds.DOWNLOAD_DIR = FAKE_DOWNLOADS
ds.load_config = lambda: None            # 否则 config.json 会把 DOWNLOAD_DIR 改回真实的
os.environ["USERPROFILE"] = TMPHOME      # 让 ~ 展开到临时目录
os.chdir(CWD)

print("隔离根目录    :", TMPROOT)
print("假默认下载目录:", FAKE_DOWNLOADS)
print("假当前目录    :", CWD)
print("真实下载目录  :", os.path.abspath(_orig_down), "← 本测试绝不允许碰它")
print()

cases = [
    ("绝对路径",             os.path.join(TMPROOT, "abs_case")),
    ("相对路径（单段）",      "教程"),
    ("相对路径（多级）",      "a/b/c"),
    ("~ 展开",               "~/vdl_home_t"),
    ("%USERPROFILE% 展开",   r"%USERPROFILE%\vdl_env_t"),
    ("$USERPROFILE 展开",    "$USERPROFILE/vdl_env_t2"),
    ("显式 ./ 相对当前目录",  "./vdl_cwd_t"),
    ("显式 ../ 相对当前目录", "../vdl_up_t"),
    ("默认（不传 --dir）",    None),
]

outside = []
for label, val in cases:
    try:
        got = vdl.resolve_dir(val)
        inside = got.startswith(TMPROOT)
        if not inside:
            outside.append(got)
        print(f"{'OK ' if inside else '!! '}{label:22} --dir={val!r:34} → {got}")
    except vdl.DirError as exc:
        outside.append(label)
        print(f"!! {label:22} --dir={val!r:34} → 意外 DirError: {str(exc)[:80]}")

print()
probe = os.path.join(TMPROOT, "probe_file.txt")
with open(probe, "w", encoding="utf-8") as f:
    f.write("x")

neg_ok = True
for label, val in [("指向已存在的文件", probe),
                   ("非法字符", os.path.join(TMPROOT, "il<le>gal|name"))]:
    try:
        got = vdl.resolve_dir(val)
        neg_ok = False
        print(f"!!  {label:22} 本该失败却成功了: {got}")
    except vdl.DirError as exc:
        print(f"OK {label:22} → DirError: {str(exc)[:80]}")

# ---------------- 清理（只删临时根） ----------------
# 注意：必须先切回原 cwd。Windows 不允许删除"当前进程的工作目录"，否则会留下空目录链。
os.chdir(_orig_cwd)

print()
real_tmp = os.path.abspath(tempfile.gettempdir()).lower()
if not os.path.abspath(TMPROOT).lower().startswith(real_tmp):
    print("拒绝清理：临时根不在系统临时目录下 ——", TMPROOT)
else:
    assert os.path.abspath(FAKE_DOWNLOADS).startswith(TMPROOT)
    shutil.rmtree(TMPROOT, ignore_errors=True)
    print(f"隔离目录已清理: {TMPROOT}")

if _orig_userprofile is None:
    os.environ.pop("USERPROFILE", None)
else:
    os.environ["USERPROFILE"] = _orig_userprofile
ds.DOWNLOAD_DIR, ds.load_config = _orig_down, _orig_load

print()
print("真实下载目录未受影响 ->",
      os.path.isdir(_orig_down), "条目数", len(os.listdir(_orig_down)))

ok = not outside and neg_ok
print()
print("结果:", "通过" if ok else "不通过")
sys.exit(0 if ok else 1)
