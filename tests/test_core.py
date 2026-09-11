"""核心单元测试: 全部跑在**自造**的合成 bundle 上。

合成 bundle 只复刻结构(槽位形状/锚点模式),槽位内容是我们自己编的,
不包含任何 Claude Code 的代码。运行: python3 -m unittest discover -s tests -v
"""
import hashlib
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from patcher import defio, discovery, glyphpack, locate, patch as patch_mod, tomlmini


def esc(s: str) -> str:
    """可见字符 → 二进制里的源码形式(空格是字面位,其余走 \\uXXXX 转义)。"""
    return "".join(ch if ch == " " else chr(92) +  "u%04X" % ord(ch) for ch in s)


# ---------- 合成 bundle(结构仿真,内容自编) ----------
# 同一张图的两种槽位切分。上游在 2.1.261 把 r1E 从 5 列加到 6 列、r1R 从 1 列减到 0,
# 整行列数不变 —— 设计稿不该因此改动。
ROW1 = {v: " ▐░▒▓█░▌" for v in ("default", "look-left", "look-right")}
ROW1["arms-up"] = "▗▟░▒▓█░▙▖"
ROW2 = {v: "▝▜▛▘" for v in ("default", "look-left", "look-right")}
ROW2["arms-up"] = " ▜▛ "
N2P = {"default": " ▗   ▖ ", "look-left": " ▘   ▘ ",
       "look-right": " ▝   ▝ ", "arms-up": " ▗   ▖ "}
NARROW = {v: (2, 5, 1) for v in ROW1} | {"arms-up": (2, 5, 2)}
WIDE = {v: (2, 6, 0) for v in ROW1} | {"arms-up": (2, 6, 1)}


def make_logo_table(widths) -> str:
    """按给定的槽位列数合成 logo 表(同一张图,不同的槽位边界)。"""
    variants = []
    for v, (a, b, c) in widths.items():
        r1, r2 = ROW1[v], ROW2[v]
        assert len(r1) == a + b + c, v
        key = v if v == "default" else '"%s"' % v
        variants.append(
            '%s:{r1L:"%s",r1E:"%s",r1R:"%s",r2L:"%s",r2R:"%s"}'
            % (key, esc(r1[:a]), esc(r1[a:a + b]), esc(r1[a + b:]),
               esc(r2[:2]), esc(r2[2:])))
    feet = ",".join('%s:"%s"' % (v if v == "default" else '"%s"' % v, esc(row))
                    for v, row in N2P.items())
    return "{" + ",".join(variants) + "}," + "N2p={" + feet + "}"


LOGO_TABLE = make_logo_table(NARROW)
LOGO_TABLE_WIDE = make_logo_table(WIDE)
FEET = '"\\u2598\\u2598 \\u259D\\u259D"'
MID = 'clawd_background",children:"\\u2588\\u2588\\u2588\\u2588\\u2588"'
HTML_LOGO = '` \\u2590\\u259B\\u2588\\u2588\\u2588\\u259C\\u258C\n\\u259D\\u259C\\u2588\\u2588\\u2588\\u2588\\u2588\\u259B\\u2598\n  \\u2598\\u2598 \\u259D\\u259D`;'
NAMES = (
    'children:"Claude Code";x=["v",Ttt];'
    '("Claude Code")+`v${Ttt}`;'
    '(" Claude Code ");'
    '["Claude Code"," "]+["v",Ttt];'
)
COLORS = 'claude:"rgb(215,119,87)";clawd_body:"rgb(215,119,87)";'


def make_bundle(logo_table: str = None, names: str = None) -> bytes:
    parts = [
        os.urandom(500),
        (logo_table or LOGO_TABLE).encode(),
        os.urandom(300),
        FEET.encode(), os.urandom(100), FEET.encode(),
        os.urandom(200), MID.encode(),
        os.urandom(200), HTML_LOGO.encode(),
        os.urandom(300), (names or NAMES).encode(),
        os.urandom(300), COLORS.encode(),
        os.urandom(500),
    ]
    return b"".join(parts)


DESIGN = {
    "meta": {"name": "测试图标"},
    "slots": {
        v: {"r1L": " ▗", "r1E": "▄▄▄▄▖", "r1R": " ", "r2L": "▟█", "r2R": "█▙"}
        for v in ("default", "look-left", "look-right")
    } | {"arms-up": {"r1L": "▗ ", "r1E": "▗▄▄▄▖", "r1R": " ▖", "r2L": " ▟", "r2R": "▙ "}},
    "mid": {"row": "█◉█◉█"},
    "feet": {"row": "╵╵ ╵╵"},
    "n2p": {"row": " ▄   ▄ "},
    "html": {"row1": " ▗▄▄▄▄▖ ", "row2": "▟██◉█◉██▙", "row3": "  ╵╵ ╵╵"},
}


class TestGlyphPack(unittest.TestCase):
    def test_tokenize(self):
        tokens = glyphpack.tokenize(r" \u2597")
        self.assertEqual(tokens, [("lit", " "), ("esc", "\u2597")])
        self.assertEqual(glyphpack.tokenize(r"\u259B "), [("esc", "▛"), ("lit", " ")])

    def test_pack_equal_length(self):
        tokens = glyphpack.tokenize(r" \u2590")
        self.assertEqual(glyphpack.pack(tokens, " ▗"), r" \u2597")
        self.assertEqual(glyphpack.pack(tokens, "  "), r" \u0020")

    def test_pack_rejects_bad_input(self):
        tokens = glyphpack.tokenize(r" \u2590")
        with self.assertRaises(ValueError):
            glyphpack.pack(tokens, "▗▗▗")  # 字符数不符
        with self.assertRaises(ValueError):
            glyphpack.pack(tokens, "▗ ")   # 字面位(lit)不能放非 ASCII
        # 字面位不能放会破坏宿主 JS 语法的字符
        for bad in ("`▗", "$▗", "\x1b▗", "\n▗", '"▗', "\\▗"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                glyphpack.pack(tokens, bad)

    def test_decode_escapes(self):
        self.assertEqual(glyphpack.decode_escapes(r"\u2597\u2596"), "▗▖")


    def test_pack_row_splits_by_binary_slots(self):
        slots = [("r1L", esc(" ▐")), ("r1E", esc("░▒")), ("r1R", "")]
        self.assertEqual(
            glyphpack.pack_row(slots, " ▗▄▖"),
            [esc(" ▗"), esc("▄▖"), ""])

    def test_pack_row_regroups_same_glyphs(self):
        """同一行字符,槽位边界挪动后,打包拼起来必须完全一致。"""
        narrow = [("r1L", esc(" ▐")), ("r1E", esc("░▒")), ("r1R", esc("▓"))]
        wide = [("r1L", esc(" ▐")), ("r1E", esc("░▒▓")), ("r1R", "")]
        row = " ▗▄▖▄"
        self.assertEqual(
            "".join(glyphpack.pack_row(narrow, row)),
            "".join(glyphpack.pack_row(wide, row)))

    def test_pack_row_reports_both_widths(self):
        slots = [("r1L", esc(" ▐")), ("r1E", esc("░▒"))]
        with self.assertRaises(ValueError) as cm:
            glyphpack.pack_row(slots, "▗▄")
        msg = str(cm.exception)
        self.assertIn("共 4 列", msg)
        self.assertIn("给了 2 列", msg)
        self.assertIn("r1E=2", msg)


class TestColor(unittest.TestCase):
    def test_pack_rgb_exact_length(self):
        self.assertEqual(discovery.pack_rgb(215, 119, 87), "rgb(215,119,87)")
        s = discovery.pack_rgb(0, 185, 107)
        self.assertEqual(len(s), 15)
        self.assertTrue(s.startswith("rgb(0,"))
        self.assertEqual(len(discovery.pack_rgb(9, 9, 9)), 15)

    def test_pack_rgb_range_check(self):
        with self.assertRaises(ValueError):
            discovery.pack_rgb(300, 0, 0)

    def test_parse_hex(self):
        self.assertEqual(discovery.parse_hex_color("#2E9BFF"), (46, 155, 255))
        self.assertEqual(discovery.parse_hex_color("2e9bff"), (46, 155, 255))
        self.assertIsNone(discovery.parse_hex_color(""))
        with self.assertRaises(ValueError):
            discovery.parse_hex_color("FFF")


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.data = make_bundle()
        self.patches, self.report, self.problems, self.warnings = discovery.build_patches(
            self.data, " Loop Code ", "v9.99", DESIGN, theme_color=(46, 155, 255))

    def test_no_problems(self):
        self.assertEqual(self.problems, [])

    def test_categories(self):
        cats = {p["cat"] for p in self.patches}
        self.assertEqual(cats, {"icon", "name", "version", "color"})

    def test_equal_length(self):
        for p in self.patches:
            if p.get("old") is not None:
                self.assertEqual(len(p["old"]), len(p["new"]), p["name"])

    def test_icon_table_uses_sha1_mode(self):
        t = next(p for p in self.patches if p["name"] == "icon_table")
        self.assertIsNone(t.get("old"))
        self.assertTrue(t["sha1"])
        self.assertEqual(t["offset"], 500)  # urandom(500) 之后就是表

    def test_counts(self):
        by_name = {p["name"]: p["expected"] for p in self.patches}
        self.assertEqual(by_name["icon_feet"], 2)
        self.assertEqual(by_name["name_title"], 1)

    def test_skip_icon_and_version(self):
        patches, _, problems, _w = discovery.build_patches(
            self.data, " Loop Code ", None, None)
        self.assertEqual(problems, [])
        cats = {p["cat"] for p in patches}
        self.assertNotIn("icon", cats)
        self.assertNotIn("version", cats)


class TestSlotLayoutDrift(unittest.TestCase):
    """上游挪动槽位边界后,同一份设计稿仍要打出同一张图。"""

    def _icon(self, table):
        data = make_bundle(logo_table=table)
        patches, report, problems, _w = discovery.build_patches(
            data, " Loop Code ", "v9.9", DESIGN)
        self.assertEqual(problems, [])
        return data, next(p for p in patches if p["name"] == "icon_table"), report

    def test_same_art_across_layouts(self):
        _, narrow, _ = self._icon(LOGO_TABLE)
        _, wide, _ = self._icon(LOGO_TABLE_WIDE)
        self.assertEqual(narrow["art_new"], wide["art_new"])

    def test_span_length_and_sha1_hold(self):
        for label, table in (("narrow", LOGO_TABLE), ("wide", LOGO_TABLE_WIDE)):
            data, t, _ = self._icon(table)
            span = data[t["offset"]:t["offset"] + len(t["new"])]
            self.assertEqual(len(span), len(t["new"]), label)
            self.assertEqual(hashlib.sha1(span).hexdigest(), t["sha1"], label)

    def test_detected_layout_is_reported(self):
        _, _, report = self._icon(LOGO_TABLE_WIDE)
        self.assertTrue(any("r1E=6 r1R=0" in line for line in report), report)
        _, _, report = self._icon(LOGO_TABLE)
        self.assertTrue(any("r1E=5 r1R=1" in line for line in report), report)

    def test_row_width_mismatch_names_both_sides(self):
        bad = dict(DESIGN)
        bad["slots"] = dict(DESIGN["slots"])
        bad["slots"]["default"] = dict(DESIGN["slots"]["default"], r1E="▄▄▄")
        _, _, problems, _w = discovery.build_patches(
            make_bundle(), " Loop Code ", "v9.9", bad)
        self.assertTrue(any("整行列数不符" in p for p in problems), problems)


class TestOptionalNameAnchors(unittest.TestCase):
    """上游删掉某个显示位不该连坐掉其余补丁(2.1.261 移除了边框上的产品名)。"""

    TITLE_ONLY = 'children:"Claude Code";x=["v",Ttt];'

    def setUp(self):
        self.data = make_bundle(names=self.TITLE_ONLY)
        self.patches, _, self.problems, self.warnings = discovery.build_patches(
            self.data, " Loop Code ", "v9.9", DESIGN, theme_color=(46, 155, 255))

    def test_missing_optional_sites_are_warnings_not_failures(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(len(self.warnings), 3)

    def test_version_and_color_still_generated(self):
        cats = {p["cat"] for p in self.patches}
        self.assertIn("version", cats)
        self.assertIn("color", cats)

    def test_all_names_missing_is_fatal(self):
        _, _, problems, _w = discovery.build_patches(
            make_bundle(names="nothing to see here;"), " Loop Code ", "v9.9", DESIGN)
        self.assertTrue(any("所有名字锚点" in p for p in problems), problems)

    def test_version_capacity_comes_from_binary(self):
        # 压缩后的变量名短一格,显示位就窄一格 —— 固定上限会漏判
        _, report, problems, _w = discovery.build_patches(
            make_bundle(names='children:"Claude Code";x=["v",m];'),
            " Loop Code ", "v9.9", DESIGN)
        self.assertTrue(any("最多 3 字符" in line for line in report), report)
        self.assertTrue(any("放不下" in p for p in problems), problems)

    def test_capacity_failure_is_atomic(self):
        """一个位放不下就整批不改,避免一半假版本一半真版本。"""
        _, _, problems, _w = discovery.build_patches(
            make_bundle(names='children:"Claude Code";x=["v",m]+["v",Ttttt];'),
            " Loop Code ", "v9.9", DESIGN)
        self.assertTrue(any("放不下" in p for p in problems), problems)


class TestDefio(unittest.TestCase):
    def test_roundtrip(self):
        data = make_bundle()
        patches, _, problems, _w = discovery.build_patches(
            data, " Loop Code ", "v9.99", DESIGN)
        self.assertEqual(problems, [])
        text = defio.dumps({"target_version": "t", "verified": True}, patches)
        meta, loaded = defio.loads(text)
        self.assertEqual(len(loaded), len(patches))
        for a, b in zip(patches, loaded):
            self.assertEqual(a.get("old"), b.get("old"))
            self.assertEqual(a["new"], b["new"])
            self.assertEqual(a["expected"], b["expected"])
            if a.get("old") is None:
                self.assertEqual(a["sha1"], b["sha1"])
                self.assertEqual(a["offset"], b["offset"])


class TestPatchE2E(unittest.TestCase):
    def _apply(self, tmpdir):
        target = pathlib.Path(tmpdir) / "fake-claude"
        target.write_bytes(make_bundle())
        data = target.read_bytes()
        patches, _, problems, _w = discovery.build_patches(
            data, " Loop Code ", "v9.99", DESIGN, theme_color=(46, 155, 255))
        assert not problems
        def_path = pathlib.Path(tmpdir) / "def.toml"
        def_path.write_text(defio.dumps(
            {"target_version": "t", "verified": True}, patches))
        return target, def_path

    def test_dry_run_then_apply(self):
        with tempfile.TemporaryDirectory() as td:
            target, def_path = self._apply(td)
            self.assertEqual(patch_mod.run(str(target), str(def_path), apply=False), 0)
            rc = patch_mod.run(str(target), str(def_path), apply=True, yes=True)
            self.assertEqual(rc, 0)
            out = target.read_bytes()
            self.assertIn(" Loop Code ".encode(), out)
            self.assertIn(b'["v9.99"]', out)
            self.assertIn(b"rgb(46,155,255)", out)
            self.assertNotIn(b"Claude Code", out)
            # 已打过补丁再跑应失败(计数为 0)
            rc2 = patch_mod.run(str(target), str(def_path), apply=False)
            self.assertEqual(rc2, 1)

    def test_wrong_version_aborts(self):
        with tempfile.TemporaryDirectory() as td:
            target, def_path = self._apply(td)
            target.write_bytes(os.urandom(2048))  # 全随机,锚点全丢
            self.assertEqual(patch_mod.run(str(target), str(def_path), apply=False), 1)


class TestValidateName(unittest.TestCase):
    def test_pad_center(self):
        self.assertEqual(discovery.validate_name("Loop Code"), " Loop Code ")
        self.assertEqual(discovery.validate_name("X"), "     X     ")
        self.assertEqual(len(discovery.validate_name("abc")), discovery.NAME_MAX)

    def test_rejects_bad(self):
        for bad in ("", "十一个字节整", 'A" Code', "A\\Code", "A`Code",
                    "A$Code", "twelve chars", "nl\nname"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                discovery.validate_name(bad)


class TestValidateVersion(unittest.TestCase):
    def test_accepts(self):
        self.assertEqual(discovery.validate_version("v9.99"), "v9.99")
        self.assertEqual(discovery.validate_version("1"), "1")

    def test_rejects_charset_and_length(self):
        # 引号/反斜杠/反引号/$ 会破坏内嵌 JS;非 ASCII 无法等长;超 5 字符放不下
        for bad in ("", 'a"b', "a\\b", "a`b", "a$b", "café", "v 9\n", "toolong"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                discovery.validate_version(bad)


class TestTomlMini(unittest.TestCase):
    def test_emit_roundtrip(self):
        for s in ("plain", "It's Code", 'say "hi"', r"█ raw", "a\nb"):
            doc = tomlmini.loads("k = " + tomlmini.emit_str(s))
            self.assertEqual(doc["k"], s)

    def test_control_chars_roundtrip(self):
        # \r 和 \f 曾被漏判成 literal string 而破坏往返;tab 应保留为 literal
        for s in ("a\rb", "a\fb", "a\tb", "a\vb", "tab\tok"):
            doc = tomlmini.loads("k = " + tomlmini.emit_str(s))
            self.assertEqual(doc["k"], s)

    def test_rejects_duplicate_section(self):
        with self.assertRaises(ValueError):
            tomlmini.loads("[m]\nx = 1\n[m]\ny = 2")

    def test_dumps_roundtrip(self):
        d = {"display": {"name": "Bob's Code", "flag": True, "n": 3}}
        self.assertEqual(tomlmini.loads(tomlmini.dumps(d)), d)

    def test_dumps_floats_arrays_nested(self):
        # config.toml 的 [llm] 很可能带 temperature 浮点 / 模型数组:不能被静默损坏
        d = {"top": 1,
             "llm": {"temperature": 0.7, "models": ["a", "b"], "retries": 3, "on": True},
             "display": {"name": "X"}}
        self.assertEqual(tomlmini.loads(tomlmini.dumps(d)), d)

    def test_parse_float_and_inf_nan(self):
        import math
        self.assertEqual(tomlmini.loads("a = 1e3")["a"], 1000.0)
        self.assertEqual(tomlmini.loads("a = 6.02e23")["a"], 6.02e23)
        self.assertEqual(tomlmini.loads("a = -0.5")["a"], -0.5)
        self.assertTrue(math.isinf(tomlmini.loads("a = inf")["a"]))
        self.assertTrue(math.isnan(tomlmini.loads("a = nan")["a"]))

    def test_parse_inline_array(self):
        self.assertEqual(tomlmini.loads("a = [1, 2, 3]")["a"], [1, 2, 3])
        self.assertEqual(tomlmini.loads("a = ['x', 'y,z']")["a"], ["x", "y,z"])
        self.assertEqual(tomlmini.loads("a = []")["a"], [])


class TestDefioStrict(unittest.TestCase):
    def test_apostrophe_name_roundtrip(self):
        p = [{"name": "x", "cat": "name", "desc": "d",
              "old": b'children:"Claude Code"',
              "new": b'children:" Bob_s Code"', "expected": 1, "offset": 0}]
        p[0]["new"] = p[0]["new"].replace(b"_", b"'")
        meta, loaded = defio.loads(defio.dumps({"verified": True}, p))
        self.assertEqual(loaded[0]["new"], p[0]["new"])

    def test_rejects_sha1_without_offset(self):
        with self.assertRaises(ValueError):
            defio.loads("[[patch]]\nname = 'x'\nsha1 = 'aa'\nnew = 'n'\nexpected = 1\n")

    def test_rejects_unequal_length_and_zero_expected(self):
        with self.assertRaises(ValueError):
            defio.loads("[[patch]]\nname = 'x'\nold = 'ab'\nnew = 'abc'\nexpected = 1\n")
        with self.assertRaises(ValueError):
            defio.loads("[[patch]]\nname = 'x'\nold = 'ab'\nnew = 'cd'\nexpected = 0\n")


class TestBackupRefresh(unittest.TestCase):
    def test_stale_orig_not_reused(self):
        with tempfile.TemporaryDirectory() as td:
            target = pathlib.Path(td) / "claude"
            stale = pathlib.Path(td) / "claude.orig"
            target.write_bytes(b"NEW VERSION " + os.urandom(64))
            stale.write_bytes(b"OLD VERSION " + os.urandom(64))
            backup = patch_mod._backup(str(target))
            # 旧备份保留不动,新备份另起名字且内容与当前目标一致
            self.assertNotEqual(backup, stale)
            self.assertTrue(stale.read_bytes().startswith(b"OLD"))
            self.assertEqual(backup.read_bytes(), target.read_bytes())

    def test_identical_orig_reused(self):
        with tempfile.TemporaryDirectory() as td:
            target = pathlib.Path(td) / "claude"
            target.write_bytes(b"SAME " + b"x" * 64)
            orig = pathlib.Path(td) / "claude.orig"
            orig.write_bytes(target.read_bytes())
            backup = patch_mod._backup(str(target))
            self.assertEqual(backup, orig)


class TestLocate(unittest.TestCase):
    def test_sniff(self):
        with tempfile.TemporaryDirectory() as td:
            m = pathlib.Path(td) / "m"
            m.write_bytes(b"\xcf\xfa\xed\xfe" + os.urandom(100))
            j = pathlib.Path(td) / "j"
            j.write_text("#!/usr/bin/env node\nconsole.log(1)\n")
            self.assertEqual(locate.sniff_kind(str(m)), locate.KIND_MACHO)
            self.assertEqual(locate.sniff_kind(str(j)), locate.KIND_JS)
            self.assertEqual(locate.sniff_kind(str(pathlib.Path(td) / "x")), "unknown")


if __name__ == "__main__":
    unittest.main()
