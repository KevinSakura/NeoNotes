# 2026-10-05 离线复验与历史真实作品检查

用户要求先继续能够完成的复验，再提供人工检查清单。本轮完成软件回归、六类模拟编曲的完整音频链路，以及两首历史真实模型作品的技术检查。**本轮新增真实模型请求0次，没有读取真实.env、安装依赖或修改功能源代码。第五阶段5C的真实失败记录保持原样，改进后的提示仍未真实复验。**

正式工作台启动方法不变：双击 [启动工作台.cmd](D:/study/AI_prompt_study/piano_keyboard_create/启动工作台.cmd)，访问 http://127.0.0.1:8000。正式歌曲仍保存于 `data/<稳定歌曲ID>/`。本轮验收材料仅保存在 `.cache/`，不会自动进入“我的歌曲”。

## 实际复验结果

| 项目 | 结果与范围 |
| --- | --- |
| 后端回归 | 108项通过，30.93秒；包括结尾/换气错误位置、预检、歌词修复、来源、保存、撤销、MIDI及音频任务 |
| 音乐逻辑 | 10项通过，覆盖试听变速、字词时间、速度变化和无副作用 |
| 浏览器 | 10条通过，运行器显示1.0分钟；隔离服务正常退出。覆盖录制、编辑、历史、音频、候选、修复、慢速循环和停止 |
| Ruff / TypeScript / 前端构建 | 通过；未新增或升级依赖 |
| 六类当前模拟用例 | text、lyrics、preserve、preserve_lyrics、extend_lyrics、modify均完成候选校验、采用到隔离工程及真实SoundFont WAV/MP3渲染；真实请求0次 |
| 历史真实作品 | 第三阶段text和lyrics两首通过当前结构、候选来源、MIDI及已有音频技术复验；没有重新请求模型或重新生成这些历史结果 |
| 独立文件检查 | 六份当前模拟加两份历史真实，共8份通过；音乐质量及可唱性均未评估 |
| 数据与原记录 | 正式歌曲、原型和第三/第五阶段真实记录共90个文件，数量与SHA-256不变；42个功能源文件哈希不变。已有13张阶段截图在复验前另存快照 |

文件复验实际检查了：

- 保存乐谱与候选MIDI的音高、力度、开始/时长、速度图、拍号、轨道名称、通道、Program、Bank、音量及元事件一致；重新导入再导出没有丢失这些信息。ID与应用内静音/独奏状态不作为MIDI等价字段。
- 四个音乐声部仍独立，鼓使用零基通道9；歌词标记数量及其内容、位置与候选元数据一致。有歌词的材料均为14个字词标记，可一字多音。
- 候选来源任务已完成，创作输入与记录一致；音频对应正确的保存版本与完整混音，渲染MIDI和文件字节数/SHA-256正确，音色指纹与当前SoundFont一致。
- 乐谱时间约60秒、WAV时间约63秒；WAV为44.1kHz双声道16-bit PCM、非静音、峰值低于现有渲染上限、削波样本0。8份WAV峰值范围为0.06616至0.08731。
- MP3为44.1kHz双声道，完整解码成功；时长与WAV的差在0.15秒编码填充容差内。没有将MP3与WAV判为逐采样相同，也没有据此判定音质满意。

这些是技术检查。主题表达、声部是否悦耳、是否真正利用输入旋律、修改意图及照词可唱性仍须人工评估。

## 可以立即人工检查的真实材料

两首作品均来自第三阶段原真实请求，采用较早的生成合同。它们能供试听、试唱及技术兼容检查，**不能证明第五阶段改进提示能稳定生成**。此前的失败不被重新归类为成功。

| 材料 | 已核对保存版本 | 试听与下载 |
| --- | --- | --- |
| 历史真实文字创作 | 7eb4ad1551a648cab10c0ce19a8dd29f | [MP3](D:/study/AI_prompt_study/piano_keyboard_create/.cache/model-evaluations/c79aa14d935c4e998ee29bf56112f6ef/data/baaf77f3366e4643adb6a2a2a77a32a0/exports/audio/d9c805b2f12b4b039fb3d958c66a5e9f/mix.mp3) · [WAV](D:/study/AI_prompt_study/piano_keyboard_create/.cache/model-evaluations/c79aa14d935c4e998ee29bf56112f6ef/data/baaf77f3366e4643adb6a2a2a77a32a0/exports/audio/d9c805b2f12b4b039fb3d958c66a5e9f/mix.wav) · [MIDI](D:/study/AI_prompt_study/piano_keyboard_create/.cache/model-evaluations/c79aa14d935c4e998ee29bf56112f6ef/data/baaf77f3366e4643adb6a2a2a77a32a0/jobs/composition/85f606199d884467b792805613dd565d/candidate.mid) |
| 历史真实歌词谱曲 | b5e381a16d3548bb8e69dc557d3170cd | [MP3](D:/study/AI_prompt_study/piano_keyboard_create/.cache/model-evaluations/c79aa14d935c4e998ee29bf56112f6ef/data/15e45ccad3464be1834f6330ae408f12/exports/audio/7904cd7324704910bae65ce70cdc40d2/mix.mp3) · [WAV](D:/study/AI_prompt_study/piano_keyboard_create/.cache/model-evaluations/c79aa14d935c4e998ee29bf56112f6ef/data/15e45ccad3464be1834f6330ae408f12/exports/audio/7904cd7324704910bae65ce70cdc40d2/mix.wav) · [MIDI](D:/study/AI_prompt_study/piano_keyboard_create/.cache/model-evaluations/c79aa14d935c4e998ee29bf56112f6ef/data/15e45ccad3464be1834f6330ae408f12/jobs/composition/b116ed4aea8e4f07b0c140bb7d7394d9/candidate.mid) |

两首的输入主题都是“走过低谷后的重新出发”，希望的音乐感觉都是“积极、温暖的流行曲风，清楚的旋律，轻快但不过度吵闹。”。

真实歌词作品的绑定位置为：

| 行 | 歌词原文 | 原速秒位置 | 已有器乐音符 |
| --- | --- | --- | --- |
| 1 | 把昨日轻轻放下 | 13.75–24.0625 | 7字、8音符 |
| 2 | 让脚步迎向朝阳 | 26.25–40.00 | 7字、8音符 |

第一句前有器乐前奏，两句之间的字词绑定间隔为2.1875秒；这只是测得的结构事实，不代表呼吸自然或试唱已通过。原作品不含合成人声。

当前正式歌曲列表不会显示这些隔离作品，可直接打开上述音乐文件。可用时间显示定位每句；若使用支持0.75倍且保持音高的播放器，先慢速熟悉，最后仍以1倍试唱判断。不要把单纯降低音高的播放方式当作本项目的慢速试听。

## 人工清单与达成标准

完整清单、当前适用项目和填写位置见 [人工验收清单](manual-acceptance.md)。当前两首真实作品可以检查旋律、声部、曲风、歌词试唱和最终导出声音。它们没有输入演奏片段，也不是整曲修改作品，因此不能用于验证原旋律利用或修改要求。

原旋律保留、同输入自由改编和整曲修改的真实效果仍待新的合格真实候选。六类模拟音频可以用于熟悉操作和验证本地声音链路，不能代替这些真实效果验收。

## 证据与复现

- [本轮汇总](D:/study/AI_prompt_study/piano_keyboard_create/.cache/rechecks/20261005-32a3823bf52e48ffbde3d72a4bf2bd18/summary.json)及[逐文件检查](D:/study/AI_prompt_study/piano_keyboard_create/.cache/rechecks/20261005-32a3823bf52e48ffbde3d72a4bf2bd18/artifact-audit.json)。
- 同目录保留dev-check.log、browser.log、mock-evaluation.log、artifact-audit.log、只读审计脚本、原始文件/源代码指纹与旧截图副本；浏览器输出使用本轮独立目录。
- 新模拟记录：`.cache/model-evaluations/7e4a6055d2344a468780def5570a4d32/summary.json`。
- 历史真实记录：`.cache/model-evaluations/c79aa14d935c4e998ee29bf56112f6ef/summary.json`。
- 第五阶段真实失败记录：`.cache/model-evaluations/b33a7d1c0bbb498990223dc3df1894c9/summary.json`，未更改。

本轮使用现有开发检查与浏览器验收脚本，并运行：

```powershell
conda run -n piano python scripts/evaluate_model.py --mock --cases text lyrics preserve preserve_lyrics extend_lyrics modify
```

模拟复现会创建新的隔离目录，不能通过旧目录存在就认定新运行通过。正式数据不迁移，人工表不会自动改变软件审核。

## 尚未完成

新的提示没有真实验证；没有新增真实保留旋律、自由改编或整曲修改结果。人工音乐效果、原速可唱性及设备听感未评估，不能将本轮通过写成第五阶段5C通过或平台总体稳定。

仍有原测试客户端弃用提示和浏览器颜色环境提示，本轮均退出成功；没有为消除提示安装依赖。全曲、人声、局部AI修改和单声部重生成未实现。本轮结束后等待用户下一步指令，新的真实请求需另有明确预算授权。

