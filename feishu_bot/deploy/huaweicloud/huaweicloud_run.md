# 华为云部署预留说明

## 方案 A：ECS + Docker Compose

1. 在华为云 ECS 安装 Docker 和 Docker Compose。
2. 上传项目目录或 ZIP 解压到服务器。
3. 在项目根目录创建 `.env`，写入：

```env
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
LOG_LEVEL=INFO
FETCH_CHAT_INFO=true
```

4. 启动：

```bash
docker compose -f deploy/huaweicloud/docker-compose.yml up -d --build
```

5. 查看日志：

```bash
docker compose -f deploy/huaweicloud/docker-compose.yml logs -f
```

## 方案 B：CCE / 容器镜像服务

- 使用 `deploy/huaweicloud/Dockerfile` 构建镜像。
- 将 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`LOG_LEVEL`、`FETCH_CHAT_INFO` 配置为环境变量或 Secret。
- 容器无需暴露公网端口，只要能访问公网即可建立飞书 WebSocket 长连接。

## 安全建议

不要把 App Secret 写进代码仓库。生产环境请使用华为云 CCE Secret、ECS 环境变量、或密钥管理服务托管。
