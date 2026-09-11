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

from . import bytecode, glyphpack

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

# 大 logo 的第三行是与小 logo 不同的另一个常量(小 logo 走 _FEET_CONTENT)
_FEET_BIG_VISIBLE = "▝▝ ▝▝"

# 常量池里的产品名条目(池按内容去重,一条对应所有引用它的显示位)
_POOL_NAME_SITES = [
    ("Claude Code", lambda n: n, "name"),
    ("Welcome to Claude Code", lambda n: "Welcome to " + n, "name_welcome"),
]

# 显示位在版本之间会被上游整个删掉(如 2.1.261 移除了输入框边框上的产品名)。
# required=False 的锚点缺失只提示,不算失败;全都找不到才是结构真的变了。
NAME_SITES = [
    ("name_title",          'children:"Claude Code"', lambda n: f'children:"{n}"', "启动/面板标题", True),
    ("name_border",         '("Claude Code")',        lambda n: f'("{n}")',        "输入框边框(展开)", False),
    ("name_border_compact", '(" Claude Code ")',      lambda n: f'(" {n} ")',      "输入框边框(紧凑)", False),
    ("name_header",         '["Claude Code"," "]',    lambda n: f'["{n}"," "]',    "头部显示", False),
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


def validate_version(raw: str, max_len: int = VERSION_MAX) -> str:
    """校验版本显示串(1-max_len 个字符);不合法抛 ValueError,返回原串。

    max_len=None 用于改写内部版本常量的场景: 那时长度由真实版本决定
    (必须等长),而不是由 ["v",X] 显示位的宽度决定。

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
    if max_len is not None and len(raw) > max_len:
        raise ValueError(f"display.version 超过 {max_len} 字符({len(raw)}): {raw!r}")
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
# 槽位按显示行分组: 设计稿按整行给,边界由二进制现场决定(见 glyphpack.pack_row)
_ICON_ROWS = (("r1L", "r1E", "r1R"), ("r2L", "r2R"))


def _layout_sig(slot_contents: dict) -> str:
    """探测到的槽位列数,如 'r1L=2 r1E=6 r1R=0 | r2L=2 r2R=2'。"""
    return " | ".join(
        " ".join(f"{s}={len(glyphpack.tokenize(slot_contents[s]))}" for s in row)
        for row in _ICON_ROWS)


def _report_layouts(layouts, report):
    """把探测到的槽位布局写进报告(相同布局的变体合并成一行)。"""
    grouped = {}
    for variant, sig in layouts:
        grouped.setdefault(sig, []).append(variant)
    for sig, variants in grouped.items():
        report.append(f"槽位布局 {'/'.join(variants)}: {sig}")


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
    new_parts, last, layouts = [], 0, []
    for m in slot_matches:
        variant = m.group(1)
        dslots = design.get("slots", {}).get(variant)
        if not dslots:
            problems.append(f"设计稿缺少变体 [{variant}] 的槽位定义")
            continue
        old_contents = dict(zip(_SLOT_NAMES, m.groups()[1:]))
        layouts.append((variant, _layout_sig(old_contents)))
        packed = dict(old_contents)
        try:
            for row_slots in _ICON_ROWS:
                if not all(s in dslots for s in row_slots):
                    continue
                # 设计稿按整行给,现场按二进制的槽位边界切回去
                packed.update(zip(row_slots, glyphpack.pack_row(
                    [(s, old_contents[s]) for s in row_slots],
                    "".join(dslots[s] for s in row_slots))))
        except ValueError as e:
            problems.append(f"设计稿与槽位不匹配({variant}): {e}")
            continue
        prefix = m.group(0)[:m.group(0).index("r1L")]
        new_parts.append(table1[last:m.start()])
        new_parts.append(
            prefix
            + ",".join(f'{s}:"{packed[s]}"' for s in _SLOT_NAMES) + "}"
        )
        last = m.end()
    if problems:
        return
    _report_layouts(layouts, report)
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


def _names(data, display_name, patches, report, problems, warnings):
    offsets = {}
    for entry, old_s, make_new, where, required in NAME_SITES:
        old_b = old_s.encode("ascii")
        new_b = make_new(display_name).encode("ascii")
        if len(old_b) != len(new_b):
            raise ValueError(
                f"{entry}: 名字未按 NAME_MAX 补齐(先过 validate_name): "
                f"{display_name!r}")
        offs = find_all(data, old_b)
        if not offs:
            msg = f"名字锚点未找到: {old_s}(可能已打过补丁或结构变化)"
            (problems if required else warnings).append(msg)
            continue
        patches.append({
            "name": entry, "cat": "name",
            "desc": f"{where}: {old_s} → {make_new(display_name)}",
            "old": old_b, "new": new_b,
            "expected": len(offs), "offset": offs[0],
        })
        report.append(f"{entry} @ {offs[0]:,}: {where} × {len(offs)}")
        offsets[entry] = offs
    if not offsets:
        problems.append("所有名字锚点都未找到 —— 该版本结构变了,不要猜")
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

    # 显示位容量取决于该版本压缩后的变量名长度(["v",ms] 比 ["v",c3e] 窄一格),
    # 因此按二进制现场探测而非用固定上限。容量必须先按最窄位统一校验:
    # 逐个跳过放不下的位会让一部分显示位是假版本、另一部分是真版本。
    caps = {old_b: len(old_b) - (4 if kind == "arr" else 2)
            for old_b, (kind, _) in found.items()}
    tightest = min(caps, key=caps.get)
    # 模板位 `v${x}` 渲染时前面还有一个空格,占一格
    budget = min(caps[b] - (1 if found[b][0] == "tpl" else 0) for b in caps)
    report.append(
        f"版本位容量(自动探测): 最多 {budget} 字符(最窄位 {tightest.decode()})")
    if len(display_version) > budget:
        problems.append(
            f"版本号 {display_version!r}({len(display_version)} 字符)放不下: "
            f"本版本最窄显示位 {tightest.decode()} 只有 {budget} 字符 —— "
            f"改短 display.version,或用 real_version 保持真实版本")
        return

    for old_b, (kind, offs) in sorted(found.items()):
        inner = caps[old_b]
        v = (" " + display_version) if kind == "tpl" else display_version
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


def _pool_record(mapping, conflicts, old_vis, new_vis, where):
    """登记「池里这条内容 → 换成什么」。池按内容去重,一条只能有一个目标。"""
    if not old_vis or old_vis == new_vis:
        return
    prev = mapping.get(old_vis)
    if prev is None:
        mapping[old_vis] = (new_vis, [where])
        return
    if prev[0] != new_vis:
        conflicts.setdefault(old_vis, {prev[0]: "/".join(prev[1])})[new_vis] = where
        return
    prev[1].append(where)   # 多个槽位共用同一条,记全便于人工核对


def _pool_icon_map(data, design, problems, warnings):
    """从源码 logo 表学到「当前可见内容 → 设计稿新内容」,供常量池替换用。

    源码文本虽然不驱动 UI,但它保留了「哪个变体的哪个槽位是什么内容」这层
    语义;常量池只有一堆去重后的字符串,没有这层信息。所以用源码表做地图,
    把补丁落到池上。
    """
    p = data.find(_LOGO_ANCHOR)
    if p < 0:
        problems.append('logo 像素表锚点未找到(r1L:" ...")——版本结构可能变了')
        return None, [], {}
    start = data.rfind(b'{default:', 0, p)
    if start < 0:
        problems.append("logo 表起点 {default: 未找到")
        return None, [], {}
    try:
        end1 = match_brace(data, start)
        nstart = data.find(b'{', end1)
        nend = match_brace(data, nstart)
    except ValueError as e:
        problems.append(f"logo 表括号解析失败: {e}")
        return None, [], {}
    table1 = data[start:end1].decode("ascii")
    table2 = data[nstart:nend].decode("ascii")

    mapping, conflicts, layouts, default_vis = {}, {}, [], {}
    for m in _SLOT_RE.finditer(table1):
        variant = m.group(1)
        dslots = design.get("slots", {}).get(variant)
        if not dslots:
            problems.append(f"设计稿缺少变体 [{variant}] 的槽位定义")
            continue
        old_vis = {s: glyphpack.decode_escapes(c)
                   for s, c in zip(_SLOT_NAMES, m.groups()[1:])}
        if variant == "default":
            default_vis = dict(old_vis)
        layouts.append((variant, _layout_sig(dict(zip(_SLOT_NAMES, m.groups()[1:])))))
        for row_slots in _ICON_ROWS:
            if not all(s in dslots for s in row_slots):
                continue
            try:
                parts = glyphpack.split_row(
                    [len(old_vis[s]) for s in row_slots],
                    "".join(dslots[s] for s in row_slots), row_slots)
            except ValueError as e:
                problems.append(f"设计稿与槽位不匹配({variant}): {e}")
                continue
            for s, new in zip(row_slots, parts):
                _pool_record(mapping, conflicts, old_vis[s], new, f"{variant}.{s}")

    n2p = design.get("n2p", {}).get("row")
    if n2p:
        for m in _ROW_RE.finditer(table2):
            _pool_record(mapping, conflicts, glyphpack.decode_escapes(m.group(2)),
                         n2p, f"n2p.{m.group(1)}")

    mid = design.get("mid", {}).get("row")
    if mid:
        _pool_record(mapping, conflicts, glyphpack.decode_escapes(_MID_CONTENT),
                     mid, "mid.row")
    feet = design.get("feet", {}).get("row")
    if feet:
        # 小 logo 与大 logo 的第三行是两个不同常量,设计稿的 feet 都适用
        _pool_record(mapping, conflicts, glyphpack.decode_escapes(_FEET_CONTENT),
                     feet, "feet.row(小 logo)")
        _pool_record(mapping, conflicts, _FEET_BIG_VISIBLE, feet, "feet.row(大 logo)")

    for old_vis, targets in conflicts.items():
        problems.append(
            f"常量池按内容去重,{old_vis!r} 只有一条,但设计稿要求它同时变成 "
            + " / ".join(f"{n!r}({w})" for n, w in targets.items())
            + " —— 让这些槽位用同一个图案,或接受其中之一")
    return (None if conflicts else mapping), layouts, default_vis


def pool_art(design):
    """设计稿渲染成三行(default 变体),给预览用。"""
    slots = design.get("slots", {}).get("default", {})
    if not slots:
        return None
    row1 = "".join(slots.get(s, "") for s in ("r1L", "r1E", "r1R"))
    row2 = (slots.get("r2L", "") + design.get("mid", {}).get("row", "")
            + slots.get("r2R", ""))
    return [row1, row2, design.get("feet", {}).get("row", "")]


def _pool_html(data, design, warnings):
    """HTML 登录/错误页的三行 logo 在池里是一条含换行的整串,不在 logo 条目区。"""
    html = design.get("html", {})
    if not (html.get("row1") and html.get("row2") and html.get("row3")):
        return {}
    rows_new = "\n".join((html["row1"], html["row2"], html["row3"]))
    # 老串由当前 logo 拼出来,不硬编码某个版本的图案
    m = _SLOT_RE.search(data[data.rfind(b'{default:', 0, data.find(_LOGO_ANCHOR)):]
                        .decode("ascii", "replace"))
    if not m:
        return {}
    r1L, r1E, r1R, r2L, r2R = (glyphpack.decode_escapes(c) for c in m.groups()[1:])
    mid = glyphpack.decode_escapes(_MID_CONTENT)
    row1, row2 = r1L + r1E + r1R, r2L + mid + r2R
    for feet in (_FEET_BIG_VISIBLE, glyphpack.decode_escapes(_FEET_CONTENT)):
        old = "\n".join((row1, row2, "  " + feet))
        if bytecode.unique(data, old):
            if len(old) != len(rows_new):
                warnings.append(
                    f"HTML 页 logo 共 {len(old)} 字符,设计稿 html 行合计 "
                    f"{len(rows_new)} 字符,跳过")
                return {}
            return {old: (rows_new, "html")}
    warnings.append("HTML 页 logo 未在常量池找到(非启动画面,可忽略)")
    return {}


def _pool_emit(data, mapping, region, cat, patches, report, problems, prefix=""):
    """把「内容 → 新内容」映射落成 offset+sha1 的池条目补丁。"""
    done = 0
    for old_vis, (new_vis, where) in sorted(mapping.items()):
        sites = where if isinstance(where, list) else [where]
        label = "/".join(sites)
        hits = bytecode.find(data, old_vis, region)
        if not hits:
            problems.append(f"常量池里找不到 {old_vis!r}({label})")
            continue
        if len(hits) > 1:
            problems.append(
                f"{old_vis!r}({label}) 在常量池命中 {len(hits)} 条,无法确定改哪条")
            continue
        entry = hits[0]
        try:
            new_b = bytecode.replacement(entry, new_vis)
        except ValueError as e:
            problems.append(f"{label}: {e}")
            continue
        old_b = data[entry.chars:entry.chars + entry.nbytes]
        if old_b == new_b:
            continue
        patches.append({
            "name": f"{prefix}{sites[0].replace('.', '_').replace(' ', '_')}",
            "cat": cat,
            "desc": f"{label}: {old_vis!r} → {new_vis!r}",
            "offset": entry.chars,
            "sha1": hashlib.sha1(old_b).hexdigest(),
            "enc": "latin-1" if entry.eight else "utf-16-le",
            "new": new_b,
            "expected": 1,
        })
        done += 1
    if done:
        report.append(f"常量池 [{cat}]: {done} 条条目")
    return done


def _pool_channel(data, display_name, display_version, design, theme_color,
                  real_version, rewrite_real_version,
                  patches, report, problems, warnings):
    """bytecode 构建: 补丁全部打到常量池。"""
    region = None
    if design:
        mapping, layouts, _default_vis = _pool_icon_map(
            data, design, problems, warnings)
        _report_layouts(layouts, report)
        if mapping is not None:
            region = bytecode.region_for(
                data, [s for s in mapping if len(s) >= 5]) or None
            _pool_emit(data, mapping, region, "icon", patches, report, problems,
                       prefix="icon_")
            _pool_emit(data, _pool_html(data, design, warnings), None, "icon",
                       patches, report, problems, prefix="icon_")
            _pool_leftover_art(data, mapping, region, report, warnings)

    name_map = {}
    for old_vis, make_new, where in _POOL_NAME_SITES:
        if bytecode.unique(data, old_vis):
            name_map[old_vis] = (make_new(display_name), where)
        else:
            warnings.append(f"常量池里没有 {old_vis!r},跳过({where})")
    if not name_map:
        problems.append("常量池里一个产品名条目都没有 —— 该版本结构变了,不要猜")
    _pool_emit(data, name_map, None, "name", patches, report, problems)

    _pool_version(data, display_version, real_version, rewrite_real_version,
                  patches, report, problems, warnings)

    if theme_color:
        try:
            packed = pack_rgb(*theme_color)
        except ValueError as e:
            problems.append(f"主题色格式失败: {e}")
        else:
            color_map = {}
            for site in _COLOR_SITES:
                old_rgb = site.split('"', 1)[1].rstrip('"')
                if bytecode.unique(data, old_rgb):
                    color_map[old_rgb] = (packed, site.split(":", 1)[0])
            if not color_map:
                report.append("theme_color: 常量池里没有已知配色条目,跳过")
            _pool_emit(data, color_map, None, "color", patches, report, problems,
                       prefix="color_")


def _pool_version(data, display_version, real_version, rewrite,
                  patches, report, problems, warnings):
    """bytecode 构建上启动画面的版本号与 --version 共用同一条常量。"""
    if not display_version:
        return
    if not real_version:
        warnings.append("未探测到真实版本,跳过版本显示位")
        return
    entry = bytecode.unique(data, real_version)
    if entry is None:
        warnings.append(f"常量池里找不到版本常量 {real_version!r},跳过版本显示位")
        return
    if not rewrite:
        warnings.append(
            f"启动画面的版本号与 --version 共用同一条常量({real_version!r} "
            f"@ {entry.chars}),改它会连内部版本一起改 —— 已跳过。"
            "确要改: 配置 [display] rewrite_real_version = true")
        return
    if len(display_version) != len(real_version):
        problems.append(
            f"改内部版本常量需等长: 真实版本 {real_version!r}({len(real_version)} 字符),"
            f"display.version {display_version!r}({len(display_version)} 字符)")
        return
    _pool_emit(data, {real_version: (display_version, "version")}, None,
               "version", patches, report, problems)
    warnings.append("已改写内部版本常量: --version 与更新检查都会看到假版本")


def _pool_leftover_art(data, mapping, region, report, warnings):
    """报出区间内没被设计稿覆盖到的图案条目,让上游新增的图案不至于默默漏掉。"""
    if not region:
        return
    leftover = [e.text for e in bytecode.art_entries_in(data, *region)
                if e.text not in mapping and len(e.text) >= 4]
    if leftover:
        warnings.append("常量池里还有未被设计稿覆盖的图案条目: "
                        + " ".join(repr(t) for t in leftover[:6]))


def build_patches(data: bytes, display_name: str, display_version: str, design: dict,
                  theme_color=None, real_version=None, rewrite_real_version=False):
    """返回 (patches, report, problems, warnings)。

    problems 是致命问题(草稿不可直接用),warnings 只是提示。

    bytecode 构建走常量池通道: 运行时读的是 bytecode 常量池,二进制里那份
    JS 源码文本已经不驱动 UI —— 改它能通过一切计数校验,屏幕上却毫无变化。
    老版本(非 bytecode)仍走源码文本通道。

    design=None      → 不动图标(保持原图标)
    display_version=None → 不动版本位(显示真实版本)
    """
    patches: list = []
    report: list = []
    problems: list = []
    warnings: list = []
    if bytecode.is_bytecode_build(data):
        report.append("bytecode 构建: 补丁打到常量池(源码文本不驱动 UI)")
        _pool_channel(data, display_name, display_version, design, theme_color,
                      real_version, rewrite_real_version,
                      patches, report, problems, warnings)
        return patches, report, problems, warnings

    if design:
        _icon_table(data, design, patches, report, problems)
        _icon_feet(data, design, patches, report, problems)
        _icon_mid(data, design, patches, report, problems)
        _icon_html(data, design, patches, report, problems)
    name_offsets = _names(data, display_name, patches, report, problems, warnings)
    if display_version and name_offsets:
        _versions(data, name_offsets, display_version, patches, report, problems)
    _theme_color(data, theme_color, patches, report, problems)
    return patches, report, problems, warnings


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
