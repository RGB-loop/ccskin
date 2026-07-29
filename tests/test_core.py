"""核心单元测试: 全部跑在**自造**的合成 bundle 上。

合成 bundle 只复刻结构(槽位形状/锚点模式),槽位内容是我们自己编的,
不包含任何 Claude Code 的代码。运行: python3 -m unittest discover -s tests -v
"""
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from patcher import defio, discovery, glyphpack, locate, patch as patch_mod, tomlmini


# ---------- 合成 bundle(结构仿真,内容自编) ----------
LOGO_TABLE = (
    '{default:{r1L:" \\u2590",r1E:"\\u2591\\u2592\\u2593\\u2588\\u2591",r1R:"\\u258C",'
    'r2L:"\\u259D\\u259C",r2R:"\\u259B\\u2598"},'
    '"look-left":{r1L:" \\u2590",r1E:"\\u2591\\u2592\\u2593\\u2588\\u2591",r1R:"\\u258C",'
    'r2L:"\\u259D\\u259C",r2R:"\\u259B\\u2598"},'
    '"look-right":{r1L:" \\u2590",r1E:"\\u2591\\u2592\\u2593\\u2588\\u2591",r1R:"\\u258C",'
    'r2L:"\\u259D\\u259C",r2R:"\\u259B\\u2598"},'
    '"arms-up":{r1L:"\\u2597\\u259F",r1E:"\\u2591\\u2592\\u2593\\u2588\\u2591",'
    'r1R:"\\u2599\\u2596",r2L:" \\u259C",r2R:"\\u259B "}},'
    'N2p={default:" \\u2597   \\u2596 ","look-left":" \\u2598   \\u2598 ",'
    '"look-right":" \\u259D   \\u259D ","arms-up":" \\u2597   \\u2596 "}'
)
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


def make_bundle() -> bytes:
    parts = [
        os.urandom(500),
        LOGO_TABLE.encode(),
        os.urandom(300),
        FEET.encode(), os.urandom(100), FEET.encode(),
        os.urandom(200), MID.encode(),
        os.urandom(200), HTML_LOGO.encode(),
        os.urandom(300), NAMES.encode(),
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
        self.patches, self.report, self.problems = discovery.build_patches(
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
        patches, _, problems = discovery.build_patches(
            self.data, " Loop Code ", None, None)
        self.assertEqual(problems, [])
        cats = {p["cat"] for p in patches}
        self.assertNotIn("icon", cats)
        self.assertNotIn("version", cats)


class TestDefio(unittest.TestCase):
    def test_roundtrip(self):
        data = make_bundle()
        patches, _, problems = discovery.build_patches(
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
        patches, _, problems = discovery.build_patches(
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
