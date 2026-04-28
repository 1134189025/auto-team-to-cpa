# Clash Rotator

Windows Clash 节点定时切换脚本。它不嵌入 AutoTeam，只通过 Clash External Controller API 切换当前代理组。

## 使用

1. 确认 Clash 已开启 External Controller：`127.0.0.1:9097`，Secret 为 `123123`。
2. 双击 `start.bat` 开始定时切换。
3. 按 `Ctrl+C` 或关闭窗口停止。

## 常用操作

- `start.bat`：循环定时切换。
- `switch_once.bat`：只切换一次。
- `list_groups.bat`：列出可切换代理组和过滤后的节点数量。

## 配置

编辑 `config.json`：

- `controller_url`：Clash 控制器地址，默认 `http://127.0.0.1:9097`。
- `secret`：控制器密钥，默认 `123123`。
- `proxy_group`：代理组名。留空会优先选择非 `GLOBAL` 的 `Selector` 组；如果选错，先运行 `list_groups.bat`，再把正确组名填进来。
- `interval_seconds`：切换间隔秒数，默认 300 秒。
- `random`：`true` 为随机切换，`false` 为顺序切换。
- `include_keywords`：只使用包含这些关键词的节点，空数组表示不过滤。
- `exclude_keywords`：排除包含这些关键词的节点。

AutoTeam 不需要改注册流程。只要浏览器流量走 Clash，例如 `PLAYWRIGHT_PROXY_URL=http://127.0.0.1:7890`，TUN 模式下也会跟随 Clash 当前节点变化。
