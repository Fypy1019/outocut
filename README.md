# OutoCut

OutoCut 是面向 Windows 10/11 的本地优先短视频混剪工作台。桌面端采用 Electron、Vue 3 与 TypeScript，本地视频引擎采用 FastAPI、SQLite 和 FFmpeg。

## 已实现

- 素材分类、递归扫描、ffprobe 元数据和异常标记
- 人设档案、混剪模板、字幕、标题、背景音乐和输出设置
- 阿里云百炼结构化分镜文案
- MiniMax 音色列表、TTS、音频上传和声音复刻
- DPAPI 加密 API Key、本地会话令牌和 Electron 安全桥
- 单队列批量合成、进度、取消、失败重试和崩溃恢复
- CPU H.264、NVENC 探测与片段阶段自动降级
- MP4、ASS 字幕和 JSON 任务清单产物

## 开发环境

要求：Node.js 24+、pnpm 11+、Python 3.11–3.14、FFmpeg/ffprobe。

```powershell
pnpm install
python -m pip install -e "engine[dev]"
pnpm run dev
```

单独启动引擎进行 API 调试：

```powershell
$env:OUTOCUT_SESSION_TOKEN='development-token'
pnpm run dev:engine
```

## 验证

```powershell
pnpm run build
python -m ruff check engine
python -m pytest engine/tests
```

Python 测试会用 FFmpeg 生成临时视频并完成一次真实的端到端混剪。

## Windows 打包

```powershell
.\scripts\build-engine.ps1
.\scripts\copy-ffmpeg.ps1
pnpm run package:win
```

正式分发前必须在 `vendor/ffmpeg/LICENSE.txt` 中放入所分发 FFmpeg 构建的许可证说明，并确认所选构建与产品分发方式兼容。

## 数据与安全

- 默认数据目录：`%LOCALAPPDATA%\OutoCut\workspace`
- API Key 使用当前 Windows 用户的 DPAPI 加密，只存储密文。
- 本地引擎监听随机回环端口，并要求进程级 Bearer Token。
- 任务保留模板、素材、随机种子和镜头快照，便于追溯。

## 首版边界

不包含短视频平台自动发布、排期、抖音链接解析、激活码、订阅后台、自动更新和 macOS 支持。
