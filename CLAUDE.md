# webtools — Claude Code / CLAUDE.md 适配协议

本仓库自带自动适配协议。**先读同目录的 `AGENTS.md`，按其步骤执行**（内容一致）：

1. `python install.py --detect --json`
2. 对每个 `detected=true, installed=false` 的客户端执行 `python install.py --client <name>`
3. 验证 `python -m webtools doctor --json`
4. 向用户报告安装结果与缺失的 key

日常使用：
- `webtools search "查询词" --limit 5`
- `webtools fetch "<url>"`
- `webtools visit "<url>"`（Camoufox 浏览器）
- `webtools status`（额度 + 渠道健康）
- 或在支持 MCP 的客户端里直接使用 `python -m webtools.mcp_server` 暴露的工具。