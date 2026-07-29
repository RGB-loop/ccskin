"""skin — 交互式换肤向导。

零配置上手: 自动定位安装 → 可视化选图标/名字/版本/主题色 →
确认后走共享 pipeline(analyze→patch→sign→verify),并把选择存进 config.toml。
"""
import pathlib
import sys

from . import analyze, discovery, locate, pipeline, tomlmini, ui

ICON_GALLERY = ["octopus.toml", "rocket.toml", "invader.toml", "ghost.toml"]

COLOR_GALLERY = [
    ("保持原橙 #D97757", ""),
    ("天际蓝 #2E9BFF", "2E9BFF"),
    ("青梅绿 #00B96B", "00B96B"),
    ("樱粉   #E0498A", "E0498A"),
    ("紫罗兰 #827DBD", "827DBD"),
]


def _render_design_rows(design: dict):
    mid = design.get("mid", {}).get("row", "█████")
    dslots = design["slots"]["default"]
    return [
        dslots["r1L"] + dslots["r1E"] + dslots["r1R"],
        dslots["r2L"] + mid + dslots["r2R"],
        "  " + design.get("feet", {}).get("row", ""),
    ]


def _input(prompt: str) -> str:
    """input() 的包装: Ctrl-D(EOF)按取消处理,抛 KeyboardInterrupt 统一退出。"""
    try:
        return input(prompt)
    except EOFError:
        raise KeyboardInterrupt from None


def _menu(title: str, options: list, default: int = 1) -> int:
    """options: [label]。返回选中下标(0 起)。"""
    ui.info(ui.bold(title))
    for i, label in enumerate(options, 1):
        ui.info(f"  {i}) {label}")
    while True:
        s = _input(f"选择 [默认 {default}] > ").strip() or str(default)
        # 只认 ASCII 数字: 全角/上标等 unicode 数字 isdigit() 为真但 int() 会炸,
        # 阿拉伯-印度数字 int() 又能过,统一挡在门外并重新提示
        if s.isascii() and s.isdigit() and 1 <= int(s) <= len(options):
            return int(s) - 1
        ui.warn("无效输入,重新输入")


def _pick_binary(binary):
    """指定了就用,否则自动定位。"""
    if binary:
        return binary
    installs = locate.find_installations()
    if not installs:
        return None
    if len(installs) > 1:
        for p, kind in installs:
            tag = "原生二进制" if kind == locate.KIND_MACHO else (
                "npm 安装(暂不支持)" if kind == locate.KIND_JS else "未知格式")
            ui.info(f"  发现: {p}  [{tag}]")
    for p, kind in installs:
        if kind == locate.KIND_MACHO:
            return p
    return None


def _ask_name(default: str) -> str:
    while True:
        name = _input(f"显示名(≤{discovery.NAME_MAX} 字符,不足自动补齐)"
                      f" [{default}] > ").strip() or default
        try:
            discovery.validate_name(name)
            return name
        except ValueError as e:
            ui.warn(str(e))


def _ask_version() -> str:
    while True:
        v = _input(f"版本号(≤{discovery.VERSION_MAX} 字符) > ").strip()
        try:
            return discovery.validate_version(v)
        except ValueError as e:
            ui.warn(str(e))


def _ask_hex() -> str:
    while True:
        s = _input("6 位 hex(如 2E9BFF) > ").strip().lstrip("#")
        try:
            if discovery.parse_hex_color(s):
                return s
            ui.warn("不能为空,重新输入")
        except ValueError as e:
            ui.warn(str(e))


def run(binary, cfg, project_root: pathlib.Path, yes=False, pty=False) -> int:
    if not sys.stdin.isatty():
        ui.fail("skin 是交互向导,请在终端里运行;非交互请用 all --apply -y")
        return 1

    ui.step("定位 Claude Code")
    path = _pick_binary(binary)
    if not path:
        ui.fail("未找到原生二进制安装(npm 安装暂不支持,可手动传路径)")
        return 1
    version = analyze.probe_version(path)
    ui.ok(f"{path} " + ui.dim(f"({version or '版本未知'})"))

    display = cfg.get("display", {})

    ui.step("1/4 选择图标")
    options, files = [], []
    for f in ICON_GALLERY:
        try:
            design = analyze.load_design(project_root, f)
            rows = _render_design_rows(design)
        except Exception as e:  # noqa: BLE001 — 坏设计稿跳过并提示,不中断向导
            ui.warn(f"设计稿 {f} 加载失败,跳过: {e}")
            continue
        name = design.get("meta", {}).get("name", f)
        options.append(f"{name:<6} " + "  ".join(rows))
        files.append(f)
    options.append("保持原图标(不改)")
    files.append("")
    cur_icon = display.get("icon_design")
    idx = _menu("图标预览(单行拼接显示):", options,
                default=files.index(cur_icon) + 1 if cur_icon in files else 1)
    icon_design = files[idx]

    ui.step("2/4 显示名")
    name = _ask_name(display.get("name", "Loop Code"))

    ui.step("3/4 版本号显示")
    vcur = display.get("version", "v9.99")
    vidx = _menu(f"版本位最多 {discovery.VERSION_MAX} 字符(物理限制):",
                 [f"{vcur}(保持)", "显示真实版本", f"自定义(≤{discovery.VERSION_MAX} 字符)"],
                 default=1)
    if vidx == 0:
        version_s = vcur
    elif vidx == 1:
        version_s = ""
    else:
        version_s = _ask_version()

    ui.step("4/4 主题色")
    cidx = _menu("logo/边框/高亮的主色:",
                 [label for label, _ in COLOR_GALLERY] + ["自定义 hex..."], default=1)
    theme_color = COLOR_GALLERY[cidx][1] if cidx < len(COLOR_GALLERY) else _ask_hex()

    ui.step("确认")
    ui.info(f"  图标:   {icon_design or '(保持原图标)'}")
    ui.info(f"  名字:   {name!r}")
    ui.info(f"  版本:   {version_s or '(显示真实版本)'}")
    ui.info(f"  主题色: {('#' + theme_color) if theme_color else '(保持原橙)'}")
    if not yes and not ui.confirm(f"对 {path} 应用以上皮肤?"):
        ui.warn("已取消")
        return 1

    new_cfg = dict(cfg)
    new_cfg["display"] = {
        "name": name,
        "version": version_s or display.get("version", "v9.99"),
        "icon_design": icon_design or display.get("icon_design", "octopus.toml"),
        "theme_color": theme_color,
        "keep_icon": not icon_design,
        "real_version": not version_s,
    }

    rc = pipeline.run(path, new_cfg, project_root,
                      label=version, yes=True, pty=pty)
    if rc == 0:
        _save_config(project_root, new_cfg)
    return rc


def _save_config(project_root: pathlib.Path, cfg: dict) -> None:
    """把完整配置写回 config.toml(含 keep_icon/real_version 和 [llm] 等其他节)。"""
    doc = dict(cfg)
    doc["display"] = dict(cfg["display"])
    text = "# 由 skin 向导生成\n" + tomlmini.dumps(doc)
    (project_root / "config.toml").write_text(text, encoding="utf-8")
