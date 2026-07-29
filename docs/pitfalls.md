# 坑清单

每条都附规避方法,工具里已固化的用 → 标注。

## 1. 替换必须逐字节等长
Bun 单文件二进制内嵌的是 JS 源码**文本**,任何长度变化都会让后续内容的
偏移整体错位,直接损坏二进制。所有替换(old/new)字节数必须相等。
→ `patch.py` 对每条强制断言,不等长即中止。

## 2. `grep -c` 在压缩 JS 上会骗人
minified bundle 经常整个模块一行,`grep -c` 数的是"含匹配的行数"不是次数。
→ 统计出现次数用 `grep -abo <pat> file | wc -l`(工具内用 `bytes.count`)。

## 3. 二进制里有"假锚点"区
V8 字节码快照/字符串表区也能搜到 `arms-up`、`look-left` 等词,但那不是
JS 源码,**不能改**。辨别方法:上下文不是可读的 JS。
→ `discovery.py` 只认完整结构锚点(`r1L:" \u2590"` + 括号配对),不会误伤。

## 4. 压缩变量名每个版本都变
`M2p`/`N2p`/`Ida`/`YZy`/`Vxr` 是构建时随机生成的,换版本全变。
→ 锚点只用字符串**内容**(`r1L:" \u2590"`、`children:"Claude Code"`),
版本号位用正则 `\["v",[A-Za-z_$][\w$]{0,5}\]` 在名字锚点后 800B 内提取。

## 5. 补丁后必须重签名
原二进制是 Apple 开发者签名 + hardened runtime,改一个字节签名即失效,
arm64 直接拒绝运行。做法:
```
codesign --remove-signature <bin>
codesign --force --sign - --options runtime --entitlements <ent.plist> <bin>
```
entitlements(含 JIT 权限)**必须在补丁前导出**(签名还有效时);
已导出过则复用项目根目录的 `claude_entitlements.plist`。
→ `sign.py` 全自动。

## 6. Gatekeeper 首杀(Killed: 9)
ad-hoc 重签名的二进制首次运行可能被内核直接 `Killed: 9`,无窗口提示。
另外 `cp` 复制出来的副本会**继承/重获** quarantine 隔离属性,导致
`--version` 这类调用直接被杀。排查:`log show --last 2m | grep -i gatekeeper`。
→ `analyze` 和 `sign` 都会自动 `xattr -d com.apple.quarantine`;
真被拦了就去 设置 → 隐私与安全性 → "仍要打开"。

## 7. 自动更新会覆盖补丁
claude 原生安装版会自动更新,新二进制下来补丁就没了。
→ 关闭 autoUpdates 或设 `DISABLE_AUTOUPDATER=1`;升级后重跑本工具。

## 8. 锚点计数随版本漂移
`children:"Claude Code"` 在 2.1.214 有 2 处(欢迎页 + 任务面板),
别的版本可能增减。计数对不上**就是版本不符或已打过补丁**,不要硬改。
→ 定义文件里每条都有 `expected`,`patch.py` 校验失败即整体中止。

## 9. 版本号显示位长度受限
`["v",Ida]` → 替换为 `["xxxxx"]`,内容最多 5 字符;模板位 `` `v${YZy}` ``
最多 7 字符(含补位空格)。想显示更长版本号就得改代码结构,不值得。
→ `analyze.py` 自动按位长校验 + 补空格。

## 10. pty 抓屏要耐心
`script -q` 抓 TUI 启动画面时,太早发 `/exit` 会被吞掉,进程挂着不退。
→ `verify.py --pty` 固定等约 10s 抓完即 terminate,不等退出。

## 11. 不要对同一二进制重复打补丁
替换是单向的(old 串被换成 new 串),重复执行会因找不到锚点而失败
—— 这是**预期行为**,说明已经打过或版本不符。要重打就先恢复 `.orig`。
备份带指纹比对: 已有 `.orig` 与当前目标内容一致才复用;不一致
(比如自动更新换了新版本)会另存 `.orig.1`/`.orig.2`,旧备份永不被顶掉,
恢复时用 patch 步骤最后打印的那条 `cp` 命令。

## 12. 图标槽位的字面/转义形状不能乱
原槽位里字面位(1 字节空格)和转义位(6 字节 `\uXXXX`)的位置是固定的,
设计稿只能在字面位放 ASCII,转义位放任意 BMP 字符。
→ `glyphpack.py` 强制校验,报错会指出具体槽位。

## 13. 校验通过 ≠ 内容真的变了
逐条校验只证明锚点都在,不证明写回的内容真的变了(替换调用一旦丢失,
校验照样全过,写回的却是原文)。
→ `patch.py` 兜底断言替换后内容必须不同于原文;
`verify` 对显示名出现 0 次直接判失败。

## 14. 机身中部常量必须带上下文锚点
logo 第二行中部的 `"\u2588\u2588\u2588\u2588\u2588"`(5×█)在文件里出现十几次,
还有别的组件也用 `clawd_background` 包着别的转义字面量。单独拿 5×█
或裸上下文去匹配都会撞车。→ `discovery.py` 用
`clawd_background",children:"` + 内容的组合锚点;`verify` 预览再叠加
限定 logo 表附近窗口 + 恰好 5 个转义。

## 15. 同一槽位内容在不同变体里可能要映射成不同图案
不同变体的同一槽位原型内容可能相同(如 arms-up 与 default 的 r1E),
而设计稿想给它们不同图案 —— 全局内容替换会互相冲突。
→ `discovery.py` 对 logo 主表做**位置化重建**(按正则命中位置逐个变体
替换),不做全局内容替换。

## 16. pty 抓屏
- 不要用 `script(1)`:抓屏文件带缓冲,进程被杀时可能一个字节都没落盘;
  且 TERM=dumb 时 TUI 不处理 /exit,session 永不退出。
- 杀 TUI 子进程前**必须先关 pty master fd**:否则子进程在内核退出路径上
  等 master 关闭,父进程却阻塞在 waitpid 等子进程退出 —— 死锁。
  reap 全程用非阻塞 waitpid。
- TUI 会把空格优化成光标移动转义,抓到的 "Loop Code" 实际是 "LoopCode",
  比对前去掉所有空白。
→ `verify.py` 按此实现:`pty.fork` 自读 pty,结束时先关 master fd
再 SIGTERM→SIGKILL。
