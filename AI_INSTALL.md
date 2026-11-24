# AI 安装说明（兜底入口）

如果本仓库的 `AGENTS.md` / `CLAUDE.md` 没有被你的客户端自动加载，请先按本文件执行。

## 你是 AI，被要求安装 webtools。按序执行：

1. 运行：
   ```bash
   python install.py --detect --json
   ```
   得到本机已安装的客户端列表（claude-desktop / cursor / zcode / claude-code / cherry-studio 等）。

2. 对每个 `"detected": true` 的客户端运行：
   ```bash
   python install.py --client <名称>
   ```

3. 如果脚本报 `unknown client` 或该客户端不在支持列表：
   - 读 `webtools.manifest.json` 了解入口与配置结构
   - 读 `adapters/` 下对应模板，手工把 MCP 服务器条目写入该客户端配置

4. 验证：
   ```bash
   python -m webtools doctor --json
   ```
   确认 `mcp_registered.<客户端> == true`、`checks.curl_cffi == true`。

5. 向用户报告：安装到哪些客户端、是否需要重启、缺少哪些密钥。

## 决策树

- 客户端支持 MCP → 用 `install.py`（推荐，脚本写入正确 JSON）
- 客户端只支持 shell → `pip install webtools`，并把 `SKILL.md` 放入其技能目录
- 未知客户端 → `adapters/` 手工适配

## 依赖

- Python ≥ 3.10
- `curl_cffi`（必需）
- `camoufox`（可选，浏览器兜底；`pip install webtools[browser]` 后 `python -m camoufox fetch`）