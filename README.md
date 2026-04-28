# Auto Team to CPA

面向 ChatGPT Team / Codex 使用场景的多母号、批量子号和 CPA 认证文件同步管理工具。

本项目用于把多个 ChatGPT Team 管理员账号作为“母号”统一管理，通过 Freemail 自动收码创建和接入子号，完成 Codex OAuth 授权后，将可用认证文件同步到 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)。

> 重要声明：本项目仅供学习、研究和自用自动化管理场景参考。使用自动化注册、登录、邀请、账号池和认证文件同步可能违反相关服务条款，也可能触发风控、封号、IP 限制或数据损失。请自行评估风险并承担后果。

## 致谢原项目

本仓库基于并致敬 [cnitlrt/AutoTeam](https://github.com/cnitlrt/AutoTeam)。

原项目提供了 ChatGPT Team 账号轮转、Codex OAuth、额度检测、Web 面板、CPA/Sub2API 同步等核心思路和基础实现。本仓库在此基础上更聚焦于：

- 多母号批量管理
- 每个母号下的子号补齐和状态追踪
- 子号 Codex auth 自动补传到 CPA
- 疑似封禁、401、卡住账号等场景的巡检和修复
- 更适合 CPA 使用链路的控制台操作

如果这个项目对你有帮助，也请去原项目点 Star 支持作者。

## 主要功能

| 功能 | 说明 |
|------|------|
| 多母号管理 | 支持添加、批量导入、启用/停用、登录多个 ChatGPT Team 管理员账号 |
| 子号批量创建 | 遍历启用母号，通过 Freemail 自动创建临时邮箱并完成注册/加入流程 |
| Team 状态对账 | 记录本地子号、远端 Team 成员、待邀请、已移出、疑似封禁等状态 |
| Codex OAuth | 为子号或主号生成 Codex 认证文件，保存为 CPA 兼容格式 |
| CPA 同步 | 将已授权的子号 auth 补传到 CLIProxyAPI，也支持主号 Codex auth 同步 |
| 健康检测 | 检测子号额度、401、认证失效、疑似封禁和卡住阶段 |
| 自动修复 | 支持修复卡住账号、修复母子关联、清理待邀请、移出疑似封禁成员 |
| Web 控制台 | 提供总览、母号管理、子号记录、设置、日志和后台任务状态 |
| Docker 部署 | 支持容器运行，并通过 `data/` 持久化配置和认证文件 |

## 适用场景

- 你已经有 ChatGPT Team 订阅，并希望批量维护多个 Team 母号。
- 你需要把 Codex 认证文件统一同步到 CLIProxyAPI。
- 你需要定期检查子号状态，发现 401、额度耗尽或疑似封禁后再补位。
- 你希望通过 Web 面板完成母号登录、批量补号、CPA 补传和日志排查。

## 环境要求

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Playwright Chromium
- ChatGPT Team 管理员账号
- Freemail API 服务和 Root Token
- CLIProxyAPI 服务和管理密钥

Windows、Linux、macOS 均可运行。Linux 无图形环境时建议使用 Docker，或确保 Playwright 运行环境完整。

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/1134189025/auto-team-to-cpa.git
cd auto-team-to-cpa
```

### 2. 安装依赖

```bash
uv sync
uv run playwright install chromium
```

Windows 也可以直接运行：

```powershell
.\启动脚本.bat
```

脚本会自动检查依赖、构建前端并启动后端服务。

### 3. 配置 `.env`

复制配置模板：

```bash
cp .env.example .env
```

然后填写：

```dotenv
FREEMAIL_BASE_URL=https://freemail.example.com
FREEMAIL_ROOT_TOKEN=your_root_token

CPA_URL=http://127.0.0.1:8317
CPA_KEY=your_key

API_KEY=your_panel_api_key

PLAYWRIGHT_PROXY_URL=
PLAYWRIGHT_PROXY_BYPASS=
EMAIL_POLL_INTERVAL=3
EMAIL_POLL_TIMEOUT=300
```

也可以先启动 Web 服务，首次访问时在页面里完成配置。

### 4. 启动 Web 控制台

```bash
uv run autoteam api
```

默认访问：

```text
http://localhost:8787
```

如果设置了 `API_KEY`，进入控制台时需要输入该密钥。

## Docker 部署

```bash
git clone https://github.com/1134189025/auto-team-to-cpa.git
cd auto-team-to-cpa
mkdir -p data
cp .env.example data/.env
docker compose up -d
```

容器会将运行数据写入 `data/`。请在 `data/.env` 中填写 Freemail、CPA 和 API Key 配置。

## Web 控制台

| 页面 | 用途 |
|------|------|
| 总览 | 查看启用母号、已完成子号、疑似封禁、额度耗尽、待补传 CPA 等统计 |
| 母号管理 | 添加/批量导入母号、登录母号、选择 workspace、主号传 CPA |
| 子号记录 | 查看子号邮箱、所属母号、远端状态、健康状态、Auth 和 CPA 上传情况 |
| 设置 | 修改 Freemail、CPA、代理、API Key 等配置 |
| 日志 | 实时查看后端运行日志，便于排查注册、登录、授权和同步问题 |

常用任务按钮包括：

- 一键批量创建子号
- 一键补满全部母号
- 检测封禁子号
- 一键删除 401 账号
- 修复母子关联
- 修复卡住账号
- 清理待邀请
- 补传子号 CPA

## CLI 命令

```bash
uv run autoteam api
uv run autoteam status
uv run autoteam batch-run
uv run autoteam fill-all --target-per-parent 5
uv run autoteam repair-stuck-accounts
uv run autoteam cpa-resync
```

母号管理：

```bash
uv run autoteam parent list
uv run autoteam parent add --email owner@example.com --label "Team A" --default-batch-size 3
uv run autoteam parent login <parent_id>
uv run autoteam parent import-session <parent_id> --email owner@example.com --session-token <token>
uv run autoteam parent enable <parent_id>
uv run autoteam parent disable <parent_id>
uv run autoteam parent remove <parent_id>
```

## 数据文件

| 路径 | 说明 |
|------|------|
| `.env` | 本地配置，包含 Freemail、CPA、API Key 等敏感信息 |
| `auths/` | Codex 认证文件 |
| `accounts.json` | 子号状态和本地账号池 |
| `main_accounts.json` | 母号状态、登录态引用和默认批量配置 |
| `freemail_state.json` | Freemail 域名轮询状态 |
| `autoteam-api*.log` | 后端运行日志 |

这些文件默认不提交到 Git。请不要把真实 token、账号数据、auth 文件或日志上传到公开仓库。

## 开发

后端：

```bash
uv sync
uv run pytest
uv run ruff check
```

前端：

```bash
cd web
npm install
npm run dev
npm run build
```

前端构建产物会输出到：

```text
src/autoteam/web/dist
```

## 项目结构

```text
auto-team-to-cpa/
├── src/autoteam/
│   ├── api.py            # FastAPI 服务、Web 面板接口、后台任务
│   ├── manager.py        # CLI 入口和批量执行逻辑
│   ├── parents.py        # 母号数据持久化
│   ├── accounts.py       # 子号数据持久化
│   ├── freemail.py       # Freemail API 客户端
│   ├── chatgpt_api.py    # ChatGPT Team 相关浏览器/API 操作
│   ├── codex_auth.py     # Codex OAuth 和 auth 文件生成
│   ├── cpa_sync.py       # CLIProxyAPI 上传和补传
│   ├── health.py         # 子号健康检测
│   └── web/dist/         # 已构建的 Web 前端
├── web/src/              # Vue 3 前端源码
├── tests/                # 单元测试
├── docs/                 # 设计、配置和排查文档
└── tools/                # 辅助工具
```

## 风险和限制

- OpenAI、ChatGPT、Codex、Cloudflare 的风控策略可能变化，自动化流程不保证长期稳定。
- 同一时间大量登录、注册、授权或邀请可能触发风控。
- VPS IP、代理质量、邮箱域名信誉会明显影响成功率。
- 401、额度耗尽、疑似封禁等状态需要结合 Web 面板人工判断，不建议完全无人值守。
- 本项目不会替你保证任何账号、订阅、CPA 服务或认证文件的可用性。

## License

本项目沿用 MIT License。原项目版权和贡献请参考 [cnitlrt/AutoTeam](https://github.com/cnitlrt/AutoTeam)。
