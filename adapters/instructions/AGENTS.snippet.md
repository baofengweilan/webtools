# 可选：把以下片段追加到你的全局 AGENTS.md / CLAUDE.md

## iframe 网页访问（webtools 兜底链）

当内置 WebFetch / WebSearch 失败时（不可达 / 配额 / 超时 / 验证码），按顺序降级：

| 场景 | 命令 |
|---|---|
| 搜索不可达 | `webtools search "查询词" --limit 5` |
| 页面抓取失败（403 / JS 壳 / SSL） | `webtools fetch "<url>"` |
| 被判定机器人 | `webtools fetch "<url>" --browser`（Camoufox，15–90 秒/页） |
| 模组站内搜索 | `webtools search "查询词" --sites nexus:<游戏>,modio:<游戏>` |
| 查看额度/渠道健康 | `webtools status` |
| 环境自检 | `webtools doctor --json` |

规则：
- 抓取降级链：API 直连 → curl_cffi → Camoufox → Firecrawl（最后兜底）
- tavily / firecrawl 按月额度顺序使用，**绝不并行**
- 缺 key 的渠道自动跳过；密钥在 `~/.webtools/keys/`