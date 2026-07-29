"""step 3 — sign: 补丁后重新签名(macOS arm64 必须)。

原二进制是 Apple 开发者签名 + hardened runtime,改一个字节签名即失效。
做法: 从二进制导出 entitlements(codesign -d 不校验签名,补丁后也能导),
剥掉旧签名,ad-hoc 重签,保留 entitlements(JIT 等)和 hardened runtime;
导不出时回退用仓库里保存的 claude_entitlements.plist。
"""
import pathlib
import platform
import subprocess

FALLBACK_ENT = "claude_entitlements.plist"


def _sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def run(binary: str, project_root: pathlib.Path) -> int:
    if platform.system() != "Darwin":
        print("[sign] 非 macOS,跳过签名步骤")
        return 0
    fallback = project_root / FALLBACK_ENT

    r = _sh(["codesign", "-d", "--entitlements", ":-", binary])
    if r.returncode == 0 and "<plist" in r.stdout:
        # 写到 backups/,不覆盖仓库里提交的 fallback 文件
        ent_dir = project_root / "backups"
        ent_dir.mkdir(exist_ok=True)
        ent_path = ent_dir / "entitlements.plist"
        ent_path.write_text(r.stdout, encoding="utf-8")
        print(f"[sign] entitlements 已从二进制导出: {ent_path}")
    elif fallback.exists():
        ent_path = fallback
        print(f"[sign] 二进制签名已失效,回退使用已保存的 {ent_path}")
    else:
        print("[sign] 无法获得 entitlements(二进制无签名且本地无备份)")
        return 1

    _sh(["codesign", "--remove-signature", binary])
    r = _sh([
        "codesign", "--force", "--sign", "-",
        "--options", "runtime",
        "--entitlements", str(ent_path),
        binary,
    ])
    if r.returncode != 0:
        print(f"[sign] 签名失败:\n{r.stderr}")
        return 1
    r = _sh(["codesign", "-v", binary])
    if r.returncode != 0:
        print(f"[sign] 校验失败:\n{r.stderr}")
        return 1
    print("[sign] ad-hoc 重签名完成并通过 codesign -v")

    # 顺带去掉隔离属性(有则去,无则忽略),避免首次运行被 Gatekeeper 拦
    x = _sh(["xattr", binary])
    if "com.apple.quarantine" in x.stdout:
        _sh(["xattr", "-d", "com.apple.quarantine", binary])
        print("[sign] 已自动去除 quarantine 隔离属性")
    else:
        print("[sign] 无 quarantine 属性,无需处理")
    return 0
