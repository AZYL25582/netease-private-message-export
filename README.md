# 网易云私信导出：安全修复版

将本人账号指定会话保存为本地 JSON/TXT，可选择下载图片。修复版不复制浏览器 Cookie 库、不生成解密密钥或 Cookie 明文文件；抓取失败不再写成功状态，也不会覆盖原完整结果。

详细操作见 [SKILL.md](SKILL.md)。2026-09-22 已在 Windows、Python 3.12.9、Node.js 24.15.0 下完成真实账号直接读取模式的会话列表、完整分页抓取、导出与重复追加验收，并完成真实图片下载验证。浏览器兜底模式及其他平台尚未验收；验证不保证未来接口或所有账号均适用。

## 快速开始（Windows PowerShell）

需要 Python 3.8+、Node.js 18+。在 Skill 目录运行，替换用户选择的私密输出目录和对方 ID：

```powershell
$private = '<Skill之外的私密输出目录>'
$browserData = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"
python scripts/extract_cookies.py "$browserData" --profile Default list "$private/sessions.json"
python scripts/extract_cookies.py "$browserData" --profile Default fetch <对方ID> "$private/raw.json"
python scripts/export.py "$private/raw.json" "$private/export" --append
# 可选：下载图片，再刷新 TXT
python scripts/download_images.py "$private/export/netease_messages.json"
python scripts/export.py "$private/raw.json" "$private/export" --append
```

浏览器锁库时自行退出再试；不支持 v20/App-Bound Cookie 解密，也不尝试绕过。可用 `--profile "Profile 1"` 指定配置。浏览器兜底模式及其额外依赖见 SKILL.md。

没有经确认可用的副本、且原库被锁时，抓取前须自行完全退出 Edge/Chrome。低频导出不新增凭据缓存。现有未锁定副本在技术上仍可输入，但其中可能包含其他网站凭据，不能自动认定安全、复制或清理。`--append` 只合并本地数据；更新聊天仍需先运行全量分页 `fetch`。

## 重要变化

- **旧版 Cookie 两步命令已经停用。** `extract_cookies.py` 现在直接通过内存管道启动 list/fetch；`decrypt_cookies.js` 为内部模块，不再把凭据写文件。
- `list` 必须明确指定会话列表输出路径。联系人数据保存在文件中，不写到默认日志。
- 未完成的抓取保存为 `raw.json.partial.json` 并返回非零状态。只有 `more=false` 才能标接口分页结束。
- 旧版或部分 raw 必须使用 `export.py --allow-incomplete` 导出至全新目录，标明未经验证；不能拿来覆盖、追加完整结果。
- 追加会校验双方 ID。完整导出只说明本次接口遍历结束；不保证已删除或服务端未提供的历史也存在。
- 图片只连接有限的 HTTPS CDN；白名单内的 HTTP 默认端口链接先在本地升级为 HTTPS。Content-Type 只作允许类型粗筛，文件签名决定格式及扩展名，兼容 CDN 把 PNG 标为 `image/jpg`。仍校验证书、公网地址、重定向、体积及支持的内容签名。未知域名和非标准端口仍拒绝。
- 身份或消息记录冲突属于硬失败（退出码 1），不发布本次正文；网络中断等部分抓取是退出码 2，两者分开处理。
- 覆盖时自动留备份，备份同样包含聊天隐私。运行文件禁止写入 Skill 目录；Windows 文件权限继承目标目录，请选择个人可访问的私密目录。

## 检查与打包

```powershell
python -B -m unittest discover -s tests -v
node --test tests/test_netease.js
python -B scripts/check_privacy.py .
python -B scripts/package_release.py '<Skill外的输出ZIP路径>'
```

测试只使用虚构数据、模拟请求；不得接入真实 Cookie 运行测试。打包仅收录固定白名单。隐私扫描通过不等于没有任何个人信息。

隐私边界、兼容性及旧版文件处理见 [安全与限制](references/security-and-limitations.md)。保留原版 MIT 许可，见 [LICENSE](LICENSE)。
