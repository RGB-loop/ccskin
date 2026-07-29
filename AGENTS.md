# AGENTS.md

ccskin — 给 Claude Code 的 macOS 二进制做"仅显示层"换肤(图标/名字/版本号/
主题色)。全部背景、步骤、坑见 `README.zh.md`、`docs/pitfalls.md`、
`docs/checklist.md`,先读它们再动手。

## 工作流

```sh
python3 -m patcher skin                 # 交互向导(自动定位安装)
python3 -m patcher all --apply -y       # 非交互全流程
python3 -m patcher analyze <bin>        # 分步: analyze/patch/sign/verify
python3 -m unittest discover -s tests   # 单元测试(合成 bundle,不碰真二进制)
```

## 硬性约束

- 任何替换必须逐字节等长;`expected` 计数或 sha1 指纹不符即中止,
  禁止强行 patch
- 测试/示例一律用**合成 bundle**(tests/test_core.py 的 make_bundle),
  仓库里不放可执行二进制(claude 本体及其备份,几百 MB)、
  不放对方源码(icon_table 只存 offset+sha1);文档截图不受此限
- `config.toml`、`bin/`、`backups/` 不入库
- 图标新设计: 加 `design/*.toml`,先 `preview` 验证槽位字符数,
  再在 tests 里补结构用例
- analyze 发现失败: 用 `--llm` 或人工核对,不要靠猜改锚点
