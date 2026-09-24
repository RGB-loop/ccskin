"""命令行入口。

  # 最简单: 交互式换肤向导(自动定位安装)
  python3 -m patcher skin

  # 非交互一把梭(参数缺省时自动定位系统里的 claude)
  python3 -m patcher all --apply -y --pty

  # 分步
  python3 -m patcher analyze [binary] [--label X]
  python3 -m patcher patch   [binary] patches/X.toml [--apply] [-y]
  python3 -m patcher sign    [binary]
  python3 -m patcher verify  [binary] [--pty]
  python3 -m patcher preview [design.toml]
"""
import argparse
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _resolve_binary(args):
    """binary 缺省时自动定位系统安装。"""
    if getattr(args, "binary", None):
        return args.binary
    from . import locate, ui

    path = locate.default_binary()
    if not path:
        ui.fail("未自动找到 Claude Code 安装,请显式传 binary 路径")
        raise SystemExit(1)
    ui.info(f"自动定位: {path}")
    return path


def _cmd_analyze(args, cfg):
    from . import analyze

    return analyze.run(
        _resolve_binary(args), cfg, PROJECT_ROOT,
        label=args.label, out=args.out, use_llm=args.llm, force=args.force,
    )


def _cmd_patch(args, cfg):
    from . import patch

    return patch.run(_resolve_binary(args), args.definition,
                     apply=args.apply, yes=args.yes)


def _cmd_sign(args, cfg):
    from . import sign

    return sign.run(_resolve_binary(args), PROJECT_ROOT)


def _cmd_verify(args, cfg):
    from . import verify

    return verify.run(_resolve_binary(args), cfg, pty=args.pty)


def _cmd_all(args, cfg):
    from . import pipeline

    binary = _resolve_binary(args)
    if args.bundle:
        if not args.apply:
            from . import ui
            ui.fail("--bundle 必须搭配 --apply 使用")
            return 1
        return pipeline.bundle(binary, cfg, PROJECT_ROOT,
                               output_dir=args.output_dir, suffix=args.suffix,
                               label=args.label, out=args.out, use_llm=args.llm,
                               force=args.force, yes=args.yes, pty=args.pty)
    if not args.apply:
        rc = pipeline.run(binary, cfg, PROJECT_ROOT,
                          label=args.label, out=args.out, use_llm=args.llm,
                          force=args.force, apply=False)
        if rc == 0:
            print("\n[all] 已完成分析(dry-run)。确认无误后执行:")
            print(f"  python3 -m patcher all {binary} --apply -y"
                  f"{' --pty' if args.pty else ''}")
        return rc
    return pipeline.run(binary, cfg, PROJECT_ROOT,
                        label=args.label, out=args.out, use_llm=args.llm,
                        force=args.force, yes=args.yes, pty=args.pty)


def _cmd_skin(args, cfg):
    from . import interactive

    return interactive.run(getattr(args, "binary", None), cfg, PROJECT_ROOT,
                           yes=args.yes, pty=args.pty,
                           bundle=args.bundle, suffix=args.suffix,
                           output_dir=args.output_dir)


def _cmd_preview(args, cfg):
    """渲染图标设计稿预览(不碰二进制)。"""
    from . import analyze

    design = analyze.load_design(PROJECT_ROOT, args.design)
    slots = design["slots"]
    mid = design.get("mid", {}).get("row", "█████")
    for variant, dslots in slots.items():
        row1 = dslots["r1L"] + dslots["r1E"] + dslots["r1R"]
        row2 = dslots["r2L"] + mid + dslots["r2R"]
        print(f"[{variant}]")
        print("   ", row1)
        print("   ", row2)
    feet = design.get("feet", {}).get("row")
    if feet:
        print("   ", " " + feet, " (第三行)")
    html = design.get("html", {})
    if html:
        print("[html 页]")
        for k in ("row1", "row2", "row3"):
            print("   ", html[k])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="patcher",
        description="Claude Code 换肤工具(仅显示层: 图标/名字/版本号/主题色)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("skin", help="交互式换肤向导(推荐)")
    p.add_argument("binary", nargs="?", help="不填则自动定位系统安装")
    p.add_argument("-y", "--yes", action="store_true", help="跳过最终确认")
    p.add_argument("--pty", action="store_true", help="最后抓真实启动画面验证")
    p.add_argument("--bundle", action="store_true",
                   help="创建版本目录,放入原始 + 换肤二进制")
    p.add_argument("--suffix", default="skin", help="换肤文件后缀(默认 skin)")
    p.add_argument("--output-dir", help="bundle 输出目录(默认在源文件旁建 v版本号/)")
    p.set_defaults(func=_cmd_skin)

    p = sub.add_parser("analyze", help="发现锚点,生成补丁定义 TOML")
    p.add_argument("binary", nargs="?")
    p.add_argument("--label", help="版本标签(默认跑 --version 探测)")
    p.add_argument("--out", help="定义文件输出路径")
    p.add_argument("--llm", action="store_true", help="发现失败时调用 LLM 辅助")
    p.add_argument("--force", action="store_true", help="覆盖已存在的定义文件")
    p.set_defaults(func=_cmd_analyze)

    p = sub.add_parser("patch", help="按定义做等长替换(默认 dry-run)")
    p.add_argument("binary", nargs="?")
    p.add_argument("definition")
    p.add_argument("--apply", action="store_true", help="实际写入(自动备份 .orig)")
    p.add_argument("-y", "--yes", action="store_true", help="跳过写入前的交互确认")
    p.set_defaults(func=_cmd_patch)

    p = sub.add_parser("sign", help="ad-hoc 重签名(保留 entitlements)")
    p.add_argument("binary", nargs="?")
    p.set_defaults(func=_cmd_sign)

    p = sub.add_parser("verify", help="验证: 预览/计数/--version/可选抓屏")
    p.add_argument("binary", nargs="?")
    p.add_argument("--pty", action="store_true", help="抓真实启动画面")
    p.set_defaults(func=_cmd_verify)

    p = sub.add_parser("all", help="analyze→patch→sign→verify 全流程")
    p.add_argument("binary", nargs="?")
    p.add_argument("--label")
    p.add_argument("--out")
    p.add_argument("--llm", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="覆盖已存在的定义文件(默认写 .draft.toml)")
    p.add_argument("--pty", action="store_true")
    p.add_argument("-y", "--yes", action="store_true", help="跳过写入前的交互确认")
    p.add_argument("--bundle", action="store_true",
                   help="创建版本目录,放入原始 + 换肤二进制")
    p.add_argument("--suffix", default="skin", help="换肤文件后缀(默认 skin)")
    p.add_argument("--output-dir", help="bundle 输出目录(默认在源文件旁建 v版本号/)")
    p.set_defaults(func=_cmd_all)

    p = sub.add_parser("preview", help="渲染图标设计稿预览")
    p.add_argument("design", nargs="?", default="octopus.toml", help="design/ 下的文件名")
    p.set_defaults(func=_cmd_preview)

    args = ap.parse_args(argv)
    from . import config, ui

    cfg = config.load(PROJECT_ROOT)
    try:
        return args.func(args, cfg)
    except KeyboardInterrupt:
        print()
        ui.warn("已取消")
        return 130
    except FileNotFoundError as e:
        ui.fail(f"文件不存在: {e.filename or e}")
        return 1
    except ValueError as e:
        ui.fail(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
