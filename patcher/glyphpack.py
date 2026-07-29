"""把图标设计稿(可见字符)打包成与原型等长的 \\uXXXX 转义串。

原理: 二进制内嵌的是 JS 源码文本, logo 字符串由两类 token 组成 —
  - lit: 1 字节的字面字符(一般是空格)
  - esc: 6 字节的 \\uXXXX 转义
替换必须保持每个槽位的 token 序列形状(数量/位置/类型)完全一致,
总字节数才不变,文件内后续偏移才不会错位。
"""
import re

ESC_RE = re.compile(r"\\u([0-9A-Fa-f]{4})")


def tokenize(content: str):
    """把 JS 字符串内容(源码形式,反斜杠是字面两个字符)切成 token 列表。

    返回 [("lit"|"esc", 字符), ...]
    """
    tokens, i = [], 0
    while i < len(content):
        m = ESC_RE.match(content, i)
        if m:
            tokens.append(("esc", chr(int(m.group(1), 16))))
            i += 6
        else:
            tokens.append(("lit", content[i]))
            i += 1
    return tokens


def render(tokens) -> str:
    """token 列表解码成可见字符(用于预览)。"""
    return "".join(ch for _, ch in tokens)


def decode_escapes(s: str) -> str:
    """把源码形式的 \\uXXXX 序列解码成可见字符(用于预览)。"""
    return ESC_RE.sub(lambda m: chr(int(m.group(1), 16)), s)


def pack(tokens, glyphs: str) -> str:
    """按原 token 形状,用设计稿的可见字符重新生成源码字符串。"""
    chars = list(glyphs)
    if len(chars) != len(tokens):
        raise ValueError(
            f"设计稿字符数({len(chars)})与槽位数({len(tokens)})不一致: {glyphs!r}"
        )
    out = []
    for (kind, _), ch in zip(tokens, chars):
        code = ord(ch)
        if kind == "lit":
            # 字面位直接嵌进 JS 字符串/模板字面量,排除会破坏宿主语法的字符
            if not (0x20 <= code <= 0x7E) or ch in '"\\`$':
                raise ValueError(
                    f"字面位(lit)只能放可打印 ASCII,且不能是引号/反斜杠/反引号/$: {ch!r}")
            out.append(ch)
        else:
            if code > 0xFFFF:
                raise ValueError(f"只支持 BMP 字符: {ch!r}")
            out.append(f"\\u{code:04X}")
    return "".join(out)
