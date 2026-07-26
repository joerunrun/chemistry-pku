# 服务器部署

## 前置条件

- Linux 服务器、Docker、Docker Compose
- 代码目录、`.env`、`runtime_assets/`
- 对外开放 TCP 80

## 首次部署

```bash
git clone <仓库地址> chemistry
cd chemistry
cp env.example .env
# 编辑 .env，填写 QINIU_API_KEY、DASHSCOPE_API_KEY 等配置
mkdir -p runtime_assets
docker compose up -d --build
```

检查服务：

```bash
docker compose ps
curl http://127.0.0.1/api/glasses/health
```

## 更新、日志和停止

```bash
git pull
docker compose up -d --build
docker compose logs -f chemistry-api
docker compose down
```

## 地址

| 用途 | 地址 |
|---|---|
| 眼镜健康检查 | `http://162.105.209.134/api/glasses/health` |
| 学生管理页 | `http://162.105.209.134/admin` |
| 教师管理页 | `http://162.105.209.134/teacher-admin` |

## 数据目录

`runtime_assets/` 必须持久化，主要包含：

- `demo_videos/`：演示视频
- `user_records/`、`teacher_records/`：实验录音和 MP4
- `user_photos/`、`teacher_photos/`：照片
- `reference_images/`、`cv_detection2_data/`、`safety_check_data/`：分析资源

备份时同时备份 `.env` 和 `runtime_assets/`。服务器只改代码或配置时，统一执行 `docker compose up -d --build`。
