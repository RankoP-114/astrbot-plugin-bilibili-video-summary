# B站视频总结

B站视频总结是一个 AstrBot B站视频总结插件。它参考常见的视频总结工作流实现：

`B站链接/BV号 -> 优先读取 B站字幕 -> 无字幕时下载音频并 ASR 转写 -> LLM 生成 Markdown -> Pillow 渲染图片 -> 回传聊天`

图片渲染只依赖 Python 的 `Pillow`，不需要 `wkhtmltopdf`。
生成的字幕缓存和总结图片会按 `generated_retention_hours` / `generated_max_files` 自动清理。

## 功能

- `/B站视频总结 <B站链接/BV号/b23短链>`：生成视频总结。
- `/B站登录`：发送二维码，使用 B站 App 扫码后自动保存 Cookie。
- `/B站状态`：查看当前 B站登录状态。
- `/B站登出`：清除扫码登录保存的 Cookie。
- `/B站最新 <UP主UID/空间链接/昵称>`：总结 UP 主最新视频。
- `/B站订阅 <UP主UID/空间链接/昵称>`：订阅 UP 主，新视频可自动推送总结。
- `/B站取消订阅 <UP主UID/空间链接/昵称>`：取消订阅。
- `/B站订阅列表`：查看当前会话订阅。
- `/B站检查更新`：手动检查订阅更新。
- `/B站识别开关`：开关 B站链接自动识别。

## B站登录

可以通过聊天命令扫码获取 B站 Cookie：

- `/B站登录`：发送二维码，使用 B站 App 扫码后自动保存 Cookie。
- `/B站状态`：查看当前登录状态。
- `/B站登出`：清除扫码登录保存的 Cookie，并清空配置里的 `bilibili_cookie`。

如果你不想扫码，也可以手动在配置里填写 `bilibili_cookie`。

## 权限

`/B站视频总结` 只受群聊黑/白名单限制。其他扩展功能默认仅 AstrBot 管理员可用，开启 `allow_non_admin_commands` 后可允许非管理员调用；`/B站登录` 始终仅 AstrBot 管理员可用。

## 日志

插件日志直接输出到 AstrBot 日志系统中。开启配置项 `debug_mode` 后，会额外输出自动识别、订阅推送、下载/字幕/ASR 等关键步骤。

## ASR 配置

`asr_provider` 有两种：

- `bcut`：使用必剪云端 ASR。优点是无需自己准备模型；缺点是非公开正式 API，稳定性和限流不可控，音频也会上传到必剪服务。
- `openai_compatible`：使用 OpenAI 兼容的 `/audio/transcriptions` 接口。需要配置：
  - `asr_api_base`
  - `asr_api_key`
  - `asr_model`
  - `asr_endpoint`，默认 `/audio/transcriptions`

## LLM 配置

默认 `llm_provider=astrbot`，插件会调用 AstrBot 当前会话/默认聊天模型。

也可以设置 `llm_provider=openai_compatible`，并配置：

- `llm_api_base`
- `llm_api_key`
- `llm_model`

## Docker 字体提示

Pillow 渲染中文需要系统字体。如果 Docker 里中文显示成方块，安装或挂载 Noto CJK 字体，然后在配置里填写 `font_path`，例如：

```text
/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
```

## 系统依赖

`yt-dlp` 下载音频并调用 FFmpeg 转 mp3，因此运行环境需要安装 `ffmpeg`。

```bash
apt update
apt install -y ffmpeg fonts-noto-cjk
```

Python 依赖由 AstrBot 读取 `requirements.txt` 安装：

```text
aiohttp
yt-dlp
Pillow
segno
```

## 安装

按 AstrBot 官方插件结构，将本目录放到：

```text
AstrBot/data/plugins/astrbot_plugin_bililens
```

然后在 AstrBot WebUI 的插件管理中重载/启用插件。

## 隐私与稳定性

- 使用 `bcut` ASR 时，音频会上传到必剪/B站相关服务。
- 使用 `openai_compatible` ASR 时，音频会上传到你配置的 ASR 服务。
- B站网页接口和必剪 ASR 都不是稳定商业 API，长期生产使用建议准备 OpenAI-compatible ASR 作为备用。
