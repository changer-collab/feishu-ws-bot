# 飞书本地长连接群消息机器人（Python）

这个项目用于在本地通过飞书长连接 WebSocket 获取群消息事件，并在收到群消息时提取 `chat_id`，可选查询群详情。后续可以直接以环境变量方式部署到华为云 ECS / CCE / FunctionGraph 自定义容器。

## 1. 飞书开放平台配置

1. 创建企业自建应用，开启“机器人”能力。
2. 在“事件与回调”中选择“使用长连接接收事件”。
3. 订阅事件：`im.message.receive_v1`（接收消息 v1）。
4. 权限建议按需申请并发布应用：
   - 接收消息相关权限，如 `im:message`。
   - 如果要查询群详情，需要 IM 群信息读取相关权限；具体以开放平台权限申请页为准。
5. 将机器人拉进目标群，或确保机器人能接收该群消息。

## 2. 本地运行

```bash
cd feishu_ws_bot
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env   # macOS / Linux
```

编辑 `.env`：

```env
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
LOG_LEVEL=INFO
FETCH_CHAT_INFO=true
ENV=local
```

启动：

```bash
python main.py
```

连接成功后，控制台会打印类似 `connected to wss://...`。在飞书群里发消息后，日志会输出 `chat_id`、消息内容、发送人信息；如果 `FETCH_CHAT_INFO=true`，还会查询并打印群信息。

## 3. 目录结构

```text
feishu_ws_bot/
├── main.py
├── requirements.txt
├── .env.example
├── feishu_bot/
│   ├── __init__.py
│   ├── config.py
│   └── handlers.py
└── deploy/
    └── huaweicloud/
        ├── Dockerfile
        ├── docker-compose.yml
        └── huaweicloud_run.md
```

## 4. 常见问题

### 看不到长连接选项或保存失败

先本地运行 `python main.py`，看到连接成功后，再回到飞书开放平台配置长连接并保存。应用通常也需要发布/启用对应权限。

### 收不到群消息

检查：机器人是否已加入群；是否订阅 `im.message.receive_v1`；应用权限是否已发布；群里消息是否会 @ 机器人取决于你的机器人配置和飞书事件策略。

### 查询群信息失败

日志里会输出飞书返回的 `code / msg / log_id`。通常是权限未申请、权限未发布、机器人不在群、或 `chat_id` 类型/可见性问题。


## 自动下载群里的 PDF

保持机器人长连接运行后，在机器人可见的群聊或单聊里发送 PDF 文件消息，程序会自动读取消息里的 `message_id` 和 `file_key`，调用「获取消息中的资源文件」接口下载到 `DOWNLOAD_DIR`。

`.env` 中可配置：

```env
DOWNLOAD_PDF=true
DOWNLOAD_DIR=downloads
```

如果部署到华为云容器，建议把 `DOWNLOAD_DIR` 设置为挂载卷路径，例如：

```env
DOWNLOAD_DIR=/app/downloads
```

需要的权限通常包括：

- `im:message` / `im:message:readonly`
- `im:resource`
- 如果读取群消息，还需要群消息相关权限，并确保机器人已经被添加到该群。

## 5. QQ 群 PDF 捕获模式

当 `.env` 中 `QQ_ENABLE=true` 时，程序进入 QQ/OneBot 捕获模式（`feishu_bot.onebot` 模块），飞书侧不再启动：程序作为反向 WebSocket 服务端（默认 `0.0.0.0:8081`），等待 NapCat 等 OneBot 11 协议端连接，被动接收群文件事件与轮询群文件列表，下载 PDF 并推送后端。

关键配置项（见 `.env.example`）：

```env
QQ_ENABLE=true
ONEBOT_WS_HOST=0.0.0.0
ONEBOT_WS_PORT=8081
QQ_GROUP_ID=目标QQ群号            # 必填
QQ_HISTORY_DAYS=7               # 启动时补拉最近 N 天群文件
QQ_FILE_POLL_INTERVAL=900       # 群文件轮询间隔（秒）
QQ_STATE_PATH=qq_processed_ids.json   # 去重状态文件（Docker 建议挂载持久卷）
DOWNLOAD_DIR=downloads          # 下载文件目录
```

- NapCat 登录**必须使用小号**，存在协议号封号风险；机器人仅被动接收/下载，不主动发消息。
- Docker 一键部署（NapCat + 机器人）与本地 Windows 运行步骤见 [deploy/qq/README.md](deploy/qq/README.md)。
- 回滚：`QQ_ENABLE=false` 即恢复纯飞书模式，onebot 模块不影响现有流程。
