# Hermes Bots Manager

> Launch and operate Hermes Feishu/Lark AI bots from a web console, without touching the CLI.

Hermes Bots Manager 是一个面向内部运营和业务 PM 的飞书 Bot 管控台。它把 Hermes Agent 的多 Profile / 多 Bot 能力包装成 Web 操作界面，让非工程同事不碰命令行也能完成 Bot 创建、飞书接入、Gateway 运维、pairing 审批、Workspace 和 Skills 配置。

核心目标：在表单里粘贴飞书 App ID / Secret，3 分钟内让飞书群里的机器人可以被 @ 并回复。

## 宣传语

- 表单里粘 App ID / Secret，飞书群里的 AI Bot 3 分钟上线。
- 多 Bot、多 Profile、多 Skill，一站式管好 Hermes 机器人矩阵。
- 给运营和 PM 用的 Hermes 飞书 Bot 控制台。
- 从配置、配对到日志排障，把飞书 Bot 运维搬进浏览器。

## 当前能力

- Bot 管理：创建、克隆、重命名、归档删除 Hermes Profile。
- 飞书接入向导：输入 Bot 名称，自动调用飞书开放平台创建命令，展示二维码，扫码后回填 App ID / Secret。
- Gateway 运维：启动、停止、重启、状态检查、实时日志、日志下载。
- Pairing 审批：自动捕获 Hermes pairing code，在审批中心批准或拒绝首次用户配对。
- Workspace 管理：配置每个 Bot 的 `terminal.cwd`，支持手动路径、工作目录库和复用其他 Bot。
- Skills 管理：查看可用 Skills，启用/禁用，标记危险技能和被遮蔽技能。
- 模型配置：支持写入 ChatGPT Auth 订阅配置，也支持常规 provider / model / base_url 配置。
- 基础账号权限：本地 Owner / Admin / Editor / Viewer，JWT 登录，关键操作审计。

## 架构

```text
frontend/  React + TypeScript + Ant Design + Vite
backend/   FastAPI + SQLAlchemy + Alembic + SQLite
Hermes     通过 hermes CLI 和 ~/.hermes profiles 对接
Realtime   FastAPI WebSocket 推送 Gateway 日志和 pairing 事件
```

MVP 默认单机部署，后端只使用一个 uvicorn worker。SQLite 和内存队列都假设单进程运行。

## 运行前提

- Python 3.12+
- Node.js 20.19+
- uv
- pnpm
- 已安装并可执行的 Hermes CLI
- 如需自动创建飞书 Bot，需要本机已配置可用的飞书开放平台 CLI / 登录态

## 本地启动

```bash
./start.sh
```

启动脚本会在首次运行时复制 `backend/.env.example`，按需安装依赖，执行数据库迁移，并同时启动前后端服务。

也可以手动执行：

```bash
make install
cp backend/.env.example backend/.env
cd backend && uv run alembic upgrade head
cd ..
make dev
```

服务地址：

- 前端：http://localhost:5710
- 后端：http://localhost:8710
- API 文档：http://localhost:8710/docs

首次打开登录页时勾选“首次安装”，创建第一个 Owner 账号。项目没有内置默认账号密码。

## 常用命令

```bash
./start.sh        # 本地一键启动
make lint      # backend ruff/mypy + frontend eslint
make test      # backend pytest + frontend vitest
make smoke     # 真实 Hermes CLI smoke，用于本机集成验证
make clean     # 删除本地依赖和缓存
```

## 创建一个飞书 Bot

1. 登录管控台，点击“新建 Bot”。
2. 输入 Bot 名称。
3. 向导自动调用飞书 Bot 创建命令并展示二维码。
4. 使用飞书扫码，在开放平台完成授权/创建。
5. 回到向导填写 App ID 和 App Secret。
6. 填写 Workspace，完成创建。
7. 启动 Gateway，在飞书里向 Bot 发送第一条消息。
8. 在 Pairing 审批中心批准 pairing code，Bot 即可识别该用户。

## 配置和敏感信息

发布仓库只包含示例配置，不包含本机数据库、Hermes Profile、飞书 Secret、OpenAI/GPT 配置或 ChatGPT 登录态。

不要提交这些文件：

- `backend/.env`
- `frontend/.env.*`
- `backend/hermes_console.db`
- `~/.hermes/**`
- `~/.hermes-console/**`
- 任何真实 `FEISHU_APP_SECRET`、API Key、JWT secret 或模型服务密钥

飞书 App Secret 会加密落库，同时以 Hermes 需要的格式写入本机 Profile `.env`。这些运行时文件必须只留在部署机器上。

## 产品介绍

更完整的产品定位、用户路径和边界见 [docs/PRODUCT.md](docs/PRODUCT.md)。

## MVP 边界

- 面向单机内部部署，不是 SaaS 多租户平台。
- 不自动托管 ChatGPT 登录态，只写入 Hermes 可读取的模型配置。
- 不替代飞书开放平台权限审核；用户仍需要在飞书侧完成扫码和授权。
- 审计和安全能力保持 MVP 级别，优先保障“创建 Bot 到飞书可用”的主链路。
