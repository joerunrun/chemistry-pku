# 服务器部署

## 本地目录约定

外层 `chemistry/` 是开发和测试目录，`chemistry/code/` 只是准备上传服务器的部署副本。所有代码先在外层修改并验证，再运行：

```powershell
cd D:\chemistry\chemistry_agent\chemistry
powershell -NoProfile -ExecutionPolicy Bypass -File .\sync_to_code.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\sync_to_code.ps1 -Apply
```

第一条命令只预览，第二条命令才复制。脚本不删除部署目录中的文件，并排除 `.env`、`runtime_assets/`、缓存与日志。服务器已有的 `.env` 和 `runtime_assets/` 必须独立保留。

同步完成后，不要用资源管理器直接压缩整个 `code/`。使用安全打包脚本生成不含 `.env` 和 `runtime_assets` 的部署包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_code_zip.ps1
```

脚本默认生成带时间戳的 `code_deploy_*.zip`，并在完成后再次检查压缩包中不存在受保护数据。这样第一次把新版本上传到服务器时，压缩包本身就不会携带本地运行数据。

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

## 数据存储与更新安全

上传数据在服务器项目目录的 `runtime_assets/`，Docker 内对应 `/app/runtime_assets/`：

- 当前服务器宿主机路径：`/projects/chemistry-glasses/runtime_assets/`
- 当前服务器项目根目录：`/projects/chemistry-glasses/`
- 容器内路径：`/app/runtime_assets/`

- 教师录音和录像：`runtime_assets/teacher_records/`
- 教师照片：`runtime_assets/teacher_photos/`
- 学生录音和录像：`runtime_assets/user_records/`
- 学生照片：`runtime_assets/user_photos/`
- 转写/描述任务：`runtime_assets/media_analysis_jobs/`
- 设备映射：`runtime_assets/device_mappings.json`
- 第三层报告：`runtime_assets/derived/`
- 每日课程报告：`runtime_assets/derived/daily_courses/`
- 后续实验目录与参考资料：`runtime_assets/experiment_catalog/`

每条记录使用日期、时间和随机 ID 建立独立目录，正常上传不会覆盖旧文件。`docker compose up -d --build` 只重建代码容器，绑定的 `runtime_assets/` 不会被更新覆盖；不要删除该目录，也不要使用带清空数据的部署脚本。

普通 `unzip -o` 会合并同名目录：压缩包中没有的服务器文件通常会保留，但同相对路径文件会被覆盖。尤其不能让本地版本覆盖：

- `runtime_assets/auth/identity.sqlite3`：门户身份、角色、眼镜校准、数据认领关系
- `runtime_assets/device_mappings.json`：教师设备显示编号
- `.env`：服务器密钥与部署配置

### 使用安全解压脚本更新

服务器更新代码统一使用 `deploy/safe_extract.py`，不要再直接执行 `unzip -o code.zip`。第一次启用本机制时，先把本地 [code/deploy/safe_extract.py] 单独上传到服务器 `/projects/chemistry-glasses/deploy/safe_extract.py`；此后它会随安全代码包正常更新：

```bash
cd /projects/chemistry-glasses
python3 deploy/safe_extract.py code.zip --target . --dry-run
python3 deploy/safe_extract.py code.zip --target .
docker compose up -d --build
```

脚本具备以下保护：

- 无条件跳过压缩包中的整个 `runtime_assets/` 和 `.env`
- 不删除服务器中压缩包没有的任何文件
- 更新前备份 `.env`、`identity.sqlite3`、`device_mappings.json`
- 默认备份到 `/projects/chemistry-glasses_deploy_backups/<UTC时间>/`
- 拒绝路径穿越和符号链接等不安全压缩包成员
- 同时兼容压缩包直接包含项目文件，或外层多一层 `code/` 的结构

### 可选：把运行数据移出代码目录

最强的物理隔离方式是把数据放到项目目录外。`docker-compose.yml` 已支持在 `.env` 中设置：

```dotenv
CHEM_RUNTIME_ASSETS_HOST_DIR=/projects/chemistry-data/runtime_assets
```

迁移必须先备份并停止容器，确认目标目录不存在后再移动：

```bash
cd /projects/chemistry-glasses
docker compose down
tar -czf ../runtime_assets_before_move.tar.gz runtime_assets
mkdir -p /projects/chemistry-data
test ! -e /projects/chemistry-data/runtime_assets
mv /projects/chemistry-glasses/runtime_assets /projects/chemistry-data/runtime_assets
# 然后手动在 .env 添加 CHEM_RUNTIME_ASSETS_HOST_DIR
docker compose up -d --build
```

完成后，即使项目目录整体重新解压，实际运行数据也位于 `/projects/chemistry-data/runtime_assets`，不会进入代码覆盖范围。暂时不迁移也没关系，安全解压脚本已经能够保护当前目录结构。

更新前备份：

```bash
tar -czf runtime_assets_$(date +%Y%m%d_%H%M%S).tar.gz runtime_assets
cp .env .env.backup
```

如果使用 `code.zip`，通过上面的安全解压脚本部署。脚本会自动处理压缩包是否多一层 `code/`，并强制保护 `runtime_assets/` 和 `.env`。

更新后检查：

```bash
docker compose ps
curl http://127.0.0.1/api/glasses/health
find runtime_assets/teacher_records runtime_assets/teacher_photos -type f | tail
docker compose logs --tail=100 chemistry-api
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
