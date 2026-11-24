---
name: webtools
description: 免费额度的多源网页搜索与分层抓取（curl_cffi → Camoufox → Firecrawl），面向被墙/污染 DNS 环境；支持 MCP 服务器、CLI 与模组站内搜索。
---

# webtools 技能

本技能提供一套"免费额度优先"的搜索/抓取工具链。可用两种方式使用：

## 方式 A：MCP 服务器（推荐）

- 启动：`python -m webtools.mcp_server`（stdio）
- 安装到客户端：`python install.py --auto`（自动检测并写入配置）
- 工具：
  - `web_search(query, limit?, all?, reddit?, sites?)`
  - `web_research(query, top?, limit?, reddit?)`
  - `web_fetch(url, browser?)`
  - `quota_status()` / `channel_status()`

## 方式 B：命令行

```bash
webtools search "查询词" --limit 5
webtools search "skyui" --sites nexus:skyrimspecialedition
webtools research "问题" --top 4
webtools fetch "<url>" [--browser] [--no-cache]
webtools visit "<url>" [--act click --text "登录"]
webtools status
webtools doctor --json
```

## 降级策略（抓取）

1. API 直连（GitHub / StackExchange / Nexus）
2. curl_ffi（3 种 TLS 指纹轮换）
3. Camoufox 真浏览器（过 Cloudflare，15–90 秒）
4. Firecrawl 中转（最后兜底；加速域名 github/nexusmods/mod.io 禁止使用）

## 配置

- 密钥：`~/.webtools/keys/{tavily,firecrawl,nexus}.key` 或环境变量
- 全部可选；缺 key 自动跳过对应渠道
- 每月额度账本：`~/.webtools/state/quota.json`（`webtools status` 查看）