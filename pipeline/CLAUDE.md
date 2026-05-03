# Pipeline: CV检测 + VLM纠错

## 项目目标

将 CV 特征匹配检测和 VLM 大模型分析串联成端到端 pipeline：
新来一张学生实验图片 → CV 自动检测各装置位置画框 → VLM 逐装置找问题并总结。

---

## 文件结构

```
pipeline/
  CLAUDE.md
  common/
    __init__.py
    timing.py              TimingRecord + StepTimer 计时工具
    cv_wrapper.py          CV relaxed 检测包装 + 参数暴露 + 参考图画框
    image_builder.py       标注图构建 + label_summary + CV 结构化摘要
    vlm_client.py          VLM 客户端包装 + provider 选择 + 消息构建
  approach_a/
    __init__.py
    pipeline_a.py          一步到位 VLM：两张标注图 + 全部上下文 → 单次调用
  approach_b/
    __init__.py
    pipeline_b.py          两步式 VLM：2a 逐色框找不同 → 2b 结合实验要求分析
  web/
    __init__.py
    app.py                 Flask 应用 (端口 5080)
    static/styles.css
    templates/index.html
```

---

## Pipeline 流程

```
新图（学生带纠正图片）
       │
       ▼
  ┌──────────────┐
  │ Step 1: CV   │  detect_image_relaxed() 对新图检测
  │  自动检测     │  build_reference_result() 从标注读 bbox 画框
  └──────┬───────┘  输出: 两张带彩色检测框的标注图 + label_summary
         │
    ┌────┴────┐
    ▼         ▼
 方案A       方案B
 一步到位    2a 找不同 → 2b 分析
```

## 关键参数（CVDetectionParams）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| max_side | 1600 | 图片最大边长 |
| lowe_ratio | 0.75 | Lowe ratio test 阈值 |
| min_good_matches | 3 | 最少好匹配数（relaxed） |
| min_inliers | 2 | 最少内点数（relaxed） |
| min_inlier_ratio | 0.08 | 最低内点比例（relaxed） |
| top_k_per_label | 6 | 每个 label 保留 top-k 候选 |
| overlap_weight | 0.75 | 重叠惩罚权重 |
| feature | "akaze" | "akaze" 或 "sift" |

## 方案对比

### 方案A（approach_a/pipeline_a.py）
- 一次 VLM 调用，prompt 包含：色框说明 + CV 检测概况 + 实验要求
- 输出：逐色框差异 + 主要错误
- 计时：image_build_ms, vlm_call_ms

### 方案B（approach_b/pipeline_b.py）
- 第 1 次 VLM 调用：两张标注图，只做逐色框找不同（[label] 标签格式输出）
- 第 2 次 VLM 调用：纯文本，diff_text + cv_summary + 实验要求 → 主要错误
- 计时：image_build_ms, vlm_step1_ms, vlm_step2_ms

## Web API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 单页 UI |
| GET | `/api/references` | 列出可用参考图 |
| POST | `/api/run` | 运行 pipeline（支持 approach=a/b/both） |

启动：`python pipeline/web/app.py --port 5080`

## 关键依赖

- `sys.path` 自动插入 `CV_detection2/` 和项目根目录（各模块自行处理）
- CV 检测需要 `descriptor_db.npz` / `.json` 已建好（先跑 annotate_app.py）
- VLM 需要 API key 配置在 `.env` 或环境变量中
