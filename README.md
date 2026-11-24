# webtools

给 AI agent 用的**免费额度网页搜索/抓取工具链**，专为**被墙 / DNS 污染 / 验证码**环境设计。
同时提供 **MCP 服务器**（推荐）、**CLI**、**技能文档**三种形态，一套内核。

## 特性

- **多源并行搜索**：tavily / firecrawl（各 1000/月免费额度，**按月顺序使用、绝不并行**）
  + github（免费 API）+ reddit（arctic-shift 免费存档）
- **分层抓取**：API 直连 → curl_cffi（3 种 TLS 指纹轮换）→ **Camoufox 真浏览器**（过 Cloudflare）
  → Firecrawl 中转（最后兜底）
- **模组站内搜索**：Nexus（走浏览器过 CF）/ mod.io / GitHub，域名级过滤，不走中转
- **额度账本**：记录每月用量，tavily 耗尽自动切 firecrawl，跨月自动重置
- **渠道健康**：瞬时失败不判死，3 次才隔离，冷却指数退避
- **代理感知**：自动识别系统代理（含 MITM 代理，如 Watt Toolkit），加速域名代理优先
- **零付费 API**：只用免费额度渠道，缺 key 自动跳过

## 安装（AI 自动适配）

下载本仓库后，把它交给你的 AI agent，说"安装"即可——仓库根目录的 `AGENTS.md` / `CLAUDE.md`
会指导 AI 自动执行：

```bash
python install.py --detect --json     # 探测本机客户端
python install.py --auto              # 自动写入正确的 MCP 配置
python -m webtools doctor --json      # 验证
```

## 手动安装

```bash
pip install -e .                      # 或 pipx install .
python install.py --client zcode      # 或 claude-desktop / cursor / claude-code
```

可选浏览器能力（抓 Cloudflare 页面）：

```bash
pip install -e ".[browser]"
python -m camoufox fetch
```

## 使用

### MCP 工具

`python -m webtools.mcp_server` 暴露：

| 工具 | 说明 |
|---|---|
| `web_search(query, limit?, all?, reddit?, sites?)` | 多源搜索（sites 支持模组站内搜索） |
| `web_research(query, top?, limit?, reddit?)` | 搜索 + 抓取前 N 页，返回摘要 |
| `web_fetch(url, browser?)` | 分层抓取页面正文 |
| `channel_status()` | 渠道健康台账 |
| `quota_status()` | 月度额度账本 |

### CLI

```bash
webtools search "查询词" --limit 5
webtools search "skyui" --sites nexus:skyrimspecialedition
webtools research "问题" --top 4
webtools fetch "<url>" [--browser] [--no-cache]
webtools visit "<url>" [--act click --text "登录"]
webtools status
webtools doctor --json
```

## 配置

- 密钥目录：`~/.webtools/keys/`（`tavily.key` / `firecrawl.key` / `nexus.key`，各一行）
- 或环境变量：`TAVILY_API_KEY` / `FIRECRAWL_API_KEY` / `NEXUS_API_KEY`
- 全部可选，缺哪个就跳过哪个渠道
- 自定义目录：`WEBTOOLS_HOME=/path/to/dir`

免费额度申请：
- Tavily：https://app.tavily.com/home （1000 credits/月）
- Firecrawl：https://firecrawl.dev （1000 credits/月）
- Nexus 个人 API key：https://www.nexusmods.com/users/myaccount?tab=api

## 目录结构

```
src/webtools/
├─ search.py        # 多源搜索 + 额度账本 + 模组站内搜索
├─ fetch.py         # 分层抓取（tier 0..3）
├─ research.py      # 健康台账 + 搜抓一体
├─ browser/         # Camoufox 服务（9377）+ 客户端
├─ mcp_server.py    # MCP stdio 服务器
├─ cli.py           # webtools 命令行
└─ doctor.py        # 环境自检
adapters/           # 各客户端配置模板
install.py          # 客户端探测与配置写入
```

## License

MIT