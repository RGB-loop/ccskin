"""锚点发现与替换内容生成。

核心原则: 锚点只依赖字符串 *内容*(\\uXXXX 转义序列 / 显示文本),
不依赖压缩后的变量名(M2p/Ida/YZy...)和文件偏移 —— 后两者每个版本都变。

已知显示位:
  - logo 像素表: {default:{r1L:..,r1E:..,r1R:..,r2L:..,r2R:..}, "look-left":{..},
    "look-right":{..}, "arms-up":{..}} 加一张 4 行底部小脚表(N2p)
  - logo 第三行小脚: "\\u2598\\u2598 \\u259D\\u259D"(大 logo 与小 logo 各一处)
  - HTML 登录/错误页 logo: 反引号模板里的 3 行转义艺术字
  - 产品名: children:"Claude Code" / ("Claude Code") / (" Claude Code ") /
    ["Claude Code"," "]
  - 版本号: 名字附近的 ["v",变量] 与 `v${变量}`(变量名每版不同,正则提取)

每个 patch 条目除 old/new/expected 外还带:
  cat    分类(icon/name/version),用于分组展示
  desc   人类可读的替换说明(写入定义文件注释)
  offset 首个命中偏移(便于人工用 grep -abo 交叉核对)
"""
import hashlib
import re

from . import glyphpack

# ---------- 稳定锚点(raw 字符串,保留字面反斜杠) ----------
_LOGO_ANCHOR = br'r1L:" \u2590"'          # logo 表第一个槽位
_FEET_CONTENT = r'\u2598\u2598 \u259D\u259D'      # logo 第三行(▘▘ ▝▝)
_HTML_R1_CONTENT = r' \u2590\u259B\u2588\u2588\u2588\u259C\u258C'  # HTML 页 logo 第 1 行(前导空格)
_HTML_R2_CONTENT = r'\u259D\u259C\u2588\u2588\u2588\u2588\u2588\u259B\u2598'  # 第 2 行
_HTML_R3_CONTENT = r'  \u2598\u2598 \u259D\u259D'          # 第 3 行(两前导空格)
_HTML_R3_SUFFIX = '`;'                        # 模板结束标记
_MID_CONTENT = r'\u2588\u2588\u2588\u2588\u2588'      # logo 第二行机身中部(█████,常量)
_MID_CTX = 'clawd_background",children:'       # 该常量的上下文锚点(5×█ 在文件里出现十几次,必须带上下文)

# ---------- 版本号显示位(变量名每版不同,用正则抓) ----------
_VER_ARR_RE = re.compile(br'\["v",[A-Za-z_$][A-Za-z0-9_$]{0,5}\]')
_VER_TPL_RE = re.compile(br'`v\$\{[A-Za-z_$][A-Za-z0-9_$]{0,5}\}`')
_VER_WINDOW = 800  # 名字锚点向后找版本位的窗口
VERSION_MAX = 5    # 版本显示串上限(受最窄的 ["v",X] 显示位宽度限制)

# ---------- 名字显示位 ----------
NAME_MAX = 11  # 原串 "Claude Code" 的字符数,替换必须等长

NAME_SITES = [
    ("name_title",          'children:"Claude Code"', lambda n: f'children:"{n}"', "启动/面板标题"),
    ("name_border",         '("Claude Code")',        lambda n: f'("{n}")',        "输入框边框(展开)"),
    ("name_border_compact", '(" Claude Code ")',      lambda n: f'(" {n} ")',      "输入框边框(紧凑)"),
    ("name_header",         '["Claude Code"," "]',    lambda n: f'["{n}"," "]',    "头部显示"),
]


_JS_UNSAFE = '"\\`$'


def _js_bad_chars(raw: str) -> list:
    """会破坏内嵌 JS 语法或无法等长替换的字符(非可打印 ASCII / " \\ ` $)。

    这些串(名字、版本)原样嵌进二进制内的 JS 字符串/模板字面量:
    非 ASCII 是多字节没法等长替换;引号/反斜杠/反引号/$ 会破坏宿主语法,
    而完整性检查发现不了(字节数没变,签名却签了个语法坏掉的二进制)。
    """
    return [ch for ch in raw if not (0x20 <= ord(ch) <= 0x7E) or ch in _JS_UNSAFE]


def validate_name(raw: str) -> str:
    """校验显示名并居中补齐到 NAME_MAX 个字符;不合法抛 ValueError。"""
    if not raw:
        raise ValueError("display.name 为空")
    bad = _js_bad_chars(raw)
    if bad:
        raise ValueError(
            f"display.name 含不支持的字符 {bad[0]!r}: 只允许可打印 ASCII,"
            "且不能是引号/反斜杠/反引号/$(会破坏二进制内嵌的 JS)")
    if len(raw) > NAME_MAX:
        raise ValueError(f"display.name 超过 {NAME_MAX} 字符({len(raw)}): {raw!r}")
    pad = NAME_MAX - len(raw)
    return " " * (pad // 2) + raw + " " * (pad - pad // 2)


def validate_version(raw: str) -> str:
    """校验版本显示串(1-VERSION_MAX 个字符);不合法抛 ValueError,返回原串。

    和名字一样受 JS 语法约束(见 _js_bad_chars)。补齐到各显示位的实际
    可用宽度在 _versions 里做,这里只做字符集与长度上限校验。
    """
    if not raw:
        raise ValueError("display.version 为空")
    bad = _js_bad_chars(raw)
    if bad:
        raise ValueError(
            f"display.version 含不支持的字符 {bad[0]!r}: 只允许可打印 ASCII,"
            "且不能是引号/反斜杠/反引号/$(会破坏二进制内嵌的 JS)")
    if len(raw) > VERSION_MAX:
        raise ValueError(f"display.version 超过 {VERSION_MAX} 字符({len(raw)}): {raw!r}")
    return raw

_CONTENT = r'((?:\\u[0-9A-Fa-f]{4}|[^"\\])*)'
_SLOT_RE = re.compile(
    r'"?(default|look-left|look-right|arms-up)"?:\{'
    r'r1L:"' + _CONTENT + r'",r1E:"' + _CONTENT + r'",r1R:"' + _CONTENT +
    r'",r2L:"' + _CONTENT + r'",r2R:"' + _CONTENT + r'"\}'
)
_ROW_RE = re.compile(
    r'"?(default|look-left|look-right|arms-up)"?:"' + _CONTENT + r'"'
)
_SLOT_NAMES = ("r1L", "r1E", "r1R", "r2L", "r2R")


# ---------- JS 源码扫描辅助 ----------
def _skip_string(data: bytes, i: int) -> int:
    """data[i] 是引号(" ' `),返回字符串结束之后的位置。"""
    q = data[i]
    i += 1
    while i < len(data):
        c = data[i]
        if c == 0x5C:  # 反斜杠: 跳过下一个字符
            i += 2
            continue
        if c == q:
            return i + 1
        i += 1
    raise ValueError("JS 字符串未闭合")


def match_brace(data: bytes, i: int) -> int:
    """data[i] == '{',返回匹配闭包之后的位置(跳过字符串内容)。"""
    if data[i] != 0x7B:
        raise ValueError("match_brace: 起点不是 {")
    depth = 0
    while i < len(data):
        c = data[i]
        if c in (0x22, 0x27, 0x60):
            i = _skip_string(data, i)
            continue
        if c == 0x7B:
            depth += 1
        elif c == 0x7D:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("花括号未闭合")


def find_all(data: bytes, sub: bytes):
    """返回 sub 在 data 中所有非重叠命中的起始偏移(与 bytes.count 计数一致)。

    非重叠是关键: patch 阶段按这些偏移做等长切片写入,重叠命中会导致重复
    写入/相互覆盖,因此发现与写入两端都必须用同一套非重叠语义。
    """
    offs, i = [], 0
    while True:
        j = data.find(sub, i)
        if j < 0:
            return offs
        offs.append(j)
        i = j + len(sub)


def _mid_pair(design: dict):
    """机身中部的 (旧内容, 新内容)。设计稿没给 mid 或新旧相同则新内容为 None。"""
    row = design.get("mid", {}).get("row")
    if not row:
        return _MID_CONTENT, None
    new_c = glyphpack.pack(glyphpack.tokenize(_MID_CONTENT), row)
    return _MID_CONTENT, (new_c if new_c != _MID_CONTENT else None)


def _art_lines(span_text: str, feet_content: str, mid_content: str = _MID_CONTENT):
    """把 logo 表文本 + 第二行中部 + 第三行内容渲染成 3 行可见图案(用于预览)。"""
    m = _SLOT_RE.search(span_text)
    if not m:
        return []
    s = m.groups()[1:]
    dec = glyphpack.decode_escapes
    return [
        dec(s[0] + s[1] + s[2]),
        dec(s[3]) + dec(mid_content) + dec(s[4]),
        "  " + dec(feet_content) + "  ",
    ]


# ---------- 各显示位的发现与替换生成 ----------
def _icon_table(data, design, patches, report, problems):
    p = data.find(_LOGO_ANCHOR)
    if p < 0:
        problems.append(
            'logo 像素表锚点未找到(r1L:" \\u2590")——版本结构可能变了,或二进制已打过补丁'
        )
        return
    start = data.rfind(b'{default:', 0, p)
    if start < 0:
        problems.append("logo 表起点 {default: 未找到")
        return
    try:
        end1 = match_brace(data, start)
        nstart = data.find(b'{', end1)
        nend = match_brace(data, nstart)
    except ValueError as e:
        problems.append(f"logo 表括号解析失败: {e}")
        return
    span = data[start:nend].decode("ascii")
    table1 = data[start:end1].decode("ascii")
    table2 = data[nstart:nend].decode("ascii")
    slot_matches = list(_SLOT_RE.finditer(table1))
    if not slot_matches:
        problems.append("logo 表结构解析失败(变体/槽位正则不匹配)")
        return

    # 位置化重建: 逐个变体按正则命中位置替换槽位,
    # 同一原型槽位内容在不同变体里可以映射到不同图案
    new_parts, last = [], 0
    for m in slot_matches:
        variant = m.group(1)
        dslots = design.get("slots", {}).get(variant)
        if not dslots:
            problems.append(f"设计稿缺少变体 [{variant}] 的槽位定义")
            continue
        try:
            packed = [
                glyphpack.pack(glyphpack.tokenize(old_c), dslots[slot_name])
                if slot_name in dslots else old_c
                for slot_name, old_c in zip(_SLOT_NAMES, m.groups()[1:])
            ]
        except ValueError as e:
            problems.append(f"设计稿与槽位不匹配({variant}): {e}")
            continue
        prefix = m.group(0)[:m.group(0).index("r1L")]
        new_parts.append(table1[last:m.start()])
        new_parts.append(
            prefix
            + f'r1L:"{packed[0]}",r1E:"{packed[1]}",r1R:"{packed[2]}",'
            + f'r2L:"{packed[3]}",r2R:"{packed[4]}"' + "}"
        )
        last = m.end()
    if problems:
        return
    new_table1 = "".join(new_parts) + table1[last:]

    # 底脚表(N2p): 所有变体用同一设计,全局内容替换即可
    row_matches = list(_ROW_RE.finditer(table2))
    n2p = design.get("n2p", {}).get("row")
    new_table2 = table2
    if row_matches and n2p:
        row_repl = {}
        for m in row_matches:
            old_c = m.group(2)
            try:
                row_repl[old_c] = glyphpack.pack(glyphpack.tokenize(old_c), n2p)
            except ValueError as e:
                problems.append(f"设计稿 n2p.row 不匹配({m.group(1)}): {e}")
        if problems:
            return
        for old_c, new_c in row_repl.items():
            new_table2 = new_table2.replace('"' + old_c + '"', '"' + new_c + '"')

    span = data[start:nend].decode("ascii")
    new_span = new_table1 + span[end1 - start:nstart - start] + new_table2
    if len(new_span) != len(span):
        problems.append("logo 表替换后长度变化(内部错误)")
        return

    # 第三行小脚的新内容(用于新图标预览;缺设计就沿用旧的)
    new_feet = _FEET_CONTENT
    feet_row = design.get("feet", {}).get("row")
    if feet_row:
        try:
            new_feet = glyphpack.pack(glyphpack.tokenize(_FEET_CONTENT), feet_row)
        except ValueError:
            pass
    old_mid, new_mid = _mid_pair(design)

    design_name = design.get("meta", {}).get("name", "新图标")
    span_bytes = span.encode("ascii")
    patches.append({
        "name": "icon_table",
        "cat": "icon",
        "desc": f"logo 像素表({len(slot_matches)} 变体 + {len(row_matches)} 底脚行)→ {design_name}",
        # 不存原文(里面是对方源码),存偏移+sha1,patch 时现场读取校验
        "offset": start,
        "sha1": hashlib.sha1(span_bytes).hexdigest(),
        "new": new_span.encode("ascii"),
        "expected": 1,
        "art_old": _art_lines(span, _FEET_CONTENT, old_mid),
        "art_new": _art_lines(new_span, new_feet, new_mid or old_mid),
    })
    report.append(f"icon_table @ {start:,}: logo 表 {len(slot_matches)} 变体 + "
                  f"{len(row_matches)} 底脚行 → {design_name}")


def _icon_mid(data, design, patches, report, problems):
    """logo 第二行机身中部常量(█████ → 设计稿,如章鱼眼睛 █◉█◉█)。

    5×█ 在文件里出现十几次,必须带上下文锚点。"""
    try:
        old_c, new_c = _mid_pair(design)
    except ValueError as e:
        problems.append(f"设计稿 mid.row 不匹配: {e}")
        return
    if new_c is None:
        return
    old_b = (_MID_CTX + '"' + old_c + '"').encode("ascii")
    new_b = (_MID_CTX + '"' + new_c + '"').encode("ascii")
    offs = find_all(data, old_b)
    if not offs:
        problems.append("logo 机身中部常量未找到(带上下文锚点,可能结构变化)")
        return
    patches.append({
        "name": "icon_mid",
        "cat": "icon",
        "desc": f"机身中部 {glyphpack.decode_escapes(old_c)} → {design['mid']['row']}",
        "old": old_b, "new": new_b,
        "expected": len(offs), "offset": offs[0],
    })
    report.append(f"icon_mid @ {offs[0]:,}: 机身中部 → {design['mid']['row']} × {len(offs)}")


def _icon_feet(data, design, patches, report, problems):
    row = design.get("feet", {}).get("row")
    if not row:
        return
    try:
        new_content = glyphpack.pack(glyphpack.tokenize(_FEET_CONTENT), row)
    except ValueError as e:
        problems.append(f"设计稿 feet.row 不匹配: {e}")
        return
    old_b = ('"' + _FEET_CONTENT + '"').encode("ascii")
    new_b = ('"' + new_content + '"').encode("ascii")
    offs = find_all(data, old_b)
    if not offs:
        problems.append("logo 第三行小脚未找到(可能已打过补丁或结构变化)")
        return
    patches.append({
        "name": "icon_feet",
        "cat": "icon",
        "desc": f"第三行小脚 {glyphpack.decode_escapes(_FEET_CONTENT)} → {row}",
        "old": old_b, "new": new_b,
        "expected": len(offs), "offset": offs[0],
    })
    report.append(f"icon_feet @ {offs[0]:,}: 第三行小脚 × {len(offs)}")


def _icon_html(data, design, patches, report, problems):
    html = design.get("html", {})
    if not html:
        return
    # 第 1+2 行在同一个反引号模板里,中间是真实换行
    old12 = ('`' + _HTML_R1_CONTENT + "\n" + _HTML_R2_CONTENT).encode("ascii")
    offs = find_all(data, old12)
    if not offs:
        report.append("icon_html: HTML 页 logo 未找到(非启动画面,可忽略)")
        return
    try:
        new12 = (
            '`' + glyphpack.pack(glyphpack.tokenize(_HTML_R1_CONTENT), html["row1"])
            + "\n"
            + glyphpack.pack(glyphpack.tokenize(_HTML_R2_CONTENT), html["row2"])
        ).encode("ascii")
        new3 = (
            glyphpack.pack(glyphpack.tokenize(_HTML_R3_CONTENT), html["row3"])
            + _HTML_R3_SUFFIX
        ).encode("ascii")
    except ValueError as e:
        problems.append(f"设计稿 html 行不匹配: {e}")
        return
    old3 = (_HTML_R3_CONTENT + _HTML_R3_SUFFIX).encode("ascii")
    offs3 = find_all(data, old3)
    if not offs3:
        # 只改前两行会得到半新半旧的 logo,拒绝半截替换
        problems.append("icon_html: 第 1+2 行找到了但第 3 行锚点未找到"
                        "(模板结构可能变了)")
        return
    patches.append({
        "name": "icon_html_r12", "cat": "icon",
        "desc": "HTML 页 logo 第 1+2 行",
        "old": old12, "new": new12, "expected": len(offs), "offset": offs[0],
    })
    patches.append({
        "name": "icon_html_r3", "cat": "icon",
        "desc": "HTML 页 logo 第 3 行",
        "old": old3, "new": new3,
        "expected": len(offs3), "offset": offs3[0],
    })
    report.append(f"icon_html @ {offs[0]:,}: HTML 页 logo 模板 × {len(offs)}")


def _names(data, display_name, patches, report, problems):
    offsets = {}
    for entry, old_s, make_new, where in NAME_SITES:
        old_b = old_s.encode("ascii")
        new_b = make_new(display_name).encode("ascii")
        if len(old_b) != len(new_b):
            raise ValueError(
                f"{entry}: 名字未按 NAME_MAX 补齐(先过 validate_name): "
                f"{display_name!r}")
        offs = find_all(data, old_b)
        if not offs:
            problems.append(f"名字锚点未找到: {old_s}(可能已打过补丁或结构变化)")
            continue
        patches.append({
            "name": entry, "cat": "name",
            "desc": f"{where}: {old_s} → {make_new(display_name)}",
            "old": old_b, "new": new_b,
            "expected": len(offs), "offset": offs[0],
        })
        report.append(f"{entry} @ {offs[0]:,}: {where} × {len(offs)}")
        offsets[entry] = offs
    return offsets


def _versions(data, name_offsets, display_version, patches, report, problems):
    """版本号显示位: 跟在名字锚点后面的 ["v",变量] / `v${变量}`。"""
    found = {}  # old_bytes -> (kind, [窗口内命中的偏移...])

    def grab(kind, pos):
        rx = _VER_ARR_RE if kind == "arr" else _VER_TPL_RE
        m = rx.search(data, pos, pos + _VER_WINDOW)
        if m:
            kind0, offs = found.setdefault(m.group(0), (kind, []))
            if m.start() not in offs:
                offs.append(m.start())

    for pos in name_offsets.get("name_title", []):
        grab("arr", pos)
    for pos in name_offsets.get("name_header", []):
        grab("arr", pos)
    for pos in name_offsets.get("name_border", []):
        grab("tpl", pos)

    if not found:
        problems.append("版本号显示位未找到(名字锚点附近没有 [\"v\",变量] / `v${变量}`)")
        return
    for old_b, (kind, offs) in sorted(found.items()):
        inner = len(old_b) - (4 if kind == "arr" else 2)
        v = (" " + display_version) if kind == "tpl" else display_version
        if len(v) > inner:
            problems.append(
                f"版本号 {display_version!r} 放不下显示位 {old_b.decode()}(最多 {inner} 字符)"
            )
            continue
        v = v.ljust(inner, " ")
        new_b = (b'["' + v.encode() + b'"]') if kind == "arr" else (b'`' + v.encode() + b'`')
        cnt = data.count(old_b)
        if cnt != len(offs):
            # patch 是全文件替换,窗口外的同串可能是功能代码
            problems.append(
                f"版本位 {old_b.decode()} 全文件出现 {cnt} 次,"
                f"名字锚点窗口内只审计到 {len(offs)} 次,拒绝全文替换")
            continue
        patches.append({
            "name": f"version_{old_b.decode('ascii').strip('[]`')}",
            "cat": "version",
            "desc": f"{old_b.decode()} → {new_b.decode()}",
            "old": old_b, "new": new_b,
            "expected": cnt, "offset": offs[0],
        })
        report.append(f"version @ {offs[0]:,}: {old_b.decode()} → {new_b.decode()}(×{cnt})")


# ---------- 主题色(终端 rgb 主题里的品牌橙) ----------
# ansi 主题(ansi:redBright)不映射任意 RGB,保持不变
_COLOR_SITES = [
    'claude:"rgb(215,119,87)"',      # 边框/标题主色
    'claude:"rgb(255,153,51)"',      # 另一套 rgb 主题里的同名色
    'clawd_body:"rgb(215,119,87)"',  # logo 机身
]


def pack_rgb(r: int, g: int, b: int, total: int = 15) -> str:
    """把 rgb(r,g,b) 格式化成正好 total 字符(逗号后补空格;
    解析器容忍空格——原主题表里就有 "rgb(240, 240, 240)")。"""
    for v in (r, g, b):
        if not (0 <= v <= 255):
            raise ValueError(f"颜色分量超出 0-255: {(r, g, b)}")
    base = f"rgb({r},{g},{b})"
    need = total - len(base)
    if need < 0:
        raise ValueError(f"rgb({r},{g},{b}) 超过 {total} 字符")
    p1, p2, p3 = base.split(",", 2)
    first = need // 2
    second = need - first
    return p1 + "," + " " * first + p2 + "," + " " * second + p3


def parse_hex_color(s: str):
    """'#2E9BFF' / '2E9BFF' / '' -> (r,g,b) / None。"""
    s = (s or "").strip().lstrip("#")
    if not s:
        return None
    if len(s) != 6:
        raise ValueError(f"颜色需为 6 位 hex: {s!r}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _theme_color(data, color, patches, report, problems):
    """color: (r,g,b) 或 None(不改)。"""
    if not color:
        return
    try:
        packed = pack_rgb(*color)
    except ValueError as e:
        problems.append(f"主题色格式失败: {e}")
        return
    for site in _COLOR_SITES:
        prefix, old_inner = site.split('"', 1)
        old_inner = old_inner.rstrip('"')
        old_b = site.encode("ascii")
        new_b = (prefix + '"' + packed + '"').encode("ascii")
        if len(old_b) != len(new_b):
            problems.append(f"主题色替换长度不等(内部错误): {site}")
            return
        offs = find_all(data, old_b)
        if not offs:
            report.append(f"theme_color: {site} 未找到(版本可能改了配色,可忽略)")
            continue
        patches.append({
            "name": f"color_{prefix.rstrip(':')}_{old_inner[4:].replace(',', '_')}",
            "cat": "color",
            "desc": f"{prefix} {old_inner} → {packed}",
            "old": old_b, "new": new_b,
            "expected": len(offs), "offset": offs[0],
        })
        report.append(f"theme_color @ {offs[0]:,}: {prefix} → {packed} × {len(offs)}")


def build_patches(data: bytes, display_name: str, display_version: str, design: dict,
                  theme_color=None):
    """返回 (patches, report, problems)。

    design=None      → 不动图标(保持原图标)
    display_version=None → 不动版本位(显示真实版本)
    """
    patches: list = []
    report: list = []
    problems: list = []
    if design:
        _icon_table(data, design, patches, report, problems)
        _icon_feet(data, design, patches, report, problems)
        _icon_mid(data, design, patches, report, problems)
        _icon_html(data, design, patches, report, problems)
    name_offsets = _names(data, display_name, patches, report, problems)
    if not problems:
        if display_version:
            _versions(data, name_offsets, display_version, patches, report, problems)
        _theme_color(data, theme_color, patches, report, problems)
    return patches, report, problems


def collect_contexts(data: bytes, limit_per_kind: int = 3) -> list:
    """给 LLM 辅助分析用的上下文片段。"""
    ctx = []
    for label, anchor in [
        ("r1L(logo 槽位)", b'r1L:"'),
        ("Claude Code", b"Claude Code"),
        ('["v",变量]', b'["v",'),
    ]:
        for off in find_all(data, anchor)[:limit_per_kind]:
            lo = max(0, off - 100)
            snippet = data[lo:off + 200].decode("ascii", errors="replace")
            ctx.append(f"--- {label} @ 偏移 {off} ---\n{snippet}")
    return ctx
