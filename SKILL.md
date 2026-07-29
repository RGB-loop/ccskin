---
name: ccskin
version: 0.2.0
description: >
  给 Claude Code CLI 换肤/换主题/改启动画面:替换像素图标、产品名、版本号显示、
  主题色(仅显示层,不动内部逻辑)。当用户提到 claude code 换肤、改主题、改图标、
  改启动画面、改版本号显示、customize/theme/skin/startup banner/icon 时使用。
  也用于新版本 claude 发布后的皮肤重新适配。
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "python3 -m patcher --help"
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# ccskin — Claude Code 换肤

通过"等长字节替换 + ad-hoc 重签名"修改 claude 二进制的**显示层**。
内部版本常量、更新检查、API 行为完全不变(`--version` 仍打印真实版本,这也是验证手段)。

## 硬性规则(违反会搞坏二进制)

- 任何替换必须逐字节等长;`expected` 计数或 sha1 指纹不符**必须中止**,禁止强行 patch
- 只在用户自己的二进制上操作;不分发二进制;不提交 `config.toml`
- 备份(`<binary>.orig`)已存在时绝不覆盖

## 怎么用(按场景)

所有命令都在**本 skill 目录下**执行(先 `cd` 进来),零依赖,系统 python3 即可。

1. **用户想换肤(首选)**: 交互向导,自动定位安装,可视化选择
   `python3 -m patcher skin [--pty]`
   非交互环境用 `python3 -m patcher all --apply -y`(读 config.toml 默认值)

2. **新版本 claude 适配**: 把二进制给用户跑
   `python3 -m patcher all <二进制路径> --apply -y --pty`
   或省略路径自动定位。analyze 报告锚点缺失时 → 加 `--llm`(需配 config.toml 的 [llm]),
   仍失败则明确告诉用户"该版本结构变了",不要猜。

3. **改显示内容(名字/版本/颜色/图标)**: 引导用户跑 `skin` 向导;
   或编辑 `config.toml`(name ≤11 字符、version ≤5 字符、theme_color 6 位 hex、
   icon_design 指向 design/*.toml),然后 `all --apply -y`

4. **新图标设计**: 复制 `design/octopus.toml` 改字符,先
   `python3 -m patcher preview <新设计.toml>` 看效果,再进 skin 流程。
   槽位字符数约束写在 design 文件注释里,违反会被 glyphpack 拒绝。

5. **恢复原版**: `cp <binary>.orig <binary>`

## 验证清单(做完必须过)

- `verify` 图标预览形状正确;显示名/版本计数 > 0
- `--version` 输出**真实**版本号(证明内部没被碰)
- `--pty` 抓屏含显示名和版本

## 参考文档

- 原理与流程: `README.zh.md`(中文) / `README.md`(EN)
- 坑清单(排障先查): `docs/pitfalls.md`
- 检查清单: `docs/checklist.md`
