# Baseline VLM

一个独立的最简视觉基线，目前支持两个 provider：

- `openrouter`
- `ali`（DashScope compatible-mode）

## 功能

1. 在网页里填一段固定的公共实验指导
2. 先发一次“预热 cache”请求
3. 后续每次上传一张图片并提具体问题
4. 前端流式显示模型回复
5. 展示首 token 时间、总耗时、`cached_tokens`、`cache_write_tokens`

## 运行

先确保根目录或当前目录有 `.env`。

OpenRouter 至少需要：

```env
OPENROUTER_API_KEY=...
```

Ali 至少需要：

```env
DASHSCOPE_API_KEY=...
```

启动 OpenRouter 版本：

```powershell
python baseline_vlm/app.py --provider openrouter --debug
```

启动阿里版本：

```powershell
python baseline_vlm/app.py --provider ali --debug
```

默认地址：

```text
http://127.0.0.1:5051
```

## 说明

- 两个 provider 都使用 `cache_control: {"type": "ephemeral"}`，把固定指导文本作为前缀
- 是否真的命中 cache，要看模型与 provider 是否支持，以及公共指导是否足够长
- 页面显示的 cache 指标来自各自返回的 `usage.prompt_tokens_details`
- 对 Ali 来说，显式缓存创建字段通常是 `cache_creation_input_tokens`
