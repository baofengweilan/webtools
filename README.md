# webtools

[English](#english) | [中文](#中文)

---

## English

Free-quota web search & fetch toolkit for AI agents, built for **poisoned-DNS / GFW / CAPTCHA** environments.
One core, three shells: **MCP server** (recommended), **CLI**, and **skill docs**.

> **Positioning — fallback first.** Always let your agent try its **built-in
> WebSearch / WebFetch** first. webtools is the **second line**: it takes over
> when the built-ins fail (blocked sites, exhausted quota, timeouts, CAPTCHA
> walls) — and doubles as a standalone toolkit for agents that have no
> built-ins at all.

### Features

- **Multi-source parallel search**: tavily / firecrawl (1000 free credits/month each, used
  **sequentially via a monthly quota ledger — never together**) + github (free API)
  + reddit (arctic-shift archive, free)
- **Tiered fetching**: API endpoints → curl_cffi (3 rotating TLS fingerprints) →
  **Camoufox real browser** (passes Cloudflare) → Firecrawl relay (last resort only)
- **Mod-site search**: Nexus (browser through CF) / mod.io / GitHub — domain-scoped,
  no relay engines
- **Quota ledger**: per-month usage tracking; tavily exhausted → auto-switch to firecrawl;
  resets on the 1st of each month
- **Channel health**: transient failures don't kill a channel; quarantined after 3 strikes
  with exponential backoff
- **Proxy-aware**: detects the system proxy (including MITM proxies like Watt Toolkit);
  accelerated domains go proxy-first, everything else direct-first
- **Zero paid APIs**: free-tier channels only; missing keys are skipped silently

### Install (AI auto-adapt)

Download this repo, hand the folder to your AI agent and say "install" —
`AGENTS.md` / `CLAUDE.md` in the repo root guide the agent automatically:

> China network note: `git clone` needs a proxy (e.g. Watt Toolkit) because git
> bypasses the Windows system proxy:
> `git -c http.proxy=http://127.0.0.1:26561 -c http.sslVerify=false clone <url>`

```bash
python install.py --detect --json     # detect local clients
python install.py --auto              # write correct MCP config
python install.py --accounts          # remind the user about free API keys
python -m webtools doctor --json      # verify
```

> Note: the installer pins the **current Python executable** into the MCP config.
> If you move/recreate the Python environment (or delete the venv), re-run
> `python install.py --auto` to refresh the path.

### Manual install

```bash
pip install -e .                      # or pipx install .
python install.py --client zcode      # or claude-desktop / cursor / claude-code
```

Optional browser tier (for Cloudflare-protected pages):

```bash
pip install -e ".[browser]"
python -m camoufox fetch
```

### Usage

#### MCP tools (`python -m webtools.mcp_server`)

| Tool | Description |
|---|---|
| `web_search(query, limit?, all?, reddit?, sites?)` | Multi-source search (`sites` = mod-site scoped) |
| `web_research(query, top?, limit?, reddit?)` | Search + fetch top pages, return digest |
| `web_fetch(url, browser?)` | Tiered page fetch |
| `channel_status()` | Provider health ledger |
| `quota_status()` | Monthly quota ledger |

#### CLI

```bash
webtools search "query" --limit 5
webtools search "skyui" --sites nexus:skyrimspecialedition
webtools research "question" --top 4
webtools fetch "<url>" [--browser] [--no-cache]
webtools visit "<url>" [--act click --text "Sign in"]
webtools status
webtools doctor --json
```

### Configuration

- Keys: `~/.webtools/keys/{tavily,firecrawl,nexus}.key` (one plain-text line each)
- Or env vars: `TAVILY_API_KEY` / `FIRECRAWL_API_KEY` / `NEXUS_API_KEY`
- All optional — missing keys simply skip that channel
- Custom home: `WEBTOOLS_HOME=/path/to/dir`

Free tiers:
- Tavily: https://app.tavily.com/home (1000 credits/month)
- Firecrawl: https://firecrawl.dev (1000 credits/month)
- Nexus personal API key: https://www.nexusmods.com/users/myaccount?tab=api

### Repository layout

```
src/webtools/
├─ search.py        # multi-source search + quota ledger + mod-site search
├─ fetch.py         # tiered fetching (tier 0..3)
├─ research.py      # health ledger + search-then-fetch digest
├─ browser/         # Camoufox service (port 9377) + client
├─ mcp_server.py    # MCP stdio server
├─ cli.py           # webtools command line
└─ doctor.py        # environment self-check
adapters/           # per-client config templates
install.py          # client detection & config writer
```

### License

MIT

---

## 中文

给 AI agent 用的**免费额度网页搜索/抓取工具链**，专为**被墙 / DNS 污染 / 验证码**环境设计。
一套内核，三种形态：**MCP 服务器**（推荐）、**CLI**、**技能文档**。

> **定位——兜底优先。** 永远先让 agent 用**自带的 WebSearch / WebFetch**；
> webtools 是**第二梯队**：自带工具失败（站点被墙、额度耗尽、超时、验证码拦截）时接管，
> 同时也是没有内置工具的 agent 的独立工具箱。

### 特性

- **多源并行搜索**：tavily / firecrawl（各 1000/月免费额度，**按月度额度账本顺序使用、绝不并行**）
  + github（免费 API）+ reddit（arctic-shift 免费存档）
- **分层抓取**：API 直连 → curl_cffi（3 种 TLS 指纹轮换）→ **Camoufox 真浏览器**（过 Cloudflare）
  → Firecrawl 中转（仅最后兜底）
- **模组站内搜索**：Nexus（走浏览器过 CF）/ mod.io / GitHub，域名级过滤，不走中转
- **额度账本**：记录每月用量，tavily 耗尽自动切 firecrawl，每月 1 日自动重置
- **渠道健康**：瞬时失败不判死，3 次才隔离，冷却指数退避
- **代理感知**：自动识别系统代理（含 Watt Toolkit 这类 MITM 代理），加速域名代理优先、
  其余直连优先；死代理自动跳过
- **零付费 API**：只用免费额度渠道，缺 key 自动跳过

### 安装（AI 自动适配）

下载本仓库后，把它交给你的 AI agent，说"安装"即可——仓库根目录的 `AGENTS.md` / `CLAUDE.md`
会指导 AI 自动执行：

> 国内网络提示：`git clone` 需要代理（如 Watt Toolkit），因为 git 不走 Windows 系统代理：
> `git -c http.proxy=http://127.0.0.1:26561 -c http.sslVerify=false clone <url>`

```bash
python install.py --detect --json     # 探测本机客户端
python install.py --auto              # 自动写入正确的 MCP 配置
python install.py --accounts          # 提醒用户注册免费 API 账号
python -m webtools doctor --json      # 验证
```

> 注意：安装器会把**当前 Python 解释器的绝对路径**写进 MCP 配置。
> 如果 Python 环境变了（换 venv、重装 Python、删掉 venv），需要重跑
> `python install.py --auto` 刷新路径。

### 手动安装

```bash
pip install -e .                      # 或 pipx install .
python install.py --client zcode      # 或 claude-desktop / cursor / claude-code
```

可选浏览器能力（抓 Cloudflare 页面）：

```bash
pip install -e ".[browser]"
python -m camoufox fetch
```

### 使用

#### MCP 工具（`python -m webtools.mcp_server`）

| 工具 | 说明 |
|---|---|
| `web_search(query, limit?, all?, reddit?, sites?)` | 多源搜索（`sites` 支持模组站内搜索） |
| `web_research(query, top?, limit?, reddit?)` | 搜索 + 抓取前 N 页，返回摘要 |
| `web_fetch(url, browser?)` | 分层抓取页面正文 |
| `channel_status()` | 渠道健康台账 |
| `quota_status()` | 月度额度账本 |

#### 命令行

```bash
webtools search "查询词" --limit 5
webtools search "skyui" --sites nexus:skyrimspecialedition
webtools research "问题" --top 4
webtools fetch "<url>" [--browser] [--no-cache]
webtools visit "<url>" [--act click --text "登录"]
webtools status
webtools doctor --json
```

### 配置

- 密钥目录：`~/.webtools/keys/`（`tavily.key` / `firecrawl.key` / `nexus.key`，各一行纯文本）
- 或环境变量：`TAVILY_API_KEY` / `FIRECRAWL_API_KEY` / `NEXUS_API_KEY`
- 全部可选，缺哪个就跳过哪个渠道
- 自定义目录：`WEBTOOLS_HOME=/path/to/dir`

免费额度申请：
- Tavily：https://app.tavily.com/home （1000 credits/月）
- Firecrawl：https://firecrawl.dev （1000 credits/月）
- Nexus 个人 API key：https://www.nexusmods.com/users/myaccount?tab=api

### 目录结构

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

### License

MIT