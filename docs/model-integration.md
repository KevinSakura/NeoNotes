# 模型调用与环境配置设计

2026-10-06最新歌词合同：有歌词直接使用框内全文，64行/1024字词，不先生成60秒选段。DeepSeek按句返回旋律型、句尾收束、和声与音色，程序建立字词/实际音符对应、换气及四声部伴奏，按实际编配确定时长；不要求模型生成全局note_indices。无歌词及旧歌曲仍保留事件合同与历史读取。全文谱曲/修改单次32768输出token，两次真实技术验收均通过，合计25106 token，音乐听感与可唱性待人工确认，详见[重写交付](lyrics-rewrite-20261006.md)。以下第六阶段初交付与前期核查为历史说明，不能将其零调用或旧60秒边界用于当前歌词入口。

初始资料核查日期：2026-10-04；实现状态更新：2026-10-06。第六阶段已接入人工审核后明确发起的整曲：以短曲旋律/风格为参考重排段落、TXT全部歌词按歌词安排时长，无歌词195秒；整曲生成和修改单次32768输出token，详情见[第六阶段交付](stage6.md)。新增真实调用0次，新整曲提示与音乐效果尚未真实验收。第三阶段原3次预算已用完，文字/歌词结构与渲染通过、保留旋律失败；第五阶段实际2次分别因结尾/换气失败，依赖修改跳过，旧记录完整保留。程序固定原演奏、逐句试唱和版本核对继续有效。本文Qwen、专用音乐模型及音频API仍是后续设计，不自动切换或调用。

## 本轮优先选择

| 方案 | 建议定位 | 选择依据与限制 |
| --- | --- | --- |
| DeepSeek `deepseek-flash` | 已接入主要路线，使用 JSON Object | 本轮3次实际响应均返回此模型名；只验证有限样例的结构与链路，不证明音乐效果；不设跨服务商回退。deepseek-v4-pro可配置但未实测。 |
| Qwen `qwen3.8-max` | 后续可选同输入对照，尚无适配器 | 官方文档列明JSON Schema；只有后续明确选择并获授权时再实现，不是主路线依赖，未验证音乐效果、账号权限、耗时或费用。 |
| 专用 MIDI 模型 | 前两者音乐质量不达标时评估 | 需实测歌词条件、旋律条件、四声部和部署要求；现在不选定具体模型，也不要求本地 GPU。 |
| Fun-Music 等音频接口 | 不纳入首版 MIDI 主链路 | 音频输出不能代替可编辑的音符、声部和歌词对应。 |

依据：[Qwen 结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)、[DeepSeek 模型能力](https://api-docs.deepseek.com/quick_start/pricing/)。2026-10-05 再次核对 DeepSeek 官方模型页及 [JSON 指南](https://api-docs.deepseek.com/guides/json_mode/)，当前 `deepseek-flash`、基础地址与 JSON Object 配置仍有官方依据；不据此声称账号权限、实际调用或音乐效果已验证，不加入未核对的 strict Schema 参数。

已用HTTPX实现DeepSeek聊天适配：`response_format={"type":"json_object"}`、`thinking={"type":"disabled"}`、非流式单次请求及输出上限，消息明确要求完整JSON；不执行工具或模型代码。参数按[官方聊天接口](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)核对。Qwen将来须另设适配器转换Schema，不能混用两家选项，也不能只改.env名称就调用。

先让模型输出共同编曲规划、结构化音符及歌词对应，再由确定性程序校验和生成 MIDI；必要时再加入有限音乐工具。不要让模型输出 Base64 MIDI。检查拒绝、空响应、截断和结构错误，失败保留原作品并提示用户，不能以“返回了文本”标记生成成功。

## 推荐先验证的路线

首版可先验证“通用 LLM 输出结构化编曲数据／调用音乐工具 → 平台校验 → 多轨 MIDI → 本地音色渲染 → MP3”。这是一条候选方案，不承诺通用模型能稳定产出可唱且优质的 60 秒作品。

DeepSeek 支持 JSON 输出和工具调用；工具需要由平台执行，模型只返回调用意图。JSON 合法不等于音乐内容正确。[JSON 文档](https://api-docs.deepseek.com/guides/json_mode/)、[工具调用文档](https://api-docs.deepseek.com/guides/tool_calls/)

Qwen 官方结构化输出文档列有 Qwen3.8-Max、Qwen3.8-Flash，并给出 qwen3.8-max 的调用示例。使用真实模型 ID，不能直接把系列名 qwen3.8 当作已验证的 API 模型名。[官方文档](https://help.aliyun.com/zh/model-studio/qwen-structured-output)

Fun-Music 当前接口输出 MP3/WAV，未列出 MIDI、多轨音符或歌词—音符对齐结果；is_instrumental=true 时 lyrics 参数无效，同时提供 lyrics 与 prompt 时 prompt 被忽略。因此不能仅替换模型名称就满足可编辑四声部、歌词可唱、无人声试听及独立情感控制的要求。该判断基于当前公开接口，不是对全部音乐模型能力的判断。[官方 API](https://help.aliyun.com/zh/model-studio/fun-music-api)

若通用 LLM 路线质量不达标，评估支持符号音乐／MIDI 输出及旋律或歌词条件的专用模型。音频生成服务可留作后续独立扩展，不作为 MIDI 编辑流程的替代。

## 当前代码职责

1. 前端先保存输入，再提交歌曲ID、基础版本、生成/整曲修改操作与单次付费确认；后端从保存版本整理输入，前端不持有API Key。
2. 后端model_settings.py按需读取根目录.env的MODEL_*并缓存，DeepSeekAdapter为唯一真实适配器。状态接口允许列表与安全错误不显示密钥/原始配置，其他provider的真实请求拒绝。
3. 创作模块整理歌词优先、情感其次、旋律走向参考的约束，生成共同段落和配器计划，再生成四声部数据与歌词对应信息。
4. DeepSeek使用BASE_URL后的/chat/completions，单任务最多1次，无重试、重定向或服务商切换。空JSON、重复键、NaN、截断、拒绝、密钥回显及大于2MiB响应拒绝；仅保存允许的返回模型、ID和usage。
5. 适配器处理模型支持的参数差异和响应字段，不能认为所有兼容接口都支持同一套思考模式、结构化输出或工具参数。
6. 校验模块检查完整响应、字段类型、音符范围、时长、声部乐器及歌词对应；歌词可唱性与音乐质量仍须人工评审。不得把普通文字回答或截断数据直接当作成功的编曲。
7. 平台将合格数据转为 MIDI，再通过音色渲染及音频编码生成 MP3，保存到 data/歌曲目录。60 秒约束应在时间轴和导出检查中落实，不能仅依靠提示词。
8. 自然语言修改提供当前工程音乐数据与原始约束，产出新版本，校验并保存修改历史，支持撤销。短曲审核不通过时保留当前版本，不自动推进全曲。

当前合同由backend/app/composition.py的MusicPlan定义：BPM、调性、连续段落和和声、四类轨道及`[开始拍,音高,持续拍,力度]`事件、歌词行号/字词号到键盘事件索引。编译后由models.py的Composition保存段落、和声与音符ID对应；Mido生成MIDI，不让模型直接编码文件。保留旋律精度、输入上限及质量边界见stage3.md。

自然语言创作要求和 60 秒等业务规则应在工程数据与后端约束中保存，不需要全部塞入 .env。音色资源也不是 API Key 提供的，需要单独配置和加载。

## .env 使用方式

.env.example是无密钥模板，已有.env不覆盖。第三阶段只有后端按需读取根目录.env的MODEL_*；修改后重启，前端状态/编辑不会发起生成。APP_*、工具路径与AUDIO_*未加载，实际工具路径仍固定在resources.py。MODEL_*是项目约定，不是服务商规定的名称。

第三阶段可先实现适配器、约束校验和模拟测试，不消耗 API 额度；实际验证 DeepSeek 是否能满足主题、情感、旋律约束与歌词可唱性，则需要真实 API 请求，并另行取得用户授权和调用预算。官方按输入/输出 token 计费，从充值或赠送余额扣除，不能以模拟结果当作模型质量验收。[DeepSeek 计费说明](https://api-docs.deepseek.com/quick_start/pricing/)

### DeepSeek 示例

```dotenv
APP_DATA_DIR=./data
MODEL_MOCK=true
MODEL_PROVIDER=deepseek
MODEL_BASE_URL=https://api.deepseek.com
MODEL_API_KEY=填写自己的DeepSeek密钥
MODEL_NAME=deepseek-flash
MODEL_OUTPUT_MODE=json_object
MODEL_TIMEOUT_SECONDS=180
MODEL_MAX_RETRIES=0
MODEL_MAX_TOKENS=32768
```

地址和模型名依据核查时的[官方模型文档](https://api-docs.deepseek.com/quick_start/pricing/)，后续以账号权限和官方列表为准。

### Qwen 北京地域示例（后续设计，当前不可调用）

```dotenv
APP_DATA_DIR=./data
MODEL_MOCK=true
MODEL_PROVIDER=qwen
MODEL_BASE_URL=https://YOUR_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
MODEL_API_KEY=填写对应地域和业务空间的百炼密钥
MODEL_NAME=qwen3.8-max
MODEL_OUTPUT_MODE=json_schema
```

YOUR_WORKSPACE_ID须换成实际业务空间ID，地域、密钥和模型权限匹配。设计来源为[Qwen官方文档](https://help.aliyun.com/zh/model-studio/qwen-structured-output)；当前还需独立实现适配器与测试，不能切换这些字段当作已有功能。不要保留重复键。

### 本地配置补充

模板保留MODEL_MOCK=true与空密钥；模拟结果明确标示，不读取真实配置的隔离测试不会发API请求。真实使用需用户自己改为false、填写密钥及在页面确认本次发送资料和付费。开发助手额外验收须单独授权；本轮3次上限已用完，失败也计入。

超时默认180秒（10–300可配置）、重试仅0、输出默认32768token。既有.env不改写：短曲仍使用原MODEL_MAX_TOKENS，整曲生成及整曲修改固定32768。每个任务记录状态、实际请求数、输出上限、返回模型/请求ID及可确认usage；限流、网络错误或服务重启不会重发。未知用量不当作0，失败也可能计费，费用以账单为准。旧验收脚本支持--max-output-tokens 12000或32768（默认32768，与配置取较小值），新真实测试仍须独立授权预算，不能沿用已结束的旧轮次。

`APP_HOST=127.0.0.1`、`APP_PORT=8000` 定义未来配置中的本地监听地址；`FFMPEG_PATH`、`FFPROBE_PATH`、`FLUIDSYNTH_PATH` 已填入现有 tools 文件路径。`SOUNDFONT_PATH` 已填为 `./tools/soundfonts/GeneralUser-GS-main/GeneralUser-GS.sf2`，此文件在第二阶段已用于浏览器试听与后端渲染，无需重新下载。上述变量当前尚未读取，未来须按项目根目录解析；空密钥/占位域名应阻止真实请求，音色缺失只阻止音频功能，不应阻止编辑或 MIDI 保存。

### 后续音频服务预留

AUDIO_PROVIDER、AUDIO_API_URL、AUDIO_API_KEY、AUDIO_MODEL_NAME 留空即可，不是首版通用 LLM 编曲的必需配置。若将来采用 Fun-Music，需单独实现音频服务适配器；它使用完整音乐生成端点和 model/input 请求体，不走聊天的 messages 请求格式。不能只将 MODEL_NAME 改为 fun-music-v1。

## 验证与记录

在实际选型阶段，先确认账号权限及结构化输出，再用相同的歌词选段、旋律片段和情感描述比较候选模型。检查 60 秒、四声部、MIDI 往返、歌词试唱及自然语言修改是否达标，并记录等待时间和实际用量。不得把格式检查通过等同于音乐验收通过。

实际.env仅供后端读取，不能发送给浏览器、写入歌曲历史或记录密钥到日志。安全记录可保存provider、返回model、所选创作输入及已通过检查的结果。配置、HTTP、数据校验、MIDI和渲染依赖已安装于piano并记录在requirements；本轮无需额外安装。实际调用结果、剩余风险及2首试听音频位置见stage3.md，成功返回JSON不等于音乐质量或歌词可唱性验收。
