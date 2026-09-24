"""step 1 — analyze: 分析二进制,发现锚点,生成补丁定义 TOML。

自动发现失败时可以 --llm 调用 LLM 辅助判断(只给建议,不改文件)。
"""
import pathlib
import platform
import re
import subprocess
import time

from . import bytecode, defio, discovery, llm as llm_mod, ui


def _try_probe(binary: str):
    """单次 binary --version 尝试,成功返回版本串,失败返回 None。"""
    try:
        r = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=30
        )
        token = r.stdout.strip().split()[0] if r.stdout.strip() else ""
        return token or None
    except Exception:
        return None


def probe_version(binary: str):
    """跑 binary --version 拿版本号;首次失败等 2 秒重试一次(macOS Gatekeeper
    首次执行新路径的二进制可能需要额外的公证检查时间)。"""
    v = _try_probe(binary)
    if v:
        return v
    time.sleep(2)
    return _try_probe(binary)


def remove_quarantine(binary: str):
    """去掉 macOS quarantine 属性,避免 Gatekeeper 拦截。"""
    if platform.system() == "Darwin":
        subprocess.run(["xattr", "-d", "com.apple.quarantine", binary],
                       capture_output=True)


_SEMVER_QUOTED_RE = re.compile(rb'"(\d{1,2}\.\d{1,2}\.\d{1,4})"')
_SEMVER_RAW_RE = re.compile(rb'(\d{1,2}\.\d{1,2}\.\d{1,4})')


def probe_version_from_data(data: bytes):
    """从二进制数据中提取版本号(当 --version 探测失败时的降级方案)。

    先扫描源码文本中带引号的 semver 模式;bytecode 构建上再验证常量池
    唯一性,找不到时降级扫不带引号的模式(常量池条目是原始字节)。
    """
    candidates = set()
    for m in _SEMVER_QUOTED_RE.finditer(data):
        v = m.group(1).decode("ascii", errors="ignore")
        if int(v.split(".")[0]) <= 19:
            candidates.add(v)
    is_bc = bytecode.is_bytecode_build(data)
    if is_bc:
        pool_hits = [v for v in candidates if bytecode.unique(data, v)]
        if len(pool_hits) == 1:
            return pool_hits[0]
        extra = set()
        for m in _SEMVER_RAW_RE.finditer(data):
            v = m.group(1).decode("ascii", errors="ignore")
            if v not in candidates and int(v.split(".")[0]) <= 19:
                extra.add(v)
        pool_hits = [v for v in extra if bytecode.unique(data, v)]
        if len(pool_hits) == 1:
            return pool_hits[0]
        return None
    if not candidates:
        return None
    shortest = min(candidates, key=len)
    if len(shortest) <= 10:
        return shortest
    return None


def safe_label(label: str) -> str:
    """版本标签用于文件名: 过滤 shell/glob 危险字符(如 probe 失败的 '?')。"""
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in label)
    return cleaned or "unknown"


def resolve_out(project_root: pathlib.Path, label: str,
                out: str = None, force: bool = False) -> str:
    """定义文件输出路径。已存在且未 --force 时写 .draft.toml,不覆盖原文件。"""
    if out is None:
        out = str(project_root / "patches" / f"{label}.toml")
    if pathlib.Path(out).exists() and not force:
        base = out[:-5] if out.endswith(".toml") else out
        out = base + ".draft.toml"
    return out


def load_design(project_root: pathlib.Path, design_name: str) -> dict:
    from . import tomlmini

    path = project_root / "design" / design_name
    with open(path, "r", encoding="utf-8") as f:
        return tomlmini.loads(f.read())


def _show_preview(patches, name, version, theme_color=None, design=None,
                  rewrote_real_version=False):
    """原图标 vs 新图标对照 + 名字/版本/颜色汇总。"""
    for p in patches:
        if p.get("art_old") and p.get("art_new"):
            ui.info(ui.bold("图标对照(左原 / 右新):"))
            for old_line, new_line in zip(p["art_old"], p["art_new"]):
                ui.info("  " + old_line.ljust(14) + ui.dim("→") + "  " + new_line)
            break
    else:
        # 常量池通道逐条替换,没有「整表」可对照,直接渲染设计稿
        art = discovery.pool_art(design) if design else None
        if art and any(p.get("cat") == "icon" for p in patches):
            ui.info(ui.bold("新图标:"))
            for line in art:
                ui.info("  " + line)
    n_name = sum(p["expected"] for p in patches if p.get("cat") == "name")
    n_ver = sum(p["expected"] for p in patches if p.get("cat") == "version")
    n_color = sum(p["expected"] for p in patches if p.get("cat") == "color")
    if n_name:
        ui.info(ui.bold("名字:") + "  " + ui.arrow('"Claude Code"', f'"{name}"')
                + ui.dim(f"(共 {n_name} 处显示位)"))
    if n_ver:
        note = ("内部版本常量已被改写,--version 与更新检查同步变化"
                if rewrote_real_version else
                f"共 {n_ver} 处显示位,内部版本常量不动")
        ui.info(ui.bold("版本:") + "  " + ui.arrow("真实版本", version)
                + ui.dim(f"({note})"))
    if n_color and theme_color:
        hexs = "%02X%02X%02X" % theme_color
        ui.info(ui.bold("主题色:") + "  " + ui.arrow("品牌橙", f"#{hexs}")
                + ui.dim(f"(共 {n_color} 处主题定义,ansi 主题不变)"))


def run(binary: str, cfg: dict, project_root: pathlib.Path,
        label: str = None, out: str = None, use_llm: bool = False,
        force: bool = False) -> int:
    ui.step("读取二进制")
    data = pathlib.Path(binary).read_bytes()
    ui.info(f"{binary} " + ui.dim(f"({len(data):,} bytes)"))

    # cp/下载来的文件可能带 quarantine,先去掉,否则后面 --version 探测会被 Gatekeeper 杀
    remove_quarantine(binary)

    display = cfg.get("display", {})
    raw_name = display.get("name", "")
    version = display.get("version", "")
    keep_icon = bool(display.get("keep_icon"))
    real_version = bool(display.get("real_version"))
    design_name = display.get("icon_design", "octopus.toml")
    color_raw = display.get("theme_color", "")
    try:
        theme_color = discovery.parse_hex_color(color_raw)
    except ValueError as e:
        ui.fail(f"配置错误: display.theme_color {e}")
        return 1
    ui.info(f"显示配置: 名字 {ui.bold(raw_name)}"
            + (" · 版本 " + ui.bold(version) if not real_version else " · 真实版本")
            + (" · 设计稿 " + design_name if not keep_icon else " · 保持原图标")
            + (f" · 主题色 {ui.bold('#' + color_raw.lstrip('#'))}" if theme_color else ""))

    # 名字 ≤NAME_MAX 字符即可,不足自动居中补齐(与原串 "Claude Code" 等长)
    try:
        name = discovery.validate_name(raw_name)
    except ValueError as e:
        ui.fail(f"配置错误: {e}")
        return 1
    if name != raw_name:
        ui.info(f"名字不足 {discovery.NAME_MAX} 字符,已居中补齐: {raw_name!r} → {name!r}")
    # real_version 时版本串不会被使用,不做校验
    rewrite_version = bool(display.get("rewrite_real_version"))
    if not real_version:
        try:
            discovery.validate_version(
                version, max_len=None if rewrite_version else discovery.VERSION_MAX)
        except ValueError as e:
            ui.fail(f"配置错误: {e}")
            return 1

    design = None if keep_icon else load_design(project_root, design_name)
    version_arg = None if real_version else version

    ui.step("发现锚点")
    probed = probe_version(binary)
    if not probed:
        probed = probe_version_from_data(data)
        if probed:
            ui.info(f"--version 探测失败,从二进制数据提取到版本: {probed}")
    patches, report, problems, warnings = discovery.build_patches(
        data, name, version_arg, design, theme_color=theme_color,
        real_version=probed,
        rewrite_real_version=rewrite_version)
    for line in report:
        ui.ok(line)
    for line in warnings:
        ui.warn(line)
    for line in problems:
        ui.fail(line)

    label = safe_label(label or probed or "unknown")
    if label == "unknown":
        ui.warn("--version 探测失败,定义文件将用 unknown 命名")
    out = resolve_out(project_root, label, out=out, force=force)
    meta = {
        "target_version": label,
        "design": design_name,
        "display_name": name,
        "display_version": version,
        "theme_color": color_raw.lstrip("#") if theme_color else "",
        # 无问题时自动置真;有问题走 draft 分支,保持 false
        "verified": not problems,
    }

    if problems:
        pathlib.Path(out).write_text(defio.dumps(meta, patches), encoding="utf-8")
        ui.warn(f"有 {len(problems)} 个问题,已写出草稿(含已发现部分): {out}")
        if use_llm:
            ui.step("LLM 辅助分析(仅建议,不改文件)")
            try:
                ctx = discovery.collect_contexts(data)
                print(llm_mod.assist(cfg, problems, ctx))
            except Exception as e:  # noqa: BLE001
                ui.fail(f"LLM 调用失败: {e}")
        return 2

    ui.step("替换预览")
    _show_preview(patches, name, version, theme_color, design,
                  rewrote_real_version=rewrite_version
                  and any(p.get('cat') == 'version' for p in patches))

    pathlib.Path(out).write_text(defio.dumps(meta, patches), encoding="utf-8")
    ui.step("完成")
    ui.ok(f"补丁定义已生成: {ui.bold(out)}(共 {len(patches)} 条替换,verified=true)")
    ui.info("下一步(或者直接用 all 一把梭: analyze→patch→sign→verify):")
    ui.info(f"  python3 -m patcher patch {binary} {out} --apply")
    return 0
