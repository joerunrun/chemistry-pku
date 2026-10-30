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

脚本固定生成 `code_deploy.zip`，本地已经存在旧包时直接覆盖，并在完成后再次检查压缩包中不存在受保护数据。因此每次上传和部署都使用同一个文件名。

## 前置条件

- Linux 服务器、Docker、Docker Compose
- 代码目录、`.env`、`runtime_assets/`
- 对外开放 TCP 80

## 老师补充的实验纠错材料

学生纠错继续检索外部知识库，同时按现场观察匹配老师材料。规则、关键词和 PDF 页码在 `pipeline/qa/teacher_materials.json`，图片在 `runtime_assets/knowledge_supplements/organic_apparatus/`。每次最多附两张参考图，仍是两次模型调用；过滤、萃取没有对应示例图时只附文字。保留“强制／建议”和院系适用范围，不把老师错误示例当作现场问题。

原 PDF 保存在 `runtime_assets/knowledge_supplements/有机实验-实验装置识别要点.pdf`。本地准备图片及单独的资源包（仅准备材料的电脑需要安装 PyMuPDF，服务器无需安装）：

```powershell
python -m pipeline.tools.prepare_teacher_materials --bundle knowledge_supplements_deploy.zip
```

`code_deploy.zip` 继续排除所有 `runtime_assets/`。这次部署额外上传 `knowledge_supplements_deploy.zip`，它只含六张参考图片（约 0.5 MB），在服务器项目根目录解压：

```bash
cd /projects/chemistry-glasses
python3 -m zipfile -e knowledge_supplements_deploy.zip .
```

然后按原流程安全更新代码、重建 `chemistry-api`。没有数据库迁移，不修改用户记录、绑定或 `.env`；眼镜无需更新 APK。以后收到不同 PDF，先整理规则与图片索引，再运行准备工具，不会自动导入目录内任意文件。

2026-10-19 已部署至 `.134`（代码提交 `73c49cf`）：老师材料的六类规则和六张参考图片已启用。沿用安全解压的 `--no-backup`，仅重建 `chemistry-api`；图片单独写入 `knowledge_supplements/organic_apparatus/`。健康、登录、材料匹配、图片编码及两版 APK 更新接口检查通过。原有 10 条眼镜绑定、705 批归属、1391 条归属明细逐条核对保留，媒体资料未减少，`.env` 未改，其他服务保持运行。APK 仍为 2.3（versionCode=5），无需重装。

## 语音反馈与单一任务（2.4）

教师、学生联网版 2.4（versionCode=6）新增“小北助手，开始反馈／结束反馈”，内置提示音分别为“开始收集反馈／结束收集反馈”。停顿不结束收集。录音、录像、反馈只接受各自的结束指令，进行中单击镜腿不拍照、不切换功能；结束后再使用其他功能。教师版原有持续录音保留，反馈期间在教学录音中留出静音，反馈正文只写入独立反馈录音。

后台独立的“意见反馈”标签展示文字、原始语音、角色、时间、眼镜编号与排班关联情况。上传接口为 `POST /api/glasses/feedback/upload`；每条反馈独立存放在 `runtime_assets/feedback/<反馈ID>/`，包含 `feedback.json`、`feedback_audio.wav` 和异步生成的 `analysis.json`，不进入日常资料和每日总结。原有网页文字反馈继续保留。按实际开始时间匹配使用者，未匹配则保留为未归属，后续排班调整不改写旧反馈归属。

沿用原部署顺序：先更新后端并确认健康接口有 `voice_feedback`，再发布 `release/服务器部署/glasses-update-2.4.zip`。本次无数据库迁移；眼镜需安装 2.4 才能使用语音反馈。

2026-10-19 已部署至 `.134`，教师、学生联网版 2.4（versionCode=6）均已发布。实际语音反馈的上传、原始语音播放、异步转写和重复上传去重验证通过，部署测试反馈已清理；独立反馈页面正常。原有 12 条眼镜排班、705 批归属、1391 条归属明细逐条核对保留，四类原资料未减少，`.env` 未改，不额外备份。只重建化小北 API，其他服务保持运行；本地两份一键更新 ZIP、简明指南及 `code_deploy.zip` 已同步更新。眼镜联网后重开助手，选择“立即更新”，按系统提示安装。

## 修改排班时补归历史资料（2026-10-22，已部署）

学生、教师或管理员保存眼镜编号与固定星期时，会补归同角色、同眼镜、录制或拍摄日为该星期的历史未归属录音、录像和照片。已归属给任何人的资料不迁移；不同角色、眼镜或星期的资料不混用。同一排班再次保存也会重新补归，页面显示本次补归条数。

归属以北京时间的实际录制／拍照时间为准。晚上传的资料优先使用拍摄当时的历史排班；没有历史排班时，再按当前眼镜和星期归属。原排班历史保留，不修改原文件；补归仍使用现有认领表，不需要数据库迁移或更新 APK。上线后，已改过排班的用户再点一次“保存”即可触发补归，不会在部署时批量改写所有资料。

已部署代码 `13aa05c` 至 `.134`，仅重建 `chemistry-api` 并重载网关，未额外备份。健康、登录及容器内代码一致性检查通过；18 条眼镜排班、848 批归属、1534 条归属明细逐条核对保留，四类媒体数量未减少，`.env` 和两版 APK 更新信息未改，其他服务保持运行。已改过排班但仍缺资料的用户重新保存一次即可，无需重装眼镜程序。

## 学生版视频与字号优化（2.6，2026-10-25 已部署）

已部署后端 `8ce8454`，并发布学生版 `2.6-student`（versionCode=10，Android 代码 `a877020`）。相关视频最多 20 条、每页 3 条，滑动选择、单击播放；视频显示区域扩大并保持比例，回答字号为 26sp，滑动翻阅时保持结果。教师版继续使用 2.5.1（versionCode=9）。

沿用安全解压 `--no-backup`，只重建 `chemistry-api` 并重载网关。18 条眼镜排班、849 批归属、1539 条归属明细逐条保留，媒体数量未减少，`.env` 未改。健康、登录与 20 条可播放视频检索检查通过；从公网完整下载学生 APK（12,859,394 字节）并核对 SHA256，通过。未安装、启动或更新当前连接的眼镜，由使用者联网打开学生版、选择“立即更新”自行验证云端下载与安装。

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
- 时间段课程报告：`runtime_assets/derived/scoped_courses/`
- 共性问题报告：`runtime_assets/derived/common_issues/`
- 后续实验目录与参考资料：`runtime_assets/experiment_catalog/`

每条记录使用日期、时间和随机 ID 建立独立目录，正常上传不会覆盖旧文件。`docker compose up -d --build` 只重建代码容器，绑定的 `runtime_assets/` 不会被更新覆盖；不要删除该目录，也不要使用带清空数据的部署脚本。

普通 `unzip -o` 会合并同名目录：压缩包中没有的服务器文件通常会保留，但同相对路径文件会被覆盖。尤其不能让本地版本覆盖：

- `runtime_assets/auth/identity.sqlite3`：门户身份、角色、眼镜校准、数据认领关系
- `runtime_assets/device_mappings.json`：教师设备显示编号
- `.env`：服务器密钥与部署配置

### 使用安全解压脚本更新

服务器更新代码统一使用 `deploy/safe_extract.py`，不要再直接执行 `unzip -o code_deploy.zip`。第一次启用本机制时，先把本地 `code/deploy/safe_extract.py` 单独上传到服务器 `/projects/chemistry-glasses/deploy/safe_extract.py`；此后它会随安全代码包正常更新：

```bash
cd /projects/chemistry-glasses
python3 deploy/safe_extract.py code_deploy.zip --target . --dry-run
python3 deploy/safe_extract.py code_deploy.zip --target . --no-backup
docker compose up -d --build
```

脚本在服务器终端只输出英文，避免终端编码不一致造成中文乱码。

脚本具备以下保护：

- 无条件跳过压缩包中的整个 `runtime_assets/` 和 `.env`
- 不删除服务器中压缩包没有的任何文件
- 更新前备份 `.env`、`identity.sqlite3`、`device_mappings.json`
- 默认备份到 `/projects/chemistry-glasses_deploy_backups/<UTC时间>/`
- 拒绝路径穿越和符号链接等不安全压缩包成员
- 同时兼容压缩包直接包含项目文件，或外层多一层 `code/` 的结构

明确不需要额外备份时，可在实际解压命令加 `--no-backup`；它只跳过备份复制，仍禁止覆盖 `.env` 和 `runtime_assets/`。

当前按不额外备份的约定使用上述 `--no-backup` 命令。2026-10-18 的学生固定星期排班、自动归属、日历双份学习记录及管理员未归属资料分配，也统一通过本地 `code_deploy.zip` 发布；先同步 `code/`，再重新打包，不使用旧包。本地打包不代表服务器已经更新。

2026-10-16 已部署学生自然语音控制、知识库问答／视频搜索／照片纠错，以及助教固定星期绑定和日历双份总结。此次使用 `--no-backup` 更新差异代码，保留原有 Compose、Nginx 和数据挂载配置，仅补充知识库环境变量。归属明细 1391 条、归属批次 705 批均核对一致，8 条原绑定保留，需由助教补选星期。

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

上传新的 `code_deploy.zip` 后，通过上面的安全解压脚本部署。脚本会自动处理压缩包是否多一层 `code/`，并强制保护 `runtime_assets/` 和 `.env`。

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
| 统一登录入口 | `http://162.105.209.134/login` |
| 登录后统一工作台 | `http://162.105.209.134/workspace` |
| 旧实验纠错调试页 | `http://162.105.209.134/pipeline` |
| 眼镜健康检查 | `http://162.105.209.134/api/glasses/health` |

用户只需要访问 `/login`。北大门户登录后，学生、教师自动进入同一个 `/workspace`，页面根据身份显示对应标签；管理员也在登录页右下角弹窗输入密码后进入该工作台。

根路径 `/` 会立即跳转到 `/login`。这也保证北大从已登记的 `/#/iaaa_login?...` 返回时，不会先渲染旧实验纠错页面；旧页面保留在 `/pipeline`，仅供单独调试。

旧的 `/teacher-admin`、`/admin`、`/admin/users` 地址仅作为兼容入口，会自动跳转到统一工作台对应标签，不再作为独立操作页面。

## 数据目录

`runtime_assets/` 必须持久化，主要包含：

- `demo_videos/`：演示视频
- `user_records/`、`teacher_records/`：实验录音和 MP4
- `user_photos/`、`teacher_photos/`：照片
- `reference_images/`、`cv_detection2_data/`、`safety_check_data/`：分析资源

备份时同时备份 `.env` 和 `runtime_assets/`。服务器只改代码或配置时，统一执行 `docker compose up -d --build`。


## 眼镜 APK 更新发布

2026-10-16 已上线教师、学生 2.1（versionCode=3）。`GET /api/glasses/updates/teacher` 或 `/student` 返回版本、包名、摘要和下载地址；APK 保存在 `runtime_assets/app_updates/<角色>/<角色>-<版本号>.apk`，对应 `latest.json`。只提供查询和下载，不开放上传接口。

发布新版先上传保持原签名、递增版本号的 APK，核对摘要后替换 latest.json。字段为 package_name、version_code、version_name、sha256、size_bytes、notes，download_url 由接口生成。教师和学生分开发布，不触及身份库和录制资料。

2026-10-18 本地准备 2.2（versionCode=4）：两版单击镜腿直接拍照，教师不再单独唤醒；教师语音按语句一次判断固定动作。旧 2.1 的语音接口继续兼容。先按上文部署新的 `code_deploy.zip`，确认健康接口已有 `teacher_direct_control`，再上传 `release/服务器部署/glasses-update-2.2.zip` 并发布：

```bash
cd /projects/chemistry-glasses
unzip glasses-update-2.2.zip -d /tmp
sudo python3 /tmp/glasses-update-2.2/publish_app_updates.py
```

发布脚本仅写入 `runtime_assets/app_updates/teacher/` 与 `student/` 的新版 APK、latest.json，不删除旧版，不触碰其他数据。APK 单独发布，不混进受保护的 `code_deploy.zip`。已装带更新功能的 2.1 联网版可重新打开助手，选择“立即更新”，下载后按系统提示安装；也可在空闲时长按镜腿检查。USB 测试包不走在线更新。本地打包不代表已经发布到服务器。

2026-10-19 已部署至 `.134`：后台支持教师 `teacher_direct` 一次判定，教师、学生联网 APK 2.2（versionCode=4）均已发布。两版单击直接拍照，教师取消额外唤醒；学生录音与录像使用各自的开始／结束内置提示音，问答保持只显示文字。两版 APK 的公开下载及 SHA256 已核对。原有 9 条眼镜排班、705 批归属及 1391 条归属明细全部保留，四类录制资料数量核对一致，服务器 `.env` 未改，不额外备份；仅重建化小北 API，其他服务保持运行。正式联网版用户可在眼镜内更新，本地 USB 测试版仍需覆盖安装联网 APK 才会使用服务器。

同日追加发布 2.3（versionCode=5）：学生单击与语音“拍照”共用同一个入口和完整流程，包括开始提示、倒计时、拍照、保存和上传，取消单击专用快捷分支。两版拍照成功增加内置“拍照完成”提示；学生先显示“照片上传中”，收到服务器回执后显示“照片已上传”，失败则提示自动重试。两版联网 APK 已发布并通过公开下载与 SHA256 校验；连接眼镜也已覆盖安装联网 2.3，并实机验证单击产生的新照片收到 `.134` 上传成功回执。此次仅发布眼镜 APK 和更新信息，后台服务未重启，原配置及归属关系不变。正式联网 2.1／2.2 可通过眼镜内更新到 2.3，仍需系统确认安装。


## 使用概览：仅后台统计（2026-10-30 已部署）

只需部署新的 `code_deploy.zip`，无需更新眼镜 APK。教师、管理员工作台新增“使用概览”，可筛选日期和角色、全屏展示。按后台成功问答、接受的纠错任务、成功上传的资料累计，视频为访问估算；缺少身份的归入“未区分”。不扫描、回填历史，教师连续录音不计入主动录音。

统计保存在独立的 `runtime_assets/usage/usage.sqlite3`，和其他运行数据一起保留。未发布的 2.7 APK 上报方案已撤销，勿再发布该旧方案的 APK 包。学生取消单击拍照保留在已有 2.6.2 包中，教师不需要为统计更新。

2026-10-30 已部署代码 `f76640c` 至 `.134`：从 `code_deploy.zip` 提取七个统计相关文件，通过安全解压 `--no-backup` 更新，只重建 `chemistry-api` 并重载 Nginx。实际管理员登录后，概览页面、统计接口、健康和登录页均返回 200；首次统计为 0，没有生成演示记录或回填历史。20 条排班、925 批归属、1615 条归属明细的旧行全部保留；教师录制／照片、学生录制／照片文件数分别为 7497／996／70／228，均未减少。`.env`、Compose、Nginx 配置和 APK 发布信息未变，其他容器保持原实例。教师 APK 仍为 2.5.1、学生仍为 2.6，未操作眼镜。

## 视频转发修正（2026-10-20）

已部署代码提交 `6c3ebc4` 至 `.134`，仅重建 `chemistry-api`，使用安全解压的 `--no-backup`，不额外备份。外部知识库的 `is_transcript` 视频转录结果现已返回真实 MP4，当前后台识别到 5 个可播放候选；修正 HEAD 转发误报长度为 0 的问题。

通过 Nginx 抽查“2-5滴定管的洗涤”和“2-7滴定”：HEAD 正确返回 47,845,334 与 88,653,012 字节，GET 开头、末尾 Range 均返回 206 和正确总长度。原有 12 条排班、705 批归属、1391 条归属明细逐条核对保留，四类原资料数量未减少，`.env` 与两版 APK 发布信息未改，其他服务保持运行。此次无需更新眼镜 APK，实际解码与播放可在眼镜上试播确认。
