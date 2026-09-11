# ccskin

给 Claude Code 的 macOS 单文件二进制换"皮肤":替换启动画面的**像素图标、
产品名、版本号显示、主题色**。只改显示层,内部版本常量、自动更新检查、
API 上报等行为完全不变。

![换肤后的 Claude Code 启动画面:蓝色像素小章鱼图标,名字 Loop Code,版本 v9.99](docs/images/startup.png)

<sub>实际效果 —— 图标 `design/octopus.toml` · 名字 `Loop Code` · 版本 `v9.99` ·
主题色 `#2E9BFF`。模型那一行及以下都是 Claude Code 自己的输出,未做任何改动。</sub>

原理一句话:找到显示相关的字符串,做**逐字节等长替换**,再 ad-hoc 重签名。

具体改哪份字符串取决于构建方式,工具会自动判定:

- **2.1.26x 起**用 `bun build --bytecode` 编译。二进制里那份 JS 源码文本还在,
  但运行时读的是 **bytecode 常量池** —— 改源码文本能通过一切校验而屏幕毫无变化,
  所以补丁打到常量池。
- **更早的版本**没有 bytecode,仍改 JS 源码文本。

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

显示位随版本增减,analyze 会如实报告每一处命中与缺失。

| 位置 | 2.1.261(bytecode) | 更早的版本(源码文本) |
|---|---|---|
| 启动大标题 | ✅ | ✅ |
| 首次运行欢迎页 | ✅ | — |
| 输入框边框 / 头部标题 | 上游已移除 | ✅ |
| 版本号显示 | ❌ 与 `--version` 共用常量 | ✅ |
| 像素图标(4 种动画形态 + 第三行 + 底行) | ✅ | ✅ |
| HTML 登录/错误页 logo | ✅ | ✅ |
| 主题色 | ✅ | ✅ |

**没改的**:`--version` 输出(除非显式开 `rewrite_real_version`)、`/doctor`、
更新提示、状态消息等 —— 那些用的是内部常量或属信息性文本,不属于启动画面。

## 图标设计指南

`design/octopus.toml` 里每个槽位填"可见字符"。**槽位边界由二进制现场探测**
—— 上游会在版本之间挪动字符在槽位间的分配(2.1.261 就把 r1E 从 5 列加到 6 列、
r1R 从 1 列减到 0),但整行列数不变,所以设计稿只要保证**每行总列数**对就行:

- `default` / `look-left` / `look-right`:第一行 8 列、第二行 9 列
- `arms-up`:第一行 9 列、第二行 9 列
- `mid.row`=5:第二行机身中部(四种变体共用一个常量;章鱼的眼睛在这里)
- `feet.row`=5(第三行)、`n2p.row`=7、`html.row1/2/3`=8/9/7

**去重约束**:相同内容的字符串在二进制里只有一条,被多个变体共用。上游自己
就让 `default` 与 `arms-up` 共用 r1E,所以设计稿不能给它们不同图案 —— 举手
姿势只能靠 r1L/r1R 表现。冲突时 analyze 会直接报错并指出是哪两个槽位。

改完设计稿先 `python3 -m patcher preview your.toml` 看效果,
再跑 analyze 生成新定义。可用字符:任意 BMP 的 `\uXXXX`(方块字符
▟▙▛▜▐▌▄▀█▖▗▘▝、◉●○、╵ 等)+ 槽位字面位上的 ASCII。

## 注意

- **bytecode 构建上版本号改不了**:启动画面的版本与 `--version` 共用同一条
  常量,没法只改显示,默认自动跳过。愿意接受 `--version` 和更新检查一起变的话,
  配 `rewrite_real_version = true`(新版本号必须与真实版本等长)
- **自动更新会覆盖补丁**:在 claude 设置里关掉 autoUpdates,或设
  `DISABLE_AUTOUPDATER=1`;每次升级后重跑本工具即可
- 首次运行补丁后的二进制如被系统拦(Killed: 9):设置 → 隐私与安全性 →
  "仍要打开",或 `xattr -d com.apple.quarantine <文件>`
- 恢复原版:补丁时自动备份 `<binary>.orig`,拷回即可
