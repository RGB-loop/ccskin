"""极简 TOML 读写(项目自用,零依赖)。

Python 3.11+ 用标准库 tomllib;3.9/3.10 走本模块的手写解析器。两者对本模块
写出的文件解析出的结果必须一致 —— dumps()/emit_* 只生成两者都能读回原值的语法。

支持子集(读 + 写):
  [table] / [[array-of-tables]] / 点分键(a.b.c)
  值: literal & basic 字符串、十进制整数、浮点(含指数 / inf / nan)、bool、
      标量内联数组 [a, b]、# 行注释
本项目用不到、因而不支持(手写配置时请回避): 十六/八/二进制整数字面量、
  日期时间、多行字符串、内联表 {..}、跨行数组。
"""
import math
import re

# literal string(单引号)能原样容纳的内容: 除 tab(0x09)外的控制字符都不行,
# 单引号本身也不行 —— 命中任何一个就改用 basic string 逐字转义。
_LITERAL_UNSAFE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_BASIC_ESCAPES = {
    "\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t",
    "\r": "\\r", "\x08": "\\b", "\x0c": "\\f",
}
_HEX = set("0123456789abcdefABCDEF")
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def emit_str(s: str) -> str:
    """优先 literal string(补丁内容含大量字面反斜杠,不转义最安全);
    含单引号或(除 tab 外的)控制字符时改用 basic string 逐字转义。"""
    if "'" not in s and not _LITERAL_UNSAFE.search(s):
        return "'" + s + "'"
    out = []
    for ch in s:
        if ch in _BASIC_ESCAPES:
            out.append(_BASIC_ESCAPES[ch])
        elif _CTRL.match(ch):
            out.append("\\u%04X" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def emit_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, str):
        return emit_str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(emit_value(x) for x in v) + "]"
    raise TypeError(f"tomlmini 无法序列化类型 {type(v).__name__}: {v!r}")


def _emit_key(k: str) -> str:
    return k if _BARE_KEY.fullmatch(k) else emit_str(k)


def dumps(doc: dict) -> str:
    """写出任意层级的表结构。标量/数组键写在各自子表头之前(TOML 顺序要求)。"""
    lines: list = []
    _emit_table(doc, (), lines)
    return "\n".join(lines).strip("\n") + "\n"


def _emit_table(table: dict, path: tuple, lines: list) -> None:
    scalars = [(k, v) for k, v in table.items() if not isinstance(v, dict)]
    subtables = [(k, v) for k, v in table.items() if isinstance(v, dict)]
    if path:
        if lines:
            lines.append("")
        lines.append("[" + ".".join(_emit_key(p) for p in path) + "]")
    for k, v in scalars:
        lines.append(f"{_emit_key(k)} = {emit_value(v)}")
    for k, v in subtables:
        _emit_table(v, path + (k,), lines)


def _unescape_basic(body: str) -> str:
    """按 TOML 规则逐字符解转义。

    不能用链式 str.replace: 先替换 \\n 再替换 \\\\ 会把 '\\\\n'
    (字面反斜杠 + n)错解成 '反斜杠 + 真换行'。必须左到右单次扫描。
    """
    simple = {"n": "\n", "t": "\t", "r": "\r", '"': '"',
              "\\": "\\", "b": "\x08", "f": "\x0c"}
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(body):
            raise ValueError("字符串末尾有孤立反斜杠")
        e = body[i + 1]
        if e in simple:
            out.append(simple[e])
            i += 2
        elif e in ("u", "U"):
            n = 4 if e == "u" else 8
            hexs = body[i + 2:i + 2 + n]
            if len(hexs) != n or any(h not in _HEX for h in hexs):
                raise ValueError(f"非法 \\{e} 转义: {body[i:i + 2 + n]!r}")
            out.append(chr(int(hexs, 16)))
            i += 2 + n
        else:
            raise ValueError(f"未知转义序列: \\{e}")
    return "".join(out)


try:
    import tomllib as _stdlib

    def loads(text: str):
        return _stdlib.loads(text)

except ModuleNotFoundError:  # Python < 3.11: 手写兜底,行为对齐 tomllib

    def _strip_comment(line: str) -> str:
        out, i, in_s, in_d = [], 0, False, False
        while i < len(line):
            c = line[i]
            if in_s:
                out.append(c)
                if c == "'":
                    in_s = False
                i += 1
            elif in_d:
                out.append(c)
                if c == "\\" and i + 1 < len(line):
                    out.append(line[i + 1])
                    i += 2
                else:
                    if c == '"':
                        in_d = False
                    i += 1
            elif c == "'":
                in_s = True
                out.append(c)
                i += 1
            elif c == '"':
                in_d = True
                out.append(c)
                i += 1
            elif c == "#":
                break
            else:
                out.append(c)
                i += 1
        return "".join(out)

    def _parse_value(s: str):
        s = s.strip()
        if not s:
            raise ValueError("空值")
        if s[0] == "'":
            # literal string: 内容不解转义,但引号必须正好收尾
            if len(s) < 2 or not s.endswith("'") or "'" in s[1:-1]:
                raise ValueError(f"bad literal string: {s}")
            return s[1:-1]
        if s[0] == '"':
            if len(s) < 2 or not s.endswith('"'):
                raise ValueError(f"bad string: {s}")
            body = s[1:-1]
            # 未转义的内部双引号 = 语法错误(tomllib 同样拒绝)
            i = 0
            while i < len(body):
                if body[i] == "\\":
                    i += 2
                    continue
                if body[i] == '"':
                    raise ValueError(f"bad string: {s}")
                i += 1
            return _unescape_basic(body)
        if s[0] == "[":
            return _parse_array(s)
        if s in ("true", "false"):
            return s == "true"
        if re.fullmatch(r"[+-]?inf", s):
            return float(s)
        if re.fullmatch(r"[+-]?nan", s):
            return float("nan")
        if re.fullmatch(r"[+-]?(0|[1-9](_?[0-9])*)", s):
            return int(s.replace("_", ""))
        # 浮点: 至少要有小数点或指数,否则归为整数(与 tomllib 一致)
        if (re.fullmatch(
                r"[+-]?(0|[1-9](_?[0-9])*)(\.[0-9](_?[0-9])*)?([eE][+-]?[0-9](_?[0-9])*)?", s)
                and re.search(r"[.eE]", s)):
            return float(s.replace("_", ""))
        raise ValueError(f"unsupported value: {s!r}")

    def _parse_array(s: str):
        s = s.strip()
        if not s.endswith("]"):
            raise ValueError(f"数组未闭合: {s}")
        return [_parse_value(part) for part in _split_top_commas(s[1:-1])]

    def _split_top_commas(inner: str):
        """按顶层逗号切分数组内容,跳过字符串与嵌套括号内的逗号。"""
        parts, buf, i, depth = [], [], 0, 0
        while i < len(inner):
            c = inner[i]
            if c in "'\"":
                j = i + 1
                if c == "'":
                    while j < len(inner) and inner[j] != "'":
                        j += 1
                else:
                    while j < len(inner):
                        if inner[j] == "\\":
                            j += 2
                            continue
                        if inner[j] == '"':
                            break
                        j += 1
                buf.append(inner[i:j + 1])
                i = j + 1
            elif c == "[":
                depth += 1
                buf.append(c)
                i += 1
            elif c == "]":
                depth -= 1
                buf.append(c)
                i += 1
            elif c == "," and depth == 0:
                parts.append("".join(buf))
                buf = []
                i += 1
            else:
                buf.append(c)
                i += 1
        tail = "".join(buf).strip()
        if tail:                       # 末元素;尾逗号(tail 为空)按 TOML 规则忽略
            parts.append(tail)
        return parts

    def _split_key(key: str):
        parts, cur, in_d = [], [], False
        for c in key.strip():
            if c == '"':
                in_d = not in_d
            elif c == "." and not in_d:
                parts.append("".join(cur).strip().strip('"'))
                cur = []
            else:
                cur.append(c)
        parts.append("".join(cur).strip().strip('"'))
        return [p for p in parts if p]

    def loads(text: str):
        root: dict = {}
        cur = root
        declared = set()          # 已声明的 [table] 路径
        keyed = set()             # 已赋值的 (表路径, key)
        cur_path: tuple = ()
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = _strip_comment(raw).strip()
            if not line:
                continue
            try:
                if line.startswith("[[") and line.endswith("]]"):
                    path = _split_key(line[2:-2])
                    parent = root
                    for p in path[:-1]:
                        parent = parent.setdefault(p, {})
                    arr = parent.setdefault(path[-1], [])
                    if not isinstance(arr, list):
                        raise ValueError("not an array")
                    cur = {}
                    arr.append(cur)
                    cur_path = tuple(path) + (len(arr) - 1,)
                elif line.startswith("["):
                    if not line.endswith("]"):
                        raise ValueError("bad section header")
                    path = _split_key(line[1:-1])
                    if tuple(path) in declared:
                        raise ValueError(f"表 [{'.'.join(path)}] 重复声明")
                    declared.add(tuple(path))
                    cur = root
                    for p in path:
                        cur = cur.setdefault(p, {})
                    cur_path = tuple(path)
                else:
                    key, _, val = line.partition("=")
                    if not _:
                        raise ValueError("expected key = value")
                    k = key.strip().strip('"')
                    if (cur_path, k) in keyed:
                        raise ValueError(f"键 {k!r} 重复赋值")
                    keyed.add((cur_path, k))
                    cur[k] = _parse_value(val)
            except ValueError as e:
                raise ValueError(f"TOML 第 {lineno} 行: {e}: {raw.strip()}") from None
        return root

