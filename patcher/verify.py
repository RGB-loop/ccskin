"""step 4 — verify: 验证补丁效果。

  1. 重新解析二进制里的 logo 表,解码渲染预览(确认图标形状)
  2. 统计显示名/版本出现次数
  3. 跑 --version: 确认二进制能运行、内部真实版本号没变
  4. --pty: 用 script(1) 分配伪终端抓真实启动画面,grep 名字/版本
"""
import os
import pathlib
import re
import signal
import subprocess
import time

from . import discovery, glyphpack, ui


_PREVIEW_WINDOW = 4000   # logo 表附近查找 mid/feet 的半径


def _preview(data: bytes, feet_expected: str = None) -> None:
    """渲染二进制里当前的 logo(3 行)。feet_expected: 定义文件里第三行的源码形式。"""
    start = data.find(b'{default:{r1L:')
    if start < 0:
        ui.warn("未找到 logo 表,无法预览")
        return
    try:
        end = discovery.match_brace(data, start)
        table = data[start:end].decode("ascii")
    except (ValueError, UnicodeDecodeError) as e:
        ui.warn(f"logo 表解析失败: {e}")
        return
    lo = max(0, start - _PREVIEW_WINDOW)
    window = data[lo:start + _PREVIEW_WINDOW]

    # 第二行机身中部常量(可能被 mid 设计改过): 带上下文锚点,内容为 5 个 token
    mid = discovery._MID_CONTENT
    mm = re.search(
        re.escape(discovery._MID_CTX.encode("ascii"))
        + rb'"((?:\\u[0-9A-Fa-f]{4}|[^"\\]){5})"', window)
    if mm:
        mid = mm.group(1).decode("ascii")

    # 第三行小脚: 按期望内容(设计稿/原始常量)精确定位,不做模糊猜测
    feet = None
    for cand in (feet_expected, discovery._FEET_CONTENT):
        if cand and ('"' + cand + '"').encode("ascii") in window:
            feet = cand
            break

    rows = discovery._art_lines(table, feet or discovery._FEET_CONTENT, mid)
    if not rows:
        ui.warn("logo 表解析失败(槽位正则不匹配)")
        return
    ui.info("   " + rows[0])
    ui.info("   " + rows[1])
    if feet is not None:
        ui.info(" " + rows[2])
    else:
        ui.warn("第三行小脚未在 logo 表附近找到,跳过该行预览")


def _capture_pty(binary: str, seconds: int = 10) -> str:
    """用 pty.fork 直接抓 TUI 输出(不用 script(1): 其抓屏文件带缓冲,
    进程被杀时可能丢内容)。"""
    import pty
    import select

    abspath = str(pathlib.Path(binary).resolve())
    pid, fd = pty.fork()
    if pid == 0:  # 子进程: 拿到控制终端后 exec 目标
        os.environ["TERM"] = "xterm-256color"
        try:
            os.execv(abspath, [abspath])
        finally:
            # execv 失败绝不能回落到父进程的代码里继续跑
            os._exit(127)
    out = bytearray()
    end = time.time() + seconds
    fed = False
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.5)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        if not fed and time.time() > end - 3:
            try:
                os.write(fd, b"/exit\n")
            except OSError:
                pass
            fed = True
    # 先关 master fd 再杀:否则子进程(SIGHUP/SIGKILL 后)在内核退出路径上
    # 等 master 关闭,而我们阻塞 waitpid 等它退出 —— 互相等待形成死锁。
    # 全程不用阻塞式 waitpid。
    try:
        os.close(fd)
    except OSError:
        pass
    reaped = False
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, OSError):
            reaped = True
            break
        deadline = time.time() + 1.5
        while time.time() < deadline:
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                wpid = pid
            if wpid == pid:
                reaped = True
                break
            time.sleep(0.05)
        if reaped:
            break
    return out.decode("utf-8", errors="replace")


def run(binary: str, cfg: dict, pty: bool = False) -> int:
    data = pathlib.Path(binary).read_bytes()
    display = cfg.get("display", {})
    raw_name = display.get("name", "")
    real_version = bool(display.get("real_version"))
    version = "" if real_version else display.get("version", "")

    # 定位第三行小脚的期望内容(设计稿),给预览用
    feet_expected = None
    if not display.get("keep_icon"):
        try:
            from . import analyze
            design = analyze.load_design(
                pathlib.Path(__file__).resolve().parent.parent,
                display.get("icon_design", "octopus.toml"))
            row = design.get("feet", {}).get("row")
            if row:
                feet_expected = glyphpack.pack(
                    glyphpack.tokenize(discovery._FEET_CONTENT), row)
        except Exception:  # noqa: BLE001 — 预览是尽力而为,不因设计稿问题中断验证
            pass

    ui.step("图标预览")
    _preview(data, feet_expected)

    failures = 0
    ui.step("显示内容计数")
    if raw_name:
        # 锚定 children:"..." 上下文计数;裸子串对 'Code' 这类常见词会假阳性
        try:
            padded = discovery.validate_name(raw_name)
        except ValueError:
            padded = raw_name
        anchored = f'children:"{padded}"'.encode("ascii", errors="replace")
        cnt = data.count(anchored)
        (ui.ok if cnt else ui.fail)(f"显示名锚点 {anchored.decode()!r}: {cnt} 次")
        if cnt == 0:
            ui.warn("显示名未出现在标题显示位——二进制可能没打补丁")
            failures += 1
    if version:
        # 按补丁写入的形状计数: ["v9.99 "](数组位)/ ` v9.99 `(模板位)
        vb = re.escape(version.encode())
        cnt = len(re.findall(rb'\["' + vb + rb' *"\]', data)) \
            + len(re.findall(rb'` ?' + vb + rb' *`', data))
        (ui.ok if cnt else ui.fail)(f"显示版本 {version!r}: {cnt} 处显示位")
        if cnt == 0:
            failures += 1
    elif real_version:
        ui.info("已选择显示真实版本,跳过版本计数")
    if failures:
        ui.fail("显示内容计数未通过")
        return 1

    ui.step("运行 --version(证明内部版本未受影响)")
    try:
        r = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=60)
        out = (r.stdout or r.stderr).strip()
        if r.returncode == 0:
            ui.ok(f"输出: {out}")
        else:
            ui.fail(f"退出码 {r.returncode}: {out}")
            ui.warn("若退出码是 -9(Killed: 9)→ 被 Gatekeeper 拦,"
                    "到 设置→隐私与安全性 点“仍要打开”")
            return 1
    except subprocess.TimeoutExpired:
        ui.fail("--version 超时")
        return 1
    except OSError as e:
        ui.fail(f"无法运行目标二进制: {e}")
        return 1

    if pty:
        ui.step("抓真实启动画面(pty,约 10s)")
        text = _capture_pty(binary)
        # TUI 会把空格优化成光标转义序列,比较时去掉所有空白
        plain = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
        squash = re.sub(r"\s+", "", plain)
        ok_name = re.sub(r"\s+", "", raw_name) in squash if raw_name else True
        ok_ver = version in squash if version else True
        if not text:
            ui.fail("未抓到任何输出")
            return 1
        (ui.ok if ok_name else ui.fail)(f"画面包含名字 {raw_name!r}: {ok_name}")
        if version:
            (ui.ok if ok_ver else ui.fail)(f"画面包含版本 {version!r}: {ok_ver}")
        if not (ok_name and ok_ver):
            return 1
    ui.ok("验证通过")
    return 0
