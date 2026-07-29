"""analyze → patch → sign → verify 的共享流水线。

cli 的 `all` 命令和交互向导 `skin` 都走这里,避免两份拷贝各自漂移。
"""
import pathlib

from . import analyze, patch as patch_mod, sign, ui, verify


def run(binary: str, cfg: dict, project_root: pathlib.Path, *,
        label: str = None, out: str = None, use_llm: bool = False,
        force: bool = False, yes: bool = False, pty: bool = False,
        apply: bool = True) -> int:
    """全流程执行;apply=False 时只做 analyze(dry-run)。返回退出码。"""
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
