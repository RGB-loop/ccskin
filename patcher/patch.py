"""step 2 — patch: 按补丁定义做等长替换。默认 dry-run,--apply 才写入。

两阶段: 先对照原始文件校验全部条目并解析成 (offset, new) 写入计划
(条目顺序无关,dry-run 与 --apply 判定一致),全部通过才一次性写回。
写入前备份到 <binary>.orig;已有备份与目标指纹不符(如自动更新换了
新版本)时另存序号后缀,绝不覆盖。
"""
import hashlib
import pathlib
import shutil

from . import defio, discovery, glyphpack, ui

_CAT_LABEL = {"icon": "图标", "name": "名字", "version": "版本", "color": "主题色"}


def _readable(entry) -> str:
    """没有 desc 时,从 old/new 解码出一个可读的替换说明。"""
    if entry.get("old") is None:
        return f"@ {entry.get('offset', 0):,} 的大段结构替换(见定义注释)"
    old = entry["old"].decode("ascii", errors="replace")
    new = entry["new"].decode("ascii", errors="replace")
    if len(old) <= 60:
        dec_old = glyphpack.decode_escapes(old)
        dec_new = glyphpack.decode_escapes(new)
        if dec_old != dec_new:
            return f"{dec_old} → {dec_new}"
    return "(大段结构替换,见定义文件)"


def _plan(data: bytes, patches: list):
    """对照原始内容校验每条补丁,返回 (写入计划, 失败条数)。

    写入计划是 [(offset, new_bytes)],全部基于原始文件的偏移。
    """
    writes = []
    failures = 0
    for p in patches:
        old, new, expected = p.get("old"), p["new"], p["expected"]
        if old is None:
            off = p["offset"]
            span = data[off:off + len(new)]
            if len(span) != len(new) or hashlib.sha1(span).hexdigest() != p["sha1"]:
                ui.fail(f"{p['name']}: @ {off:,} 内容指纹不符(版本不符?或已打过补丁?)")
                failures += 1
                continue
            if span == new:
                ui.fail(f"{p['name']}: 目标内容与替换内容已一致(已打过补丁?)")
                failures += 1
                continue
            ui.ok(f"{p['name']} @ {off:,} (sha1 校验通过)")
            writes.append((off, new))
            continue
        if len(old) != len(new):
            ui.fail(f"{p['name']}: old/new 长度不等 ({len(old)} vs {len(new)})")
            failures += 1
            continue
        if old == new:
            ui.fail(f"{p['name']}: old 与 new 相同,替换无意义(定义文件错误)")
            failures += 1
            continue
        offs = discovery.find_all(data, old)
        if len(offs) != expected:
            ui.fail(f"{p['name']}: 预期 {expected} 处,实际 {len(offs)} 处"
                    f"(版本不符?或已经打过补丁?)")
            failures += 1
            continue
        ui.ok(f"{p['name']} × {len(offs)}")
        writes += [(off, new) for off in offs]

    # 区间重叠检查: 两条补丁改到同一段字节,结果取决于顺序 —— 直接拒绝
    writes.sort()
    for (a_off, a_new), (b_off, _) in zip(writes, writes[1:]):
        if a_off + len(a_new) > b_off:
            ui.fail(f"替换区间重叠: @ {a_off:,}(长 {len(a_new)}) 与 @ {b_off:,}")
            failures += 1
    return writes, failures


def _fingerprint(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _backup(binary: str) -> pathlib.Path:
    """备份原文件。已有备份先比指纹: 相同复用,不同换新名,绝不覆盖。

    优先备份到 <binary>.orig(旁边);目标目录不可写时降级到项目 backups/。
    两个目录各有 <base> 及 <base>.1..99 共 100 个槽位,全被占用且内容都不同
    才算失败。指纹按需惰性计算(常见的首次备份根本不需要读整个大文件)。
    """
    src = pathlib.Path(binary)
    src_fp = None  # 惰性: 只有真要比对已存在备份时才算整文件 sha256

    def save_into(base: pathlib.Path):
        """返回备份路径;100 个槽位都被占用且内容不同时返回 None。"""
        nonlocal src_fp
        for backup in [base] + [pathlib.Path(f"{base}.{i}") for i in range(1, 100)]:
            if not backup.exists():
                shutil.copy2(binary, backup)
                ui.ok(f"已备份原文件: {backup}")
                return backup
            if backup.stat().st_size == src.stat().st_size:
                if src_fp is None:
                    src_fp = _fingerprint(src)
                if _fingerprint(backup) == src_fp:
                    ui.info(f"备份已存在且与目标一致,复用: {backup}")
                    return backup
            ui.warn(f"{backup} 与当前目标内容不同(可能是旧版本的备份),保留不动")
        return None

    try:
        backup = save_into(pathlib.Path(binary + ".orig"))
    except OSError as e:
        # copy2/stat 抛错 = 目标目录不可写(如 /usr/local/bin)
        ui.warn(f"目标目录不可写({e}),备份改存到项目 backups/")
        backup = None
    if backup is not None:
        return backup

    alt_dir = pathlib.Path(__file__).resolve().parent.parent / "backups"
    alt_dir.mkdir(exist_ok=True)
    backup = save_into(alt_dir / (src.name + ".orig"))
    if backup is None:
        raise OSError(f"备份失败: {alt_dir}/ 下 100 个槽位都被占用且内容均与目标不同")
    return backup


def run(binary: str, def_path: str, apply: bool = False, yes: bool = False) -> int:
    meta, patches = defio.load_file(def_path)
    data = pathlib.Path(binary).read_bytes()
    original_len = len(data)

    ui.step("读取")
    ui.info(f"目标: {binary} " + ui.dim(f"({original_len:,} bytes)"))
    ui.info(f"定义: {def_path} " +
            ui.dim(f"(target={meta.get('target_version')}, verified={meta.get('verified')})"))
    if not meta.get("verified"):
        ui.warn("定义文件 verified != true,建议先人工核对")

    ui.step("替换内容预览")
    for cat, label in list(_CAT_LABEL.items()) + [(None, "其他")]:
        entries = [p for p in patches
                   if (p.get("cat") == cat if cat else p.get("cat") not in _CAT_LABEL)]
        if not entries:
            continue
        total = sum(e["expected"] for e in entries)
        ui.info(ui.bold(f"[{label}]") + f" {len(entries)} 条替换,共 {total} 处:")
        for e in entries:
            desc = e.get("desc") or _readable(e)
            ui.info(f"    {desc} " + ui.dim(f"×{e['expected']}"))

    ui.step("逐条校验(对照原始内容)")
    writes, failures = _plan(data, patches)
    if failures:
        ui.fail(f"{failures} 条校验失败,未写入任何内容")
        return 1
    if not writes:
        ui.fail("没有任何可执行的替换(内部错误)")
        return 1
    ui.ok(f"{len(patches)} 条全部通过,共 {len(writes)} 处替换,总大小不变")

    if not apply:
        ui.info(ui.dim("dry-run 结束,未写入。确认无误后加 --apply 实际写入。"))
        return 0

    ui.step("写入")
    if not yes and not ui.confirm(f"确认写入 {binary}?(原文件备份为 .orig)"):
        ui.warn("已取消;非交互环境请加 --yes")
        return 1
    backup = _backup(binary)
    buf = bytearray(data)
    for off, new in writes:
        buf[off:off + len(new)] = new
    if len(buf) != original_len:
        ui.fail("总大小发生变化(内部错误),未写入")
        return 1
    pathlib.Path(binary).write_bytes(buf)
    ui.ok("写入完成(签名已失效)")
    ui.info(f"下一步: python3 -m patcher sign {binary}")
    ui.info(ui.dim(f"恢复原版: cp {backup} {binary}"))
    return 0
