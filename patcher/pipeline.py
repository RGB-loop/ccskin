"""analyze → patch → sign → verify 的共享流水线。

cli 的 `all` 命令和交互向导 `skin` 都走这里,避免两份拷贝各自漂移。
"""
import pathlib
import shutil

from . import analyze, patch as patch_mod, sign, ui, verify


def run(binary: str, cfg: dict, project_root: pathlib.Path, *,
        label: str = None, out: str = None, use_llm: bool = False,
        force: bool = False, yes: bool = False, pty: bool = False,
        apply: bool = True) -> int:
    """全流程执行;apply=False 时只做 analyze(dry-run)。返回退出码。"""
    analyze.remove_quarantine(binary)
    label = analyze.safe_label(label or analyze.probe_version(binary) or "unknown")
    out = analyze.resolve_out(project_root, label, out=out, force=force)
    # out 已定好(含 .draft 防覆盖),force=True 让 analyze 直接写它
    rc = analyze.run(binary, cfg, project_root,
                     label=label, out=out, use_llm=use_llm, force=True)
    if rc != 0 or not apply:
        return rc
    rc = patch_mod.run(binary, out, apply=True, yes=yes)
    if rc != 0:
        return rc
    rc = sign.run(binary, project_root)
    if rc != 0:
        return rc
    rc = verify.run(binary, cfg, pty=pty)
    if rc == 0:
        ui.step("全部完成")
        ui.ok(f"可以运行了: {binary}")
    return rc


def bundle(binary: str, cfg: dict, project_root: pathlib.Path, *,
           output_dir: str = None, suffix: str = "skin", **kw) -> int:
    """创建版本目录,放入原始 + 换肤二进制。

    目录结构: v{版本号}/{原始文件名} + v{版本号}/{原始文件名}{suffix}
    """
    analyze.remove_quarantine(binary)
    src = pathlib.Path(binary)
    ver = analyze.probe_version(binary)
    if not ver:
        data = src.read_bytes()
        ver = analyze.probe_version_from_data(data)
    if not ver:
        ui.fail("版本探测失败,无法确定目录名;请用 --label 显式指定")
        return 1

    bundle_dir = pathlib.Path(output_dir) if output_dir else src.parent / f"v{ver}"
    if bundle_dir.exists():
        ui.warn(f"目录已存在: {bundle_dir}")
    bundle_dir.mkdir(parents=True, exist_ok=True)

    orig_path = bundle_dir / src.name
    skin_path = bundle_dir / (src.stem + suffix + src.suffix)
    if not src.suffix:
        skin_path = bundle_dir / (src.name + suffix)

    ui.step("创建 bundle")
    shutil.copy2(binary, orig_path)
    ui.ok(f"原始文件: {orig_path}")
    shutil.copy2(binary, skin_path)
    ui.info(f"待换肤:   {skin_path}")

    rc = run(str(skin_path), cfg, project_root, **kw)
    if rc != 0:
        return rc

    backup = pathlib.Path(str(skin_path) + ".orig")
    if backup.exists():
        backup.unlink()

    ui.step("bundle 就绪")
    ui.ok(f"目录: {bundle_dir}")
    ui.info(f"  原始: {orig_path.name}")
    ui.info(f"  换肤: {skin_path.name}")
    return 0
