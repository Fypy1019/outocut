# OutoCut

OutoCut 是面向 Windows 10/11 的本地短视频处理与智能混剪工作台。桌面端使用 Electron、Vue 3 和 TypeScript，本地媒体引擎使用 FastAPI、SQLite、Python 和 FFmpeg。

本文档适用于 OutoCut `0.2.6`，重点说明如何在一台全新的 Windows 电脑上配置环境、验证源码、生成安装包并安装运行。

## 功能概览

- 素材目录管理、递归扫描、视频元数据识别和异常素材标记
- 镜头剪辑、混剪模板、任务队列、失败重试和中断恢复
- 字幕、标题、背景音乐、智能配音和声音复刻
- 分辨率批量转换与 CPU/NVIDIA NVENC 编码
- 水印、静态贴纸、像素点 GIF、视频覆盖和固定去帧
- 文件批量重命名
- 千川素材上传辅助
- 电商套图生成
- 抖音链接解析与语音识别
- API Key 本地加密存储、本地引擎随机端口和会话令牌

## 项目结构

```text
outocut/
├─ electron/                 Electron 主进程和系统能力
├─ engine/                   Python/FastAPI 本地媒体引擎
│  ├─ outocut_engine/        引擎源代码
│  ├─ tests/                 Python 测试
│  └─ dist/outocut-engine/   PyInstaller 生成的引擎程序
├─ scripts/
│  ├─ build-engine.ps1       打包 Python 引擎
│  ├─ copy-ffmpeg.ps1        复制 FFmpeg 到 vendor
│  └─ verify-dist.mjs        检查前端生产资源
├─ src/                      Vue 前端
├─ vendor/ffmpeg/            安装包内置的 FFmpeg、ffprobe 和许可文件
├─ dist/                     前端生产文件
├─ dist-electron/            Electron 编译文件
├─ release/                  Windows 安装包和免安装目录
├─ package.json              应用版本、命令和 electron-builder 配置
└─ pnpm-lock.yaml            锁定的 Node.js 依赖版本
```

## 新机器打包：准备环境

### 1. 系统要求

- Windows 10/11 64 位
- 建议至少 8 GB 内存
- 建议至少预留 5 GB 磁盘空间用于依赖、构建缓存和安装包
- 能访问 npm、Python 包索引以及 Electron/NSIS 下载源的网络

需要安装以下软件：

| 软件 | 要求 | 用途 |
| --- | --- | --- |
| Node.js | 24 或更高版本 | 构建 Vue 和 Electron |
| pnpm | 11 或兼容版本 | 安装和锁定前端依赖 |
| Python | 3.11–3.14，64 位 | 构建本地视频引擎 |
| FFmpeg | 同时包含 `ffmpeg` 和 `ffprobe` | 视频处理及打包内置运行时 |
| Git | 可选 | 克隆和更新源码 |

安装 Python 时建议勾选 **Add Python to PATH**。安装 FFmpeg 后，需要把包含 `ffmpeg.exe` 和 `ffprobe.exe` 的 `bin` 目录加入系统 `PATH`。

### 2. 验证基础命令

打开一个新的 PowerShell 窗口，运行：

```powershell
node --version
npm --version
python --version
ffmpeg -version
ffprobe -version
```

任何一条出现“无法识别”时，都应先修复对应软件的安装或 `PATH`，不要继续打包。

安装 pnpm：

```powershell
npm install --global pnpm@11
pnpm --version
```

## 新机器打包：获取并初始化项目

可以使用 Git 克隆项目，也可以把源码压缩包解压到本地。建议使用较短、可写的目录，例如：

```text
E:\OutoCut\outocut
```

以下所有命令都应在**项目根目录**执行，而不是在 `scripts` 目录执行：

```powershell
cd E:\OutoCut\outocut
```

### 1. 临时允许本项目的 PowerShell 脚本

如果源码来自下载的 ZIP 或另一台电脑，PowerShell 可能阻止未签名脚本。仅对当前窗口临时放行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

该设置会在关闭当前 PowerShell 窗口后自动失效，不会永久降低系统执行策略。

如果文件仍被标记为来自网络，可解除两个脚本的文件锁定：

```powershell
Unblock-File .\scripts\build-engine.ps1
Unblock-File .\scripts\copy-ffmpeg.ps1
```

### 2. 创建 Python 虚拟环境

强烈建议使用项目独立虚拟环境，避免打包时混入其他项目的 Python 包：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".\engine[dev]"
```

激活成功后，PowerShell 提示符前通常会出现 `(.venv)`。

检查 PyInstaller 和本地引擎：

```powershell
python -m PyInstaller --version
python -m outocut_engine --help
```

### 3. 安装 Node.js 依赖

```powershell
pnpm install --frozen-lockfile
```

`--frozen-lockfile` 会严格使用仓库中的 `pnpm-lock.yaml`，适合正式打包。如果确实更新了 `package.json` 中的依赖，再运行普通的 `pnpm install` 并提交新的锁文件。

## 一次完整的 Windows 打包

确认当前目录是项目根目录，并且 Python 虚拟环境仍处于激活状态：

```powershell
cd E:\OutoCut\outocut
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

依次执行：

```powershell
.\scripts\build-engine.ps1
.\scripts\copy-ffmpeg.ps1
pnpm run package:win
```

三个命令的作用如下：

1. `build-engine.ps1` 使用 PyInstaller 生成 `engine\dist\outocut-engine\outocut-engine.exe`。
2. `copy-ffmpeg.ps1` 从系统 `PATH` 找到 FFmpeg，把 `ffmpeg.exe`、`ffprobe.exe` 及可找到的许可说明复制到 `vendor\ffmpeg`。
3. `pnpm run package:win` 编译前端和 Electron，然后使用 electron-builder 生成 NSIS 安装包。

打包过程中 electron-builder 可能首次下载 Electron 和 NSIS，所需时间取决于网络速度。命令没有返回 PowerShell 提示符前，不要关闭窗口。

### 打包产物

成功后主要产物位于：

```text
release\OutoCut-0.2.6-Setup.exe       正式安装包
release\OutoCut-0.2.6-Setup.exe.blockmap
release\win-unpacked\OutoCut.exe     免安装测试程序
```

正式交付给用户的是 `OutoCut-0.2.6-Setup.exe`。`win-unpacked` 适合开发者在打包后快速启动检查，不建议只复制其中的 `OutoCut.exe`；它依赖同目录下的其他文件。

## 打包前验证

建议在正式生成安装包前运行：

```powershell
pnpm run build
pnpm test
python -m ruff check engine
python -m pytest engine\tests
```

也可以运行项目定义的组合检查：

```powershell
pnpm run check
```

Python 测试会调用 FFmpeg 创建临时媒体文件，因此 FFmpeg 必须可用。

## 修改版本号并发版

发布新版本前，需要同步修改以下三处，避免安装包、桌面端和引擎显示不同版本：

```text
package.json
engine\pyproject.toml
engine\outocut_engine\__init__.py
```

例如发布 `0.2.7`，三处都改成 `0.2.7`，然后重新执行完整打包流程。安装包名称由 `package.json` 中的版本自动生成。

打包后可检查版本和 SHA-256：

```powershell
$installer = Get-Item .\release\OutoCut-0.2.6-Setup.exe
$installer.VersionInfo | Select-Object ProductVersion, FileVersion
Get-FileHash -Algorithm SHA256 $installer.FullName
```

交付时建议同时记录版本号、文件大小、SHA-256 和构建日期。

## 在目标电脑安装

1. 把 `release\OutoCut-0.2.6-Setup.exe` 复制到目标电脑。
2. 双击安装包。
3. 根据安装向导选择安装目录。
4. 安装完成后从桌面快捷方式或开始菜单启动 OutoCut。
5. 第一次启动后进入“系统设置”，配置素材目录、输出目录以及需要使用的第三方 API Key。

安装后的程序已经内置 Python 引擎、FFmpeg 和 ffprobe，普通用户不需要再安装 Node.js、Python 或 FFmpeg。

如果项目没有配置代码签名证书，Windows SmartScreen 可能显示“Windows 已保护你的电脑”。正式对外分发时应配置可信的 Windows 代码签名证书；内部测试时应先核对安装包来源和 SHA-256，再决定是否继续运行。

## 开发运行

完成依赖安装并激活虚拟环境后：

```powershell
pnpm run dev
```

单独启动 Python 引擎：

```powershell
$env:OUTOCUT_SESSION_TOKEN = 'development-token'
pnpm run dev:engine
```

## 数据、日志与卸载

- 安装程序不会把用户任务数据写进安装目录。
- 桌面版默认工作区位于 Electron 用户数据目录下的 `workspace`，通常是 `%APPDATA%\OutoCut\workspace`；用户在系统设置中修改工作区后，以实际选择的目录为准。
- 桌面端日志通常位于 `%APPDATA%\OutoCut\logs\desktop.log`。
- API Key 使用当前 Windows 用户的 DPAPI 加密，只保存密文。
- 本地引擎只监听 `127.0.0.1` 的随机端口，并使用进程级 Bearer Token。
- 卸载或覆盖安装前，如果需要保留任务、素材索引和设置，请先备份实际工作区。

## 常见打包问题

### 无法运行 `build-engine.ps1`：未进行数字签名

只在当前 PowerShell 窗口临时放行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

然后从项目根目录运行：

```powershell
.\scripts\build-engine.ps1
```

### `python` 或 `PyInstaller` 无法找到

确认虚拟环境已激活：

```powershell
.\.venv\Scripts\Activate.ps1
python --version
python -m PyInstaller --version
```

如果 PyInstaller 尚未安装：

```powershell
python -m pip install -e ".\engine[dev]"
```

### `ffmpeg` 或 `ffprobe` 无法识别

```powershell
Get-Command ffmpeg
Get-Command ffprobe
```

如果找不到，安装 FFmpeg，并把包含两个 exe 的目录加入系统 `PATH`。修改 `PATH` 后要关闭并重新打开 PowerShell。

### `copy-ffmpeg.ps1` 没有复制许可证文件

脚本会尝试从 FFmpeg 安装目录寻找 `LICENSE` 和 `README.txt`，但不同发行版的目录结构可能不同。正式分发前必须人工确认以下文件存在且内容对应实际使用的 FFmpeg 构建：

```text
vendor\ffmpeg\LICENSE.txt
vendor\ffmpeg\BUILD-INFO.txt
```

FFmpeg 的许可条件取决于具体构建选项。不要在未核对许可证和分发义务的情况下对外发布安装包。

### `pnpm` 无法识别

```powershell
npm install --global pnpm@11
pnpm --version
```

### electron-builder 下载失败

首次打包需要下载 Electron、NSIS 等文件。检查网络、代理、防火墙和安全软件；恢复网络后重新运行：

```powershell
pnpm run package:win
```

### 安装后提示“本地引擎未连接”

- 先检查安全软件是否隔离了 `outocut-engine.exe`。
- 检查安装目录的 `resources\engine\outocut-engine.exe` 是否存在。
- 检查安装目录的 `resources\ffmpeg\ffmpeg.exe` 和 `ffprobe.exe` 是否存在。
- 在应用“系统设置”中查看或刷新诊断日志。
- 查看 `%APPDATA%\OutoCut\logs\desktop.log`。

### 修改代码后安装包仍像旧版本

确认执行顺序完整：

```powershell
.\scripts\build-engine.ps1
.\scripts\copy-ffmpeg.ps1
pnpm run package:win
```

只运行 `pnpm run package:win` 不会重新冻结 Python 引擎，因此 Python 代码变更可能不会进入安装包。

### 路径或权限异常

- 不要把源码放在需要管理员权限写入的目录，例如 `C:\Program Files`。
- 建议使用较短路径，避免部分 Windows 工具遇到路径长度限制。
- 不要在 OneDrive 正在同步或安全软件正在扫描构建目录时重复启动多个打包进程。

## 常用命令速查

```powershell
# 进入项目
cd E:\OutoCut\outocut

# 当前窗口临时允许脚本
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 激活 Python 环境
.\.venv\Scripts\Activate.ps1

# 开发运行
pnpm run dev

# 前端/Electron 构建
pnpm run build

# 测试
pnpm test
python -m pytest engine\tests

# 完整 Windows 打包
.\scripts\build-engine.ps1
.\scripts\copy-ffmpeg.ps1
pnpm run package:win

# 安装包位置
Get-Item .\release\OutoCut-0.2.6-Setup.exe
```

## 发布检查清单

- [ ] 三处版本号一致
- [ ] `pnpm install --frozen-lockfile` 成功
- [ ] Python 虚拟环境已激活且依赖完整
- [ ] `ffmpeg` 和 `ffprobe` 可从 `PATH` 找到
- [ ] 前端构建、前端测试和 Python 测试通过
- [ ] 已重新执行 `build-engine.ps1`
- [ ] `vendor\ffmpeg` 内的 exe 和许可文件完整
- [ ] `pnpm run package:win` 成功
- [ ] 安装包版本号正确
- [ ] SHA-256 已记录
- [ ] 在一台未安装开发环境的 Windows 电脑上完成安装和启动测试
- [ ] 核心媒体功能已做一次真实素材验证

## 许可证与第三方服务

正式分发前，应确认 FFmpeg 构建、Python 依赖、Node.js 依赖和其他随包文件符合各自许可证要求。AI 文案、图片、语音识别、配音、抖音或千川相关功能可能需要网络、有效账号、API Key、Cookie、余额或平台权限；这些外部条件不随安装包提供。
