# 产品介绍：Hermes Bots Manager

## 一句话定位

Hermes Bots Manager 是给内部团队使用的飞书 Bot 管控台，把 Hermes Agent 的命令行能力变成可视化的 Bot 创建、配置和运维流程。

## 解决的问题

运营或业务 PM 想在飞书里接入一个 AI 机器人时，通常要跨过这些步骤：创建飞书应用、配置 App ID / Secret、维护 Hermes Profile、启动 Gateway、处理 pairing code、设置 Workspace、开关 Skills、检查日志。任何一步走错，群里 @ 机器人就无法回复。

这个项目把这些步骤收进一个向导和几个运维页面里，目标是让“新建 Bot 到飞书群可用”成为 3 分钟内可完成的标准动作。

## 目标用户

- 运营 / 业务 PM：创建和管理飞书机器人，处理首次 pairing，查看日志。
- AI 实施同事：配置 Workspace、Skills、模型和运行环境。
- 管理员：管理本地账号、查看审计日志、处理异常 Bot。

## 核心流程

1. 新建 Bot，填写名称。
2. 管控台自动调用飞书创建命令，展示二维码。
3. 用户扫码进入飞书开放平台完成操作。
4. 用户回填 App ID / Secret。
5. 设置 Workspace。
6. 启动 Gateway。
7. 飞书内发送第一条消息，管控台捕获 pairing code。
8. 在审批中心批准后，Bot 开始识别该用户并回复。

## 主要模块

- Bot 列表：展示状态、模型、Skills 数量、飞书应用和快捷入口。
- 创建向导：承接飞书扫码创建、凭证填写、Workspace 设置和完成页。
- Gateway 日志：查看实时日志、连接状态、下载日志。
- Pairing 审批：集中处理新用户首次识别申请，避免只靠命令行审批。
- Workspace：配置每个 Bot 的工作目录，支持目录库复用。
- Skills：查看、启停和上传技能，区分危险技能和遮蔽状态。
- 模型配置：支持 ChatGPT Auth 订阅和常规模型 provider 配置。
- 审计日志：记录关键管理动作，满足 MVP 级别追踪需求。

## 技术方案

- 后端：FastAPI、SQLAlchemy、Alembic、SQLite、PyJWT、Fernet 加密。
- 前端：React、TypeScript、Ant Design、Vite、TanStack Query。
- 集成：通过 Hermes CLI 和 Hermes Profile 文件系统对接。
- 实时：FastAPI WebSocket 推送 Gateway 日志和 pairing 事件。

## 当前边界

这是最小可用版本，优先保证内部单机可用：

- 不做 SaaS 多租户。
- 不做复杂审批流。
- 不托管用户 ChatGPT 登录态。
- 不替代飞书开放平台权限配置和审核。
- 不支持多 uvicorn worker；MVP 使用单进程运行。

## 发布原则

仓库只包含源码、测试、文档和示例配置。真实运行数据和敏感信息不进入 Git：

- 不包含 SQLite 运行库。
- 不包含 `.env`。
- 不包含 Hermes Profile。
- 不包含飞书 App Secret。
- 不包含 GPT/OpenAI/ChatGPT 个人配置。
