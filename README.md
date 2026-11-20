# NeoNote

**新的乐章，从认谱和第一段旋律开始。**

NeoNote 是面向音乐创作新手的本地音乐工作台，提供 **创作／练习／扒谱** 三个独立入口。你可以用电脑键盘练习五线谱，输入旋律、歌词和情感让 AI 协助编曲，也可以把钢琴录音转为 MIDI，再校对并整理五线谱。

工作台采用深色录音室界面，可通过导航中的“主题”切换浅色，浏览器会记住选择。桌面使用左侧导航，手机使用底部导航。“首页”提供三个工作台的入口和最近更新的本地作品。

平台在本机运行，歌曲与练习记录保存在本地；AI 编曲通过联网调用 DeepSeek API 完成。

[快速开始](#快速开始) · [练习模式](#练习模式) · [创作模式](#创作模式) · [项目结构](#项目结构) · [开发与检查](#开发与检查)

## 功能概览

| 功能 | 可以做什么 |
| --- | --- |
| 识谱与节奏练习 | 直接选择本地基础单元，或导入 MIDI 自选片段；看五线谱完成认音与节奏两轮，保留首次答案、纠正和节奏记录。 |
| 钢琴扒谱 | 上传 MP3/WAV、选择最多60秒的片段，本地转录、音符校对、撤销及修订保存；确认节拍后导出 MIDI/MusicXML。混音钢琴分离为试验功能，须先试听确认。 |
| 虚拟键盘 | 使用电脑键盘或屏幕琴键试弹，切换乐器音色，录制和回放旋律。 |
| AI 编曲 | 根据旋律、歌词、主题和情感生成键盘、鼓、吉他、贝斯四声部编曲，支持自然语言整曲修改。 |
| MIDI 编辑 | 增删、移动音符，调整音高、时长和力度，控制声部音色、音量、独奏与静音。 |
| 歌词与旋律 | 查看和修复歌词音符对应，逐句试听、慢速循环练唱；试验入口提供乐句级谱曲与转音编辑。 |
| 中文演唱 | 使用绮萱音源按已保存的歌词和旋律合成普通话独唱，修正读音，单听人声或与伴奏合听。 |
| 音乐导出 | 导入导出多轨 MIDI，通过本地 SoundFont 渲染 WAV/MP3，下载独立人声及混音。 |
| 作品管理 | 独立歌曲目录、手动保存、撤销与历史版本恢复、歌曲回收站；音频与乐谱版本绑定。 |

## 快速开始

### 1. 准备环境

以下命令在 **Windows PowerShell** 中、项目根目录下运行。

- 安装 Conda，并准备名为 `piano` 的 Python 3.12 环境。
- 安装 Node.js 与 npm；项目使用 Vite 8，Node.js 可选 22.12+ 或 24 系列。
- 使用 Chrome 或 Edge 访问本地工作台。

如果尚未创建 `piano` 环境，先执行：

```powershell
conda create -n piano python=3.12
```

安装 Python 与前端依赖：

```powershell
conda run -n piano python -m pip install -r requirements-dev.txt
conda run -n piano python -m pip check
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_frontend.ps1
```

Python 开发、运行和测试统一使用 `piano`。仅安装 Python 运行依赖时，可以将 `requirements-dev.txt` 替换为 `requirements.txt`。

### 2. 准备本地音频资源

试听和练习需要 SoundFont；WAV/MP3 导出还需要 FluidSynth 和 FFmpeg。第三方二进制、音色及歌声模型不随源码提交，需要另行准备。

当前程序使用以下路径，解压时请保留所需的配套文件：

```text
tools/
├─ soundfonts/GeneralUser-GS-main/GeneralUser-GS.sf2
├─ fluidsynth-v2.6.1-win10-x64-cpp11/bin/fluidsynth.exe
└─ ffmpeg-9.0.2-essentials_build/bin/
   ├─ ffmpeg.exe
   └─ ffprobe.exe
```

资源版本与说明见 [音色与音频渲染文档](docs/stage2.md)。路径由 [音频资源模块](backend/app/resources.py) 定义，更换目录后需同步该模块；修改 `.env` 中的音频路径预留项不会改变实际路径。

检查资源：

```powershell
conda run -n piano python scripts/check_audio.py
```

缺少音频资源时，歌曲编辑和 MIDI 功能仍可使用；试听、练习发声和音频导出需要先完成资源准备。

扒谱还需要可选的本地推理依赖和官方模型。在 Python 3.12 的 `piano` 中显式安装和准备：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_transcription.ps1 -PrepareModels
```

该脚本安装 PyTorch 2.8 CPU、Demucs 4.0.1 等可选依赖，下载固定 Transkun 2.0.1 源码／权重和官方六声部权重到 `tools/transcription/`。普通启动和页面访问不会下载。安装差异、许可、哈希与兼容条件见[扒谱交付文档](docs/transcription-delivery-20261028.md)。扒谱不需要 API 密钥；AI 编曲和中文演唱仍分别准备。

### 3. 启动工作台

双击根目录的 **启动工作台.cmd**，或执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_local.ps1
```

打开 **http://127.0.0.1:8000** 进入创作工作台，也可从导航打开首页、练习或扒谱。启动脚本会检查依赖和资源、构建前端，并启动单进程本地服务。

在终端按 `Ctrl+C` 停止服务。源码更新后重新启动即可构建新页面；只有需要复用上次构建时才添加 `-SkipBuild`。

### 4. 配置 AI 编曲（可选）

仅在根目录不存在 `.env` 时，复制配置模板：

```powershell
if (!(Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

用本地编辑器填写配置：

| 配置项 | 用途 |
| --- | --- |
| `MODEL_MOCK` | `true` 使用标注为模拟的演示结果；`false` 发起真实模型请求。 |
| `MODEL_PROVIDER` | 当前正式适配器使用 `deepseek`。 |
| `MODEL_BASE_URL` | 模型服务地址。 |
| `MODEL_NAME` | 账号可用且与当前适配器兼容的模型名称。 |
| `MODEL_API_KEY` | 你的 API 密钥，仅保存在本地 `.env`。 |
| `MODEL_MAX_TOKENS` | 无歌词短曲的输出上限；全文歌词及整曲流程使用各自固定上限。 |

配置修改后重启后端。页面会在真实生成前提示发送的输入和额度消耗；启动应用本身不会触发付费请求。只有后端加载 `MODEL_*`，密钥不发送到前端。完整说明见 [模型接入文档](docs/model-integration.md)。

### 5. 准备中文演唱（可选）

需要中文演唱时，再准备歌手、声码器、OpenUtau 和项目内 .NET SDK。以下脚本会联网下载较大的第三方资源，再构建本地演唱工具：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_vocals.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_vocal_worker.ps1
conda run -n piano python scripts/check_vocal_resources.py
```

当前组合为绮萱 Qixuan v2.7.0、PC-NSF-HiFiGAN 2025.02 与 OpenUtau 核心，按普通话独唱、非商业演示使用。资源、环境和各自许可见 [中文演唱说明](docs/vocal-delivery-20261010.md)。

## 练习模式

1. **选择材料**：新手可以直接点选“本地基础单元”的练习谱，不需要 MIDI 或已有歌曲。自选曲目可上传 MIDI，选择非鼓轨道及 1–16 小节，或从已保存创作复制。
2. **核对五线谱**：检查整理后的节奏、谱号、调号和音域，对照试听原始片段与整理结果，确认后保存练习版本。
3. **认音练习**：在键盘捕获区演奏，按对后前进；可以纠正错误，首次答案与纠正记录分别保留。
4. **节奏练习**：全部目标最终正确后，从头开始同一片段的节奏轮。可选 0.5、0.75、1 倍速度，预备拍结束后跟随节拍演奏。
5. **查看记录**：分别查看音高、准拍、提前/滞后、漏音和休止误按，下载练习记录。

和弦需要同时按下，不能逐个弹奏后拼成答案。暂停或失去焦点会释放声音；练习记录保存失败时会保留本地缓冲，可重试保存。

### 本地基础单元

内置 5 个单元、15 条项目原创短谱，每条 2–4 小节。点选练习后，音高、节奏和五线谱自动准备，无需选择轨道或量化；首次核对并保存练习版本，之后可直接重开，再开始新的认音轮。

| 单元 | 内容 |
| --- | --- |
| 单音认谱 | C–G、一个八度的白键、打乱顺序找音。 |
| 黑键与升降号 | 五个黑键、升号与降号的对应琴键。 |
| 基础节奏 | 四分与二分音符、八分音符、休止。 |
| 简单旋律 | 上行下行、跳进、重复同音重新起音。 |
| 和弦与延音 | 双音、三和弦、跨小节延音保持。 |

单元可自由选择，显示认音完成、完整节奏轮已练及达到建议标准的记录；练过一轮不等于节奏达标。记录页显示单元与练习名称，旧谱版本及首次错误保留。教材定义随源码提供，使用过程不调用模型。详细使用、保存和练习提示见[本地基础单元说明](docs/practice-units-delivery-20261014.md)。

### 练习键位

五线谱右侧提供按键提醒，列出白键、黑键与 Shift / 空格的八度规则，方便看谱时对照；窄屏时提醒放在谱面下方。练习区域用档位指示显示当前基础八度，并列出收到的按键及发声音高。

默认基础八度为 4：

| 白键 | s | d | f | g | h | j | k | l |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 音高 | C4 | D4 | E4 | F4 | G4 | A4 | B4 | C5 |

| 黑键 | e | r | y | u | i |
| --- | --- | --- | --- | --- | --- |
| 音高 | C♯4 | D♯4 | F♯4 | G♯4 | A♯4 |

- **单按 Shift / 空格后松开**：降低 / 升高基础八度，基础八度可选 3–5。
- **按住 Shift / 空格并弹音符**：同时演奏基础音与低 / 高八度；本次组合不切换基础八度。
- **l 始终是右侧高 C**：默认 `d+l` 为 D4+C5，`Shift+d` 为 D3+D4。
- 同时按住两个修饰键时不接受新音；松开修饰键只释放相应副音。

页面提供多键自检、屏幕琴键和敲拍校准。练习材料会检查当前键位能否演奏，普通电脑键盘的多键能力以实际设备为准。

## 创作模式

歌曲栏管理本地作品和回收站，顶栏显示保存状态，并提供保存和历史版本入口。通过“收集灵感”“演奏与编辑”“保存与导出”切换步骤。录制时间线显示实际弹奏的音符，键盘、鼓、吉他、贝斯分别使用紫色、粉红色、橙色和青色。

1. **新建歌曲**，通过虚拟键盘录制片段、导入 MIDI，或直接输入主题与音乐感觉。
2. **填写歌词与要求**。可上传 UTF-8 TXT 或粘贴歌词，选择保留原旋律或允许自由改编。
3. **生成并试听候选**，满意后明确采用到编辑器。
4. **编辑乐谱**，调整音符、声部和歌词对应；也可以用自然语言提出整曲修改要求。
5. **保存歌曲**，生成器乐音频或中文演唱，试听后导出 MIDI、WAV、MP3。

有歌词时，默认一次使用全部非空歌词行，按全文安排时长，上限 64 行、1024 字词。无歌词时先生成 60 秒短曲，人工审核满意后再明确发起 3 分 15 秒整曲扩展。

“乐句与转音（v2试验）”提供乐句级生成及按字音符组编辑，包括添加转音、简化唱法和时长调整。该入口与默认全文谱曲分开，音乐效果需结合实际试听判断。

### 创作键位

创作使用独立映射，与练习模式不同。旋律乐器按半音顺序使用：

```text
A W S E D F T G Y H U J K
```

其中 `A S D F G H J K` 为白键，`W E T Y U` 为黑键，具体音高以屏幕琴键为准。鼓模式使用对应打击乐音色。文本输入时不会触发演奏。

### 演唱与导出

采用并保存全文歌词作品后，在“保存与导出”选择歌手并生成演唱。可为多音字填写无声调拼音修正，单听人声、伴奏或合听。

当前演唱使用普通话汉字歌词；英文、数字或缺少音符对应的字词会显示具体位置。导出的音频绑定保存版本，修改乐谱后需生成对应版本的新音频。

MIDI 保存可编辑的音符和乐器信息，WAV/MP3 保存渲染后的声音。完整 MIDI 导出保留所有声部；音频可以按所选声部模式渲染。器乐音频在完整乐谱时间轴后追加 3 秒尾音，导出前可试听实际渲染结果。

## 扒谱模式

1. 从导航选择“扒谱”，在上传区选择本地 MP3 或 PCM WAV。每个音频创建独立工程。
2. 填写开始和结束秒数，通过原曲时间标尺核对选段并试听。文件最长10分钟，每次处理0.05–60秒。
3. 独奏钢琴直接转录；混音先分离，点击“载入这次分离试听”，切换原始选段／分离钢琴对照，再明确继续转录。当前混音分离仍有串音风险。
4. 查看处理阶段和音符预览，使用同一片段位置对照 MIDI。可增删音符、修改音高／起止时间／力度和左右手，并撤销、重做或保存修订。
5. 确认 BPM、拍号、第一强拍、调号和量化，保存并生成五线谱，下载整理 MIDI 与 MusicXML。原始转录 MIDI 始终可单独下载。

原始演奏时间与整理拍点分别保存。短音被量化为零、复杂重叠或声部超过支持范围时会提示原因，仍保留演奏 MIDI；程序不会自动删掉这些音。修改后显示的旧谱面标注来源，需再次保存才能更新。

模型补帧产生的片段末尾持续音会在选段终点结束，完全位于选段外的音符不纳入片段；界面显示处理数量，原始转录 MIDI 完整保留，并提供保留片段内乐器／速度／控制信息的“选段 MIDI”。校对和试听使用片段内时间。异常损坏或明显超出模型补帧窗口的输出仍会拒绝。

本地转录并不保证准确，错音、漏音、踏板及分离串音需要试听校对。当前支持恒速多声部整理，左右手各自独立编号，支持每手最多32个声部并显示实际数量；原来的每手2声部限制已解除。音高、起止时间、和弦及重复起音均保留，不自动恢复圆滑线、生成伴奏或整曲改编。“复制到练习／创作”保留扩展位置，尚未开放。使用方法、真实样例及人工检查见[扒谱交付文档](docs/transcription-delivery-20261028.md)，边界与复调修复见[修复说明](docs/transcription-fixes-20261028.md)。

## 文件与数据

| 位置 | 内容 |
| --- | --- |
| `data/<歌曲ID>/` | 歌曲输入、原文件、乐谱版本、生成候选及导出音频。 |
| `data/.trash/<歌曲ID>/` | 删除歌曲的完整工程，可从回收站恢复。 |
| `data/.practice/materials/` | 原始 MIDI 和独立练习材料版本。 |
| `data/.practice/sessions/` | 练习作答与评分记录。 |
| `data/.transcriptions/<扒谱ID>/` | 上传音频、任务、分离结果、原始转录和不可变修订／导出。 |
| `tools/` | 本地音频工具、音色、歌手资源和 SDK。 |
| `.cache/` | 本地构建缓存、测试与评估产物。 |

歌曲显示名与目录 ID 分离，同名作品独立保存。创作采用显式手动保存；练习使用独立材料，不写回来源歌曲。建议定期备份完整 `data/`，同一目录使用一个后端进程。

`.gitignore` 已排除歌曲、密钥、第三方资源、依赖和缓存。源码仓库保留配置模板、依赖清单及锁文件，方便在新机器准备环境。

## 技术与项目结构

| 层次 | 技术 |
| --- | --- |
| 前端 | React、TypeScript、Vite |
| 五线谱 | MusicXML、OpenSheetMusicDisplay |
| 浏览器音频 | SpessaSynth、Web Audio、SoundFont |
| 后端与 MIDI | FastAPI、Pydantic、Mido、Python |
| AI 编曲 | DeepSeek API、结构化音乐规划与程序校验 |
| 器乐渲染 | FluidSynth、FFmpeg |
| 中文演唱 | DiffSinger、OpenUtau Core、C# 无界面宿主 |
| 钢琴扒谱（可选） | Transkun 2.0.1、Demucs 4.0.1、PyTorch CPU |
| 检查 | pytest、Ruff、前端逻辑测试、Playwright |

```text
NeoNote/
├─ backend/
│  ├─ app/                 # 本地 API、歌曲、练习、AI 与音频处理
│  ├─ tests/               # 后端测试
│  └─ vocal-worker/        # C# 演唱工具
├─ frontend/
│  ├─ app/                 # React 正式工作台
│  ├─ prototype/           # 独立早期原型
│  ├─ front_original/      # 视觉重设计静态原型（直接打开 index.html）
│  └─ front_gemini/        # Gemini 暖调琴房与夜色录音棚双主题静态原型（直接打开 index.html）
├─ scripts/                # 安装、启动、构建与检查脚本
├─ docs/                   # 技术设计、使用细节与交付记录
├─ data/                   # 本地歌曲与练习记录（运行时生成）
├─ tools/                  # 本地第三方资源（另行准备）
├─ .env.example            # 无密钥配置模板
├─ requirements.txt        # Python 运行依赖
├─ requirements-dev.txt    # Python 开发依赖
└─ 启动工作台.cmd          # Windows 启动入口
```

## 开发与检查

在两个终端分别启动后端和前端：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_dev.ps1 -Service backend
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_dev.ps1 -Service frontend
```

开发页面为 `http://127.0.0.1:5173`，前端将 `/api` 代理到后端 8000 端口。

运行后端检查、前端构建与逻辑测试：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev_check.ps1
```

浏览器测试、资源相关检查与隔离验收方式见各模块文档。真实模型调用和歌声推理需单独准备对应资源及运行条件。

## 常见问题

| 情况 | 处理方式 |
| --- | --- |
| 找不到 Conda 或 `piano` | 在能够使用 Conda 的终端中启动，并确认 `piano` 环境存在。 |
| 页面能打开但没有声音 | 检查 SoundFont 路径，点击页面完成音频初始化，等待音色就绪。 |
| 无法导出 MP3 | 运行 `scripts/check_audio.py`，检查 FluidSynth、FFmpeg 和 FFprobe。 |
| AI 一直显示模拟结果 | 检查本地 `MODEL_MOCK`；切换真实模式并配置密钥后重启后端。 |
| MIDI 导入或整理失败 | 根据页面错误检查格式、控制事件、时值及音域；保留原 MIDI 后再调整材料。 |
| 歌声生成不可用 | 检查本地歌手资源、全文歌词对应和普通话字词提示。 |
| 保存出现版本冲突 | 保留当前草稿，重新加载最新版本后再处理，避免多页面互相覆盖。 |

MIDI 支持 Type 0/1 的 PPQ 时间基准，单文件约 2 MiB、单曲最多 5000 音符 / 10 分钟。创作导入与练习整理采用不同规则；踏板、弯音等事件的具体处理见模块文档。

## 更多文档

- [项目介绍与面试准备：AI 编曲、扒谱流程及技术问答](docs/interview-preparation.md)
- [乐曲键盘扒谱：设计与实现边界](docs/v3-keyboard-transcription-plan.md) · [准备、使用与验收](docs/transcription-delivery-20261028.md)
- [整体功能设计](docs/project-plan.md)与[技术架构](docs/technical-stack.md)
- [识谱练习：设计与判定规则](docs/practice-design-20261013.md) · [使用细节与支持范围](docs/practice-delivery-20261013.md)
- [本地基础单元与练习谱](docs/practice-units-delivery-20261014.md)
- [模型接入与配置](docs/model-integration.md) · [全文歌词谱曲](docs/lyrics-rewrite-20261006.md)
- [乐句与转音试验功能](docs/lyric-phrases-delivery-20261012.md)
- [音色与音频渲染](docs/stage2.md) · [中文演唱与资源准备](docs/vocal-delivery-20261010.md)
- [歌曲回收站](docs/song-deletion-20261006.md)
- [正式工作台的视觉参考](frontend/front_original/README.md) · [Gemini 视觉原型](frontend/front_gemini/README.md)

## 第三方资源

音色、声库、声码器和引擎分别遵循其许可证。中文演唱当前资源组合用于非商业演示，公开作品的署名要求见[演唱资源说明](docs/vocal-delivery-20261010.md)。OpenSheetMusicDisplay 的 [BSD-3-Clause 许可](docs/licenses/osmd-BSD-3-Clause.txt) 随源码保留。
