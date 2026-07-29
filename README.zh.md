# ccskin

给 Claude Code 的 macOS 单文件二进制换"皮肤":替换启动画面的**像素图标、
产品名、版本号显示、主题色**。只改显示层,内部版本常量、自动更新检查、
API 上报等行为完全不变。

![换肤后的 Claude Code 启动画面:蓝色像素小章鱼图标,名字 Loop Code,版本 v9.99](docs/images/startup.png)

<sub>实际效果 —— 图标 `design/octopus.toml` · 名字 `Loop Code` · 版本 `v9.99` ·
主题色 `#2E9BFF`。模型那一行及以下都是 Claude Code 自己的输出,未做任何改动。</sub>

原理一句话:Bun 单文件二进制把 JS 源码原样嵌在 Mach-O 里,找到显示相关的
字符串锚点,做**逐字节等长替换**,再 ad-hoc 重签名即可。

## 快速开始

**作为 agent skill(最容易分发)**: 仓库根目录自带 `SKILL.md`,
用 [skills CLI](https://skills.sh) 一条命令安装(支持 Claude Code、
Kimi Code、Codex、Cursor 等),装完直接对 agent 说"给 claude code 换个皮肤":

```sh
npx skills add RGB-loop/ccskin      # 加 -g 装到全局
```

或者手动安装: 直接克隆进 agent 的技能目录(**目录名必须是 ccskin**):

```sh
git clone https://github.com/RGB-loop/ccskin ~/.claude/skills/ccskin   # Claude Code
git clone https://github.com/RGB-loop/ccskin ~/.agents/skills/ccskin  # Kimi Code
```

**交互向导(推荐)**: 自动定位系统里的 claude 安装,可视化选择
图标/名字/版本/主题色,确认后全自动执行并保存配置:

```sh
python3 -m patcher skin
```

**非交互一把梭**(用 config.toml 里的配置;binary 参数可省略,自动定位):

```sh
python3 -m patcher all --apply -y --pty
```

自动完成: 定位 → 去隔离属性 → 分析锚点 → 生成 `patches/<版本>.toml` →
等长替换(自动备份 `.orig`)→ ad-hoc 重签名 → 验证(图标预览 /
`--version` / 抓真实启动画面)。任何一步失败都会停下并说明原因;
analyze 发现不了锚点(版本大改结构)时加 `--llm` 让 LLM 帮你判断。

想分步控制或排查时,用四个子命令:

```sh
python3 -m patcher analyze [binary]              # 1. 发现锚点→生成定义
python3 -m patcher patch   [binary] patches/X.toml --apply
python3 -m patcher sign    [binary]              # 3. 重签名(自动去隔离)
python3 -m patcher verify  [binary] --pty        # 4. 验证
```

## 目录结构

```
patcher/            # 工具包(零依赖,系统 python3 即可)
  cli.py            #   命令行入口(skin/analyze/patch/sign/verify/all/preview)
  interactive.py    #   skin 交互向导
  locate.py         #   自动定位系统安装
  analyze.py        #   step1 发现锚点→生成补丁定义
  discovery.py      #     锚点模式与提取逻辑(核心)
  glyphpack.py      #     像素设计稿→等长转义串
  patch.py          #   step2 等长替换(严格校验+备份)
  sign.py           #   step3 ad-hoc 重签名
  verify.py         #   step4 预览/--version/抓屏验证
  llm.py            #   可选 LLM 辅助(OpenAI 兼容,只出建议)
design/*.toml       # 图标设计稿图库(octopus/rocket/invader/ghost,可自创)
patches/*.toml      # 补丁定义(icon_table 只存 offset+sha1,不存对方源码)
tests/              # 单元测试(合成 bundle,python3 -m unittest discover -s tests)
docs/pitfalls.md    # 坑清单
docs/checklist.md   # 检查清单
config.example.toml # 配置模板(复制为 config.toml)
claude_entitlements.plist  # 重签名用的授权文件
```

## 配置(config.toml)

- `display.name`:显示名,**最多 11 个字符、仅限可打印 ASCII**
  (名字嵌进二进制内的 JS 源码,非 ASCII 无法等长替换),不足自动用空格居中补齐
- `display.version`:版本号显示,**1-5 字符**(等长替换的物理上限),
  不足自动补空格
- `display.icon_design`:`design/` 下的图标设计稿文件名
- `[llm]`:`base_url` / `api_key` / `model` — analyze 自动发现失败时的辅助
  分析,OpenAI 兼容接口;推荐用环境变量 `PATCHER_LLM_API_KEY` 而不是写进文件

## 显示位清单(改了哪些)

| 位置 | 内容 |
|---|---|
| 启动大标题 | `children:"Claude Code"` + 后面的 `["v",变量]` |
| 输入框边框(展开) | `("Claude Code")` 与模板 `` `v${变量}` `` |
| 输入框边框(紧凑) | `(" Claude Code ")` |
| 头部/任务面板标题 | `["Claude Code"," "]` 与 `["v",变量]` |
| 像素图标 | logo 表 4 种动画形态 + 第三行小脚 + N2p 底行 |
| HTML 登录/错误页 | 反引号模板里的 3 行 ASCII logo |

**没改的**:`--version` 输出、`/doctor`、更新提示、状态消息等 —— 那些用的
是内部常量或属信息性文本,不属于启动画面。

## 图标设计指南

`design/octopus.toml` 里每个槽位填"可见字符",工具按二进制里原槽位的
转义(`\uXXXX`,6 字节)/字面(1 字节)形状等长打包。槽位字符数约束:

- `default` / `look-left` / `look-right`:r1L=2、r1E=5、r1R=1、r2L=2、r2R=2
  (第一行 8 列;第二行共 9 列)
- `arms-up`:r1L=2、r1E=5、r1R=2(第一行 9 列)
- `mid.row`=5:第二行机身中部(四种变体共用一个常量,无法按变体区分;
  章鱼的眼睛就在这里)
- `feet.row`=5(第三行)、`n2p.row`=7、`html.row1/2/3`=8/9/7

改完设计稿先 `python3 -m patcher preview your.toml` 看效果,
再跑 analyze 生成新定义。可用字符:任意 BMP 的 `\uXXXX`(方块字符
▟▙▛▜▐▌▄▀█▖▗▘▝、◉●○、╵ 等)+ 槽位字面位上的 ASCII。

## 注意

- **自动更新会覆盖补丁**:在 claude 设置里关掉 autoUpdates,或设
  `DISABLE_AUTOUPDATER=1`;每次升级后重跑本工具即可
- 首次运行补丁后的二进制如被系统拦(Killed: 9):设置 → 隐私与安全性 →
  "仍要打开",或 `xattr -d com.apple.quarantine <文件>`
- 恢复原版:补丁时自动备份 `<binary>.orig`,拷回即可
