# webtools — Agent 适配协议（本文件自述，无需用户提示）

你正在处理 **webtools** 仓库。当用户要求"安装 / 使用 / 适配这个项目"时，**不要询问客户端类型**，按本协议直接执行：

1. **检测客户端**
   ```bash
   python install.py --detect --json
   ```
   输出形如 `{"zcode": {"path": "...", "detected": true, "installed": false, ...}, ...}`

2. **安装到每个检测到的客户端**
   ```bash
   python install.py --client <name>     # 对 detected=true 且 installed=false 的逐个执行
   ```
   脚本会写入该客户端正确格式的 MCP 配置（幂等，不会重复）。

3. **（可选）浏览器能力**
   仅当用户需要抓 Cloudflare/JS 页面时：
   ```bash
   python install.py --auto --with-browser
   ```
   会 `pip install camoufox` 并下载约 1.2GB 的浏览器二进制。

4. **验证**
   ```bash
   python -m webtools doctor --json
   ```
   预期：`checks.curl_cffi == true`；`mcp_registered.<client> == true`；`key:*` 取决于用户是否配置。

5. **向用户报告**
   - 装了哪些客户端、需要重启才能生效
   - 缺哪些 key（免费申请地址见 `~/.webtools/keys/README.txt`）
   - 工具如何验证（客户端里应出现 web_search / web_research / web_fetch / quota_status / channel_status）

## 排错

- 脚本报 `unknown client` → 读 `webtools.manifest.json` + `adapters/` 模板手工适配
- `curl_cffi=false` → `pip install curl_cffi`
- `camoufox_browser` 不可用 → `pip install webtools[browser] && python -m camoufox fetch`
- 客户端里看不到工具 → 确认重启；ZCode 看 设置 → MCP，Claude Desktop 看 json 是否写对

## 日常使用（安装完成后）

- 搜索：`webtools search "查询词" --limit 5`
- 抓取：`webtools fetch "<url>"`
- 快照+交互：`webtools visit "<url>" [--act click --text "登录"]`
- 状态：`webtools status`（额度账本 + 渠道健康）
- MCP 客户端：直接调用工具，无需命令行