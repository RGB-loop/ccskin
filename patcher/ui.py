"""终端输出辅助: 阶段标题 / ✓✗ 标记 / 颜色。

只有 stdout 是 tty 时才上颜色;设 NO_COLOR 可强制关闭。
"""
import os
import sys

_TTY = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _c(code, s):
    return f"\x1b[{code}m{s}\x1b[0m" if _TTY else str(s)


def step(title):
    print("\n" + _c("1;36", f"== {title} " + "=" * max(2, 40 - len(title) * 2)))


def ok(s):
    print("  " + _c("32", "✓") + " " + s)


def fail(s):
    print("  " + _c("31", "✗") + " " + s)


def warn(s):
    print("  " + _c("33", "!") + " " + s)


def info(s):
    print("  " + s)


def dim(s):
    return _c("2", s)


def bold(s):
    return _c("1", s)


def arrow(old, new):
    return f"{old} {_c('36', '→')} {new}"


def confirm(prompt):
    """写入前的交互确认;非 tty 环境一律返回 False(由调用方提示用 --yes)。"""
    if not sys.stdin.isatty():
        return False
    try:
        ans = input(f"{prompt} [y/N] ")
    except EOFError:
        return False
    return ans.strip().lower() in ("y", "yes")
