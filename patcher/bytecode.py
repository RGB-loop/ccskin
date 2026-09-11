"""bun/JSC bytecode 常量池: 条目定位与等长替换。

新版 claude 用 `bun build --bytecode` 编译。二进制里那份 JS 源码文本还在,
但运行时读的是 bytecode 常量池 —— 改源码文本能通过一切计数与 sha1 校验,
屏幕上却毫无变化。显示层补丁必须打到常量池。

条目布局(实测 2.1.261):
    <len:   u32 LE,高位 0x80000000 置位 = 8-bit(latin1),否则 UTF-16>
    <hash:  u32 LE(实际只用低 24 位)>
    <chars: len 个 1 字节,或 len 个 2 字节>
    <pad:   补 0 到 4 字节对齐>

hash 不参与运行时校验(实测改 chars 不改 hash 照常渲染),所以只替换 chars。
池是去重的: 内容相同的字面量在整个二进制里只有一条,因此同一条会被多处
共用 —— 调用方必须自己判断改它的连带范围。
"""
import re
import struct
from collections import namedtuple

Entry = namedtuple("Entry", "start chars nbytes eight text")

_ALIGN = 4
_EIGHT_BIT = 0x80000000
_HEADER = 8  # len(4) + hash(4)


def encode(text: str, eight: bool) -> bytes:
    return text.encode("latin-1") if eight else text.encode("utf-16-le")


def find(data: bytes, text: str, region=None):
    """内容恰好等于 text 的常量池条目。池去重,正常是 0 或 1 条。

    region=(lo, hi) 用于给短串消歧 —— 单字符条目的字节模式很短,
    全文件搜索可能撞上随机数据。
    """
    if not text:
        return []
    lo, hi = region if region else (0, len(data))
    lo = max(0, lo)
    out = []
    for eight in (True, False):
        try:
            chars = encode(text, eight)
        except UnicodeEncodeError:
            continue
        pad = b"\x00" * (-len(chars) % _ALIGN)
        head = struct.pack("<I", (len(text) | _EIGHT_BIT) if eight else len(text))
        pat = re.escape(head) + b"...." + re.escape(chars) + re.escape(pad)
        for m in re.finditer(pat, data[lo:hi], re.DOTALL):
            start = lo + m.start()
            out.append(Entry(start, start + _HEADER, len(chars), eight, text))
    return sorted(out)


def unique(data: bytes, text: str, region=None):
    """恰好命中一条时返回该条目,否则返回 None。"""
    hits = find(data, text, region)
    return hits[0] if len(hits) == 1 else None


def region_for(data: bytes, texts, pad: int = 4096):
    """用若干唯一命中的串把一段池区间钉住,给同组的短串查找消歧。"""
    offs = [h[0].chars for h in (find(data, t) for t in texts) if len(h) == 1]
    return (min(offs) - pad, max(offs) + pad) if offs else None


def replacement(entry: Entry, new_text: str) -> bytes:
    """新 chars 字节;字符数必须与原条目一致(否则条目长度字段就得改)。"""
    if len(new_text) != len(entry.text):
        raise ValueError(
            f"字符数不一致: 条目 {entry.text!r} 有 {len(entry.text)} 字符,"
            f"替换串 {new_text!r} 有 {len(new_text)} 字符")
    buf = encode(new_text, entry.eight)
    if len(buf) != entry.nbytes:
        raise ValueError(
            f"{new_text!r} 无法以原编码({'latin-1' if entry.eight else 'UTF-16'})"
            f"等长写回 {entry.text!r}")
    return buf


def is_bytecode_build(data: bytes) -> bool:
    return b"// @bun @bytecode" in data


_ART_RE = re.compile(r"^[▀-▟─-╿ \n]+$")


def art_entries_in(data: bytes, lo: int, hi: int, min_len: int = 1):
    """扫出区间内所有「只由制表/方块字符组成」的 UTF-16 条目。

    用于发现上游新增或改名的 logo 串: 已知槽位都替换完之后,这里还剩下的
    就是没被设计稿覆盖到的图案,报出来比默默漏掉强。
    """
    out = []
    pos = max(0, lo)
    hi = min(hi, len(data))
    while pos + _HEADER < hi:
        (raw,) = struct.unpack_from("<I", data, pos)
        eight = bool(raw & _EIGHT_BIT)
        n = raw & ~_EIGHT_BIT
        if not (1 <= n <= 512):
            pos += _ALIGN
            continue
        nbytes = n if eight else n * 2
        cstart = pos + _HEADER
        if cstart + nbytes > hi:
            pos += _ALIGN
            continue
        chunk = data[cstart:cstart + nbytes]
        try:
            text = chunk.decode("latin-1" if eight else "utf-16-le")
        except UnicodeDecodeError:
            pos += _ALIGN
            continue
        if not eight and len(text) >= min_len and _ART_RE.match(text):
            out.append(Entry(pos, cstart, nbytes, eight, text))
            pos = cstart + nbytes + (-nbytes % _ALIGN)
            continue
        pos += _ALIGN
    return out
