"""自动定位系统里的 Claude Code 安装。

支持: macOS 原生单文件二进制(Mach-O)。识别顺序:
  1. PATH 里的 claude(shutil.which,解析符号链接)
  2. 常见安装路径
npm 安装(cli.js 文本)能识别但暂不支持补丁,会明确提示。
"""
import os
import pathlib
import shutil

CANDIDATES = [
    "~/.claude/local/claude",
    "~/.claude/local/bin/claude",
    "~/.local/bin/claude",
    "/usr/local/bin/claude",
    "/opt/homebrew/bin/claude",
]

KIND_MACHO = "macho"   # 原生单文件二进制(完整支持)
KIND_JS = "js"         # npm 安装(cli.js 文本,暂不支持)
KIND_UNKNOWN = "unknown"


def sniff_kind(path: str) -> str:
    """按文件头判断安装形态。"""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return KIND_UNKNOWN
    if head in (b"\xcf\xfa\xed\xfe",   # Mach-O 64 位(小端,macOS 实际形态)
                b"\xfe\xed\xfa\xcf",   # Mach-O 64 位(大端)
                b"\xca\xfe\xba\xbe",   # fat/universal
                b"\xca\xfe\xba\xbf"):  # fat 64
        return KIND_MACHO
    if head.startswith(b"#!") or head.lstrip().startswith(b"/**!"):
        return KIND_JS
    return KIND_UNKNOWN


def find_installations():
    """返回 [(path, kind)],去重,PATH 优先。找不到返回空列表。"""
    found: list = []
    seen = set()

    def _add(p: str):
        rp = os.path.realpath(p)
        if rp in seen or not os.path.isfile(rp):
            return
        seen.add(rp)
        found.append((rp, sniff_kind(rp)))

    w = shutil.which("claude")
    if w:
        _add(w)
    for c in CANDIDATES:
        p = os.path.expanduser(c)
        if os.path.exists(p):
            _add(p)
    return found


def default_binary():
    """第一个可用的 Mach-O 安装;没有则 None。"""
    for path, kind in find_installations():
        if kind == KIND_MACHO:
            return path
    return None
