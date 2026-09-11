"""补丁定义文件(patches/*.toml)的读写。

定义文件是纯字面量替换条目清单(analyze 生成,人工核对后使用):
  [meta]
  target_version = '2.1.214'
  design = 'rocket.toml'
  verified = false        # 人工/实测核对后改成 true

  [[patch]]
  name = 'name_title'
  cat = 'name'            # icon / name / version,分组展示用
  desc = '启动标题: children:"Claude Code" → children:" Loop Code "'
  old = 'children:"Claude Code"'
  new = 'children:" Loop Code "'
  expected = 2

old/new 里的真实换行由 tomlmini.emit_str 转义。
"""
from . import tomlmini


def dumps(meta: dict, patches: list) -> str:
    lines = [
        "# 由 patcher analyze 生成(锚点全部发现时 verified 自动为 true,可直接用)",
        "[meta]",
    ]
    for k, v in meta.items():
        lines.append(f"{k} = {tomlmini.emit_value(v)}")
    for p in patches:
        old_b = p.get("old")
        lines += ["", "[[patch]]", f"name = {tomlmini.emit_str(p['name'])}"]
        if p.get("cat"):
            lines.append(f"cat = {tomlmini.emit_str(p['cat'])}")
        if p.get("desc"):
            lines.append(f"desc = {tomlmini.emit_str(p['desc'])}")
        if old_b is not None:
            lines.append(f"old = {tomlmini.emit_str(old_b.decode('ascii'))}")
        else:
            # offset+sha1 模式: 不存原文(法务考量),patch 时从二进制现读现验
            lines.append(f"sha1 = {tomlmini.emit_str(p['sha1'])}")
        if p.get("offset") is not None:
            lines.append(f"offset = {p['offset']}")
        # 常量池条目是 UTF-16,字节里带 NUL,按 enc 存可读文本而不是裸字节
        enc = p.get("enc") or "ascii"
        if enc != "ascii":
            lines.append(f"enc = {tomlmini.emit_str(enc)}")
        lines += [
            f"new = {tomlmini.emit_str(p['new'].decode(enc))}",
            f"expected = {p['expected']}",
        ]
    return "\n".join(lines) + "\n"


def loads(text: str):
    doc = tomlmini.loads(text)
    meta = doc.get("meta", {})
    patches = []
    for i, p in enumerate(doc.get("patch", [])):
        try:
            enc = p.get("enc", "ascii")
            if enc not in ("ascii", "utf-16-le", "latin-1"):
                raise ValueError(f"不支持的 enc: {enc!r}")
            old = p["old"].encode("ascii") if "old" in p else None
            if old is None:
                if "sha1" not in p:
                    raise KeyError("old/sha1")
                if "offset" not in p:
                    raise KeyError("offset")
            new = p["new"].encode(enc)
            expected = int(p["expected"])
            if old is not None and len(old) != len(new):
                raise ValueError(f"old/new 长度不等({len(old)} vs {len(new)})")
            if expected < 1:
                raise ValueError(f"expected 必须 >= 1,当前 {expected}")
            patches.append({
                "name": p.get("name", f"patch_{i}"),
                "cat": p.get("cat", ""),
                "desc": p.get("desc", ""),
                "old": old,
                "enc": enc,
                "sha1": p.get("sha1", ""),
                "offset": int(p["offset"]) if "offset" in p else None,
                "new": new,
                "expected": expected,
            })
        except KeyError as e:
            raise ValueError(f"第 {i} 条补丁缺字段 {e}") from None
        except ValueError as e:
            raise ValueError(f"第 {i} 条补丁非法: {e}") from None
    return meta, patches


def load_file(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return loads(f.read())
