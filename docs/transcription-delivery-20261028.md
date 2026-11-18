# 乐曲键盘扒谱交付

日期：2026-10-28。适用本地非训练工作流；前述方案中“待实施”的范围已由本轮明确授权覆盖。本文件区分软件功能、真实推理与人工听感。

本日后续已修复模型补帧越界导致整次拒绝、以及每手2声部硬限制；新增选段MIDI、边界诊断和v2多声部编号。下文首次交付的失败和验收结论按当时事实保留；当前行为及复验见[边界与复调修复](transcription-fixes-20261028.md)。

## 启动与资源准备

本机可选依赖和官方资源已安装准备。重启正在运行的后端，双击根目录“启动工作台.cmd”，浏览器刷新，顶部出现“创作／练习／扒谱”。现有歌曲、练习和人声资源继续原路径使用。

新机器先按README准备Conda piano、前端、FFmpeg和SoundFont，再显式运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_transcription.ps1 -PrepareModels
```

手动分步：

```powershell
conda run -n piano python -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu
conda run -n piano python -m pip install -r requirements-transcription.txt
conda run -n piano python scripts/prepare_transcription.py
conda run -n piano python -m pip check
```

安装脚本先检查dry-run计划，任何已有包降级都拒绝继续。正式核心依赖仍在requirements.txt，可选推理依赖在requirements-transcription.txt；不需要真实.env或API密钥。页面、启动和状态接口都不会自动下载。

本机Python3.12.15，新增torch2.8.0+cpu、torchaudio2.8.0+cpu、Demucs4.0.1、SoundFile0.13.1、NumPy2.5.4、SciPy1.18.1、pretty_midi0.2.11.post0等依赖；前后包版本比较没有任何已有包变化，pip check通过。未创建额外环境，未安装Python歌声包。

完整Transkun pip安装因训练用ncls缺Windows C++编译器失败，没有擅自安装编译器、降级Python或Torch。随后核对官方Data.py局部导入与推理调用：使用固定2.0.1 wheel中未修改的源码和权重，安装实际推理依赖，不将Transkun作为完整训练包安装。因此不要直接pip install transkun来复现本机方案，使用本项目准备脚本。

Demucs4.0.1的旧加载接口不适配PyTorch2.6后的默认weights_only；宿主只对已核验官方权重显式使用weights_only=False，再把官方checkpoint字典交给load_model。未修改第三方源码、未开放上传权重执行。

## 来源、许可与固定版本

| 资源 | 官方来源与选择 | 使用条件与限制 |
| --- | --- | --- |
| Transkun2.0.1 | [官方仓库](https://github.com/Yujia-Yan/Transkun)、[PyPI2.0.1](https://pypi.org/project/transkun/2.0.1/)的原始wheel；V2 No Pedal Ext | 仓库和wheel含MIT许可，官方包未另列权重限制；没有下载训练集，不将训练数据使用条件推广成任意数据再分发许可。保留包内LICENSE。音符结束采用非踏板延长约定，模型仍可能预测踏板控制。 |
| Demucs4.0.1／htdemucs_6s | [官方仓库](https://github.com/facebookresearch/demucs)、[权重索引](https://github.com/facebookresearch/demucs/blob/main/demucs/remote/files.txt)、[官方权重](https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/5c90dfd2-34c22ccb.th) | [MIT](https://github.com/facebookresearch/demucs/blob/main/LICENSE)覆盖官方发布代码/模型，官方无额外权重许可声明。确实输出piano，不是无主唱伴奏；官方明确警告piano串音/伪影。 |
| python-audio-separator | [官方实现](https://github.com/nomadkaraoke/python-audio-separator)已复核 | MIT包装器不替代每个权重的许可。当前安装增加samplerate构建及ONNX相关组件，本轮直接接入Demucs，未安装整个包装器、未展开多模型评测。 |
| PyTorch2.8 CPU、torchaudio2.8 | [官方wheel源](https://download.pytorch.org/whl/cpu) | 包内许可保留；CPU路径已实测，不承诺CUDA兼容或耗时。 |

资源放在tools/transcription，原wheel、源码、配置、权重、manifest.json保留；代码和准备脚本在源码仓库，模型和原音频被.gitignore排除。模型/配置/官方源码逐文件SHA256，在推理前检查；准备清单变化拒绝继续原任务。

| 文件 | SHA-256 |
| --- | --- |
| transkun-2.0.1-py3-none-any.whl | `7d7f5bf88b0d21f7da0415b089cff3bcbcbdda04e1a3c9719964947942c859fe` |
| 2.0.pt | `50a80010effc2a59ffcd068a95cd2b29bd7f23a27a3515bc3ccd209c89a3d44c` |
| 2.0.conf | `d3d989214eb148230ee5df476d994dcde6af595904d3f968f1221d2e3bea5ac6` |
| 5c90dfd2-34c22ccb.th | `34c22ccb381c6f9fdbf324f04e1e2fe21aaaf293f5ded163a162697ff9a02ddd` |

独立模型权重约111.4MB；另保留原包和源码副本，磁盘占用更大。没有读取/修改/展示真实.env，没有付费API或歌声推理，没有改变现有歌手资源。

## 如何导入第一首歌

1. 选择“扒谱”，上传MP3/WAV。本机Canon文件可直接选择，工程新建于data/.transcriptions；无需先创建歌曲或导入练习。
2. 设置开始/结束秒数，先从选段起点试听。最多100MiB、10分钟、单双声道、8–192kHz，WAV首版为常见PCM。片段0.05–60秒。
3. 明确是独奏钢琴时选独奏；混音选择“混音（试验）”。开始后显示解码、分离或转录阶段，可取消，没有虚构百分比。
4. 分离完成后点“载入这次分离试听”，在“对照声音”切换原始选段／分离钢琴，共用片段秒数定位。若钢琴缺失、贝斯/吉他/主唱串入明显，取消，不继续把它当独立键盘。合适才点“我已试听，继续转录”。
5. 选择完整结果，对照原始片段、分离键盘、SoundFont MIDI；滑块显示片段和原曲秒数。MIDI试听是当前校对的器乐音符，不是重新合成原录音。
6. 表格可修改MIDI音高、开始、结束、力度和左右手，或添加/删除，支持撤销/重做。时长等于结束减开始。点“保存校对修订”创建新不可变版本；重新打开可选择旧结果和修订。跨页面保存冲突明确拒绝，切换工程/分离试听提示未保存草稿。
7. 确认BPM、拍号、第一强拍、量化和调号。正第一强拍表示弱起结束，负数表示第一强拍早于片段。点“确认参数，保存并生成五线谱”。左右手按连续性和音区给建议，可逐音改，不按固定中央C切分。
8. 下载原始转录MIDI、当前修订的演奏MIDI、可用时的整理MIDI/MusicXML。整理失败仍保存校对和演奏MIDI，显示具体原因，可点“定位问题音符”。草稿不会被旧谱面冒充；旧版本下载始终绑定原版本。

新手可先用4–8秒独奏片段；第一强拍和节拍需要自行听节奏确认，120BPM默认值不是识别结果。粗量化产生零时长会被阻止，先改细网格或试听校对短音，不自动删除它们。

## 保存和任务行为

`data/.transcriptions/<稳定ID>/source/`保留原文件；`jobs/`保存任务参数、状态、时间、模型/权重/代码指纹和本地日志；`results/`保存完整片段、piano.wav（混音）、原始performance.mid、音符/控制事件与来源；`revisions/`保存校对、设置、派生文件和清单。导出文件就在不可变版本中，不通过公用可覆盖下载文件替换。

任务全局最多1个、CPU4线程，分离试听等待释放计算资源。Windows Job Object保护子进程生命周期，取消回收后才放开并发。进程每步30分钟上限。重启标未完成任务中断，不自动续算；重试需重新点开始并创建新任务ID。等试听项可取消。失败或取消不覆盖旧成功；完整校验后才发布，下载校验文件哈希。保存使用当前修订冲突检查；原音频和原始MIDI不被编辑。

新目录不会进入歌曲列表，也不使用歌曲回收站；本次没有扒谱工程删除。切换入口保留创作草稿，停止旧音频和键盘发声。复制到练习/创作为未开放扩展标识，未做跨模块数据写入。

## 软件验证

证据根目录：`.cache/transcription-checks/20261028/`。以下均使用隔离数据和假模型配置；浏览器扒谱模拟适配器只注入测试启动脚本，生产无模拟推理入口。

- 338项后端检查通过（14项新增扒谱检查），32项现有前端逻辑检查通过。
- 33条浏览器流程通过，明确排除`real singer generation`。覆盖创建/练习原流程、扒谱上传、模拟独奏、分离等待/取消/明确继续、增删/撤销、保存、谱面音头映射、下载、刷新重开及窄屏。最后试听清理变更又通过2条扒谱浏览器复验（browser-last.log）；随后将任务恢复限定在实际应用lifespan启动，避免导入main触碰正式任务，14条扒谱后端复验通过（backend-lifecycle.log）。次数不重复计入338/33。
- TypeScript/Vite构建与相关Ruff通过，pip check通过。保留已有OSMD块超过500kB的构建提示及Starlette测试客户端弃用提示，无额外依赖升级来消除提示。
- 最后浏览器服务关停出现Windows连接已关闭的10054及graceful-shutdown提示（取消0个运行任务），随后应用完成shutdown、进程以0退出；测试通过，不将关停日志描述为完全无告警。
- 新增测试覆盖坏格式、片段限额、路径保护、重复同音、和弦、全音域21–108、跨小节延音线、弱起、负第一强拍映射、量化失败保留原MIDI、版本冲突、并发、重启中断、缺资源、损坏下载和真实休眠子进程取消回收。
- 原有data及frontend/prototype共868个文件，路径集合和SHA256全部不变；检查结果在protected-verification.json。已有Python包版本变化为空。历史失败和真实验收文件没有被覆盖。

## 真实推理材料与结果

使用本机真实CPU模型，非假JSON或假音频。以下“继续”在隔离脚本中是技术流程继续，不代表人工试听确认。没有做基线平台或排行榜。

| 材料与隔离目录 | 真实输出 | 实际结论 |
| --- | --- | --- |
| 原创SoundFont钢琴，8秒，14个参考音符；`.cache/transcription-checks/real-20261028-143921` | 独奏16音符，约23.2秒；原始MIDI与回放WAV保存 | 14个参考起音均有对应，另多出2个短八度76音符。默认量化因此拒绝零时长，未自动删音。不能记为完全准确。 |
| 同源钢琴＋电贝斯自渲染混音，同目录 | 分离＋转录27音符，约26.7秒，整理文件可生成 | 贝斯起音明显串入转录，分离质量未通过；试验流程可运行，不是可靠独立键盘提取验收。 |
| 用户提供Canon MP3的0–20秒；`.cache/transcription-checks/user-file-20261028-144159` | 分离＋转录63音符，约47.6秒；原始/校对MIDI、人声以外的分离WAV、MIDI回放WAV保存 | 默认量化遇短音，成谱被阻止；原始MIDI可用，需人工校对。来源编配和准确性未人工确认，不据此宣称混音提取准确。 |

主观听感、力度自然度和踏板同步均未评估。不是“Canon的正确完整原谱”，也未识别全曲速度。真实范例原始时间、波形和MIDI通过完整文件解析和FluidSynth实际回放渲染，生成成功只代表链路能运行。

保留早期检查：`real-20261028-143322`包含Demucs旧加载接口失败；`real-20261028-143538`为较短内部窗口检查，最终恢复官方16秒/8秒窗口，避免明显漏音。旧失败未改写。实际本轮累计6次Transkun、4次Demucs本地推理调用（含一次Demucs权重加载失败），均无付费请求；不是6首已验收作品。官方窗口与参数已固定并写入后续任务记录。

另在`.cache/transcription-checks/20261028/correction-demo/`新副本演示校对：依据原创参考明确删除两处错误76音符，保留14音符并成功生成整理MIDI/MusicXML。它是脚本模拟人工校对操作，不是产品自动筛音或人工听感通过；原始16音符结果未改动。

## 需要人工试听确认

建议在平台导入用户提供的Canon文件，先试0–20秒，按以下顺序听；隔离结果也可直接听其results中的clip.wav、piano.wav及根目录midi-preview.wav。

| 检查 | 达成标准 |
| --- | --- |
| 钢琴分离 | 原曲键盘旋律/和弦清楚保留，贝斯、吉他、人声等串入不妨碍判断；不满足则停在试听，不采用转录作键盘谱。 |
| 错音漏音 | 逐句对比原音和MIDI，明显起音、重复同音、和弦构成一致；错音/漏音通过校对处理，而非只看音符数量。 |
| 同步和尾音 | 同一定位点下起音没有持续固定错位，长音、休止、踏板释放可接受；识别音符松键不等于踏板尾声。 |
| 节拍及弱起 | 根据听到的节拍确认BPM、拍号、第一强拍；小节重音和弱起位置合理，量化偏移可接受。 |
| 校对保存 | 增删/改时长后试听一致，撤销/重做符合预期，保存重开不丢音；旧版本下载仍保持原内容。 |
| 乐谱可读性 | 左右手/声部分配合理，重复音独立起音、持续音跨小节有延音线；没有自动编造圆滑线。 |

## 明确限制和后续范围

混音分离目前有已知串音，属于试验，不能宣称键盘提取质量完善。独奏也有短八度误报，必须校对。当前是钢琴模型，电钢琴/合成器/其他键盘音色效果未知。

初交付恒速整理曾限每手最多2声部，后续修复已扩至32并记录/显示实际声部数；不做自动节拍识别、自由速度/复杂变速图、专业复杂记谱、PDF、跨模块复制、删除或自动伴奏。波形拖选未实施，使用秒数与音频控件选段。不会将不支持的记谱伪造为成功，原始演奏MIDI继续保留。

踏板在原始MIDI和事件诊断中保留；当前SoundFont对照和校对/整理MIDI只使用音符，不声称完全保留踏板音效。尚未建立硬件/长负载/多机器耗时结论，真实键盘和声卡延迟不在此模块自动验收范围。
