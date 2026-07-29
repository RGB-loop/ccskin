# 检查清单

## 补丁前

- [ ] 新二进制放入 `bin/`(或任意路径),记录版本号
- [ ] `config.toml` 里 name(≤11 个 ASCII 字符)/ version(≤5 字符)/ icon_design 确认
- [ ] `python3 -m patcher preview <design>` 预览过图标

## 一键流程(`all --apply`)

- [ ] analyze 报告每类锚点都有 ✓(icon_table/icon_mid/icon_feet/name_*/version_*)
- [ ] 图标对照预览是你要的图案;名字/版本汇总符合预期
- [ ] 有 ✗ → 先人工查;查不动再 `--llm`,不要强行继续
- [ ] patch 逐条校验全过,备份 `.orig` 已生成
- [ ] `codesign -v` 通过,quarantine 已自动去除

## 验证(verify)

- [ ] 图标预览三行形状正确
- [ ] `--version` 输出**真实内部版本号**(证明内部常量没被碰)
- [ ] `--pty` 抓屏含显示名/显示版本
- [ ] 实际启动一次,交互正常(输入框边框文字、/status 等)

## 入库(git)

- [ ] 二进制、`.orig`、`bin/` 不在提交里(.gitignore 已覆盖)
- [ ] `config.toml`(含 api_key)不在提交里
- [ ] 新版本的 `patches/<版本>.toml` 验证后提交,方便回溯
