# 本地分镜工作台接口

代码依据：软件根目录的server.py、workspaces.py、creator.py、workflow.py、multimodal.py、audio_service.py、gateway.py与studio.py。当前工作台版本0.10；Design内部合同来自当前安装客户端3.0.18.74，可能随客户端更新而变化。旧版本记录保留在后文，不代表当前版本。

## 0.3 workspace、剧本与声音

每个项目请求应带`X-Studio-Workspace: ID`；CLI在命令之前传`--workspace ID`。未指定时使用注册表的活动项目，仅为兼容旧客户端，不适合多窗口/自动化。创建请求和所有写请求仍需`X-Studio-Request: 1`与JSON。工作区选择在每个请求和后台任务创建时固定，不用切换全局文件路径。

| 方法与路径 | 用途 |
|---|---|
| GET /api/workspaces | activeWorkspaceId、未归档workspaces及archived列表 |
| POST /api/workspaces | `{name,script?,scriptPath?,workspacePath?,modelId?,activate?}`；默认创建空项目并激活；自定义目录必须为空 |
| POST /api/workspaces/{id}/activate | 设置默认活动项目，已打开的客户端仍可用header锁定自己的项目 |
| PATCH /api/workspaces/{id} | `{name?,archived?}`；归档保留文件 |
| GET /api/script | 原文、稳定段落id、行号、场次、shotIds关联 |
| POST /api/script/import | `{text,filename?,revision?}`导入原文，保留已有镜头供审阅 |
| POST /api/shots | `{title,sourceBlockIds,sceneId?,modelId?,...}`从段落创建空白待细化镜头并建立图节点 |
| PATCH /api/shots/{id} | 镜头工作字段、bindings、audioPlan；revision为project.revision；同步对应图节点 |
| POST /api/shots/batch | `{revision,patches:[{shotId,...}]}`；全批预检通过才保存，失败不保存前半批 |
| GET /api/shots/{id}/review | shot、scriptBlocks、assets、bindings、audioPlan、readiness、projectRevision和workflowRevision |
| PUT /api/shots/{id}/review | `{revision?,status,notes}`；status=approved/changes_requested/unreviewed，自动区分方案和当前媒体版本 |
| POST /api/assets/upload | `{name,contentBase64,kind?,role?}`；最大64MB，返回单asset，默认为candidate |
| POST /api/audio/voice-design | `{name,characterId?,description,previewText,confirmed:true,requestId}`；异步本地job，试听≤500字符 |
| POST /api/audio/speech | `{shotId?,cueId?,voiceAssetId,text,modelId,params,confirmed:true,requestId}`；语音模型实际目录取speech，声线取当前项目资产vendorVoiceId |

模型目录现在返回image/video/speech。video条目额外有referenceCapabilities，区分真实已适配能力与上游目录的理论能力；unsupported引用不能静默转成无参考生成。音频资产由ffprobe探测真实时长，波形由实际PCM计算，不是装饰随机线。

媒体URL为`/w/{workspaceId}/media/...`，每个项目有自己的media与台账。同名文件或相同requestId在不同项目不互相覆盖。归档并不删除后台任务或文件。软件工作区隔离数据；模型仍使用当前Design正常登录/计费归属，并不是为每个workspace创建独立云账号。

`bindings=[{assetId,alias,role,usage,enabled,order?}]`，usage可为model_reference、voice_identity、post_mix、context。`@{assetId}`和唯一的`@{alias}`会展开为模型真正接受的编号，媒体各自编号并与实际路径顺序一致。别名歧义/未连接/停用/不支持的媒体都报错。voice_identity与post_mix默认不发送给视频模型，所以不能在生成prompt里假装它们是已经传入的模型条件。

多模态请求把图片放imagePaths，音频/视频放audioPaths/videoPaths；提交前重新探测文件与模型限制，再由适配器编码为Design内部参数。外部不能直接塞params.reference_audios/reference_videos等未绑定路径来跳过校验。Seedance显式携带选定model_name，避免落入供应商默认模型。H3桌面层与开源H3原始IR是不同协议层，不混用token语法。

声音状态：角色试听→选音→逐句表演→音轨审核→混音/口型→媒体版本验收。试听资产role=voice_identity；合成台词role=dialogue_performance，默认candidate并绑定cueId，不会覆盖shot的视频mediaUrl。声线candidate可用于明确选择的草稿合成，blocked/missing不可用。当前voice_id型TTS只适配Speech 2.8，H3 Audio/SeedAudio的其他声音协议不混用。

readiness包含visualReady/audioReady/deliveryReady。对声音按cue身份、真实audio类型、资产用途和审核状态检查；同文不同角色不能由一条音轨冒充覆盖。方案approved不批准未来视频；新媒体会重置审核，交付审批绑定准确jobId。原生有声视频也须真实包含音轨且通过当前版本审阅，不因模型开关为true就自动算声音完成。

声音与视频均保存稳定requestId，没有自动付费重试。音色设计已返回voice_id但下载失败时，只从原trial_audio_url回收；不重新设计。网络不确定时保留unknown。同步设计接口不公开云taskId和逐次准确价格，字段不得伪造。

音频CLI先准备JSON，再运行（示例只预演）：

```powershell
python "<工作台目录>\studio.py" workspaces create "新剧本" --script "C:\path\script.md"
python "<工作台目录>\studio.py" --workspace PROJECT_ID script
python "<工作台目录>\studio.py" --workspace PROJECT_ID audio design "C:\path\voice.json" --request-id unique-voice-request --dry-run
python "<工作台目录>\studio.py" --workspace PROJECT_ID audio speech "C:\path\cue.json" --request-id unique-cue-request --dry-run
```

以下0.2接口仍兼容；在0.3中应同时遵守上面的workspace与声音规则。

## 0.2 节点工作流接口

所有写操作仍要求 `Content-Type: application/json` 与 `X-Studio-Request: 1`。

| 方法与路径 | 请求/用途 |
|---|---|
| GET /api/workflow | 完整节点图，含独立 revision |
| PUT /api/workflow | 提交完整图及原revision；类型、端口、重复连线、循环依赖验证后保存快照 |
| GET /api/assets | `{revision,assets}`，含path/mediaUrl/mediaType/role/reviewStatus/provenance |
| POST /api/assets/import | `{path,name?,kind?,role?,id?}`；复制本地素材，默认candidate，不自动批准 |
| PATCH /api/assets/{id} | name/role/notes/reviewStatus等；ready、candidate、blocked、missing |
| GET /api/production | 每镜就绪状态、缺件、警告及汇总 |
| POST /api/workflow/validate | `{nodeIds?:[]}`；检查依赖，不生成 |
| POST /api/workflow/compile | `{nodeIds?:[]}`；输出最终prompt、imagePaths、模型、参数、signature及待执行后期计划 |
| POST /api/workflow/run | `{nodeIds:[...],confirmed:true,requestId:"稳定ID"}`；只执行明确选定的1–32个Design生成节点 |

node结构：`{id,type,label,shotId?,assetId?,position:{x,y},data:{...}}`。edge结构：`{id,source,sourcePort,target,targetPort,role?}`。

节点包括asset、prompt、script、image、video、review、reuse、edit。主要端口为asset.image/text、prompt.text、video.prompt/references/firstFrame、image.prompt/references、review.media/out、reuse.source/video、edit.source/video。已完成图片才可流入下游视频；仅“上游节点可以运行”不等于已有参考图。

prompt.data.text 是主提示词；连接到context的文字资产会附加为实际制作规格。video/image data 包括provider、modelId、params、requiredAssetIds、negativePrompt、trimSeconds、trimStartSeconds。图片连接逐项解析为本机可用imagePaths。首帧与身份/服装参考严格区分。

`provider=imagegen` 的图像节点返回external，由Codex按指定imagegen skill执行后注册图片；网页不会冒充调用Codex内置工具。`provider=minimax-design` 才走Design生成接口。review需approved才释放媒体；reuse/edit保存明确剪辑计划，源视频未生成前显示waiting。带sourceStartSeconds的后期计划使用未裁切sourcePath，不能把全长视频冒充已完成的复用片段。

生成结果的signature包含节点、provider、kind、模型、参数、完整提示词、参考路径、裁切长度与起点；更改条件不会误认旧试片为新结果。`trimStartSeconds=1,trimSeconds=4`表示保留源片1–5秒，计费仍按完整生成源时长。原片及裁片分开保存。

工作流run和旧单镜jobs各有幂等台账，提交不自动重试。一个批次的所有输入先验证，预检失败不发送已排好的付费任务。复用、后期和外部imagegen不被偷偷纳入Design付费运行。当前项目是“待生成准备”，不是一次性全片执行授权。

只读CLI示例：

```powershell
python "<工作台目录>\studio.py" workflow get
python "<工作台目录>\studio.py" workflow validate --node video-M063
python "<工作台目录>\studio.py" workflow compile --node video-M063
python "<工作台目录>\studio.py" assets list
python "<工作台目录>\studio.py" production
```

工作流与项目分别有revision；修改镜头内容时需同步相应prompt/video节点，运行以节点图编译结果为准。`tools/materialize_preproduction.py`用于本例初次导入；默认保留现有图，显式`--rebuild-workflow`才重建。原项目不随图修改而回写。

## 地址与运行条件

| 服务 | 默认地址 | 职责 |
|---|---|---|
| 分镜工作台 | `http://127.0.0.1:8766` | 画布、项目版本、单镜任务、媒体保存与预览 |
| Design 网关 | `http://127.0.0.1:8001` | 实时模型目录、原登录、计费、生成与结果物化 |

启动：

```powershell
python "<工作台目录>\server.py" --port 8766
```

工作台可以离线编辑项目；模型目录与生成需要 Design 网关在线且登录有效。`MINIMAX_DESIGN_GATEWAY` 可在启动服务前指定实际 loopback 网关地址。CLI 的 `STORYBOARD_STUDIO_URL` 可指定实际工作台地址。两者都限制为本机 HTTP 地址，不支持公网转发。

工作台所有写请求必须带：

```http
Content-Type: application/json
X-Studio-Request: 1
```

Host 必须是当前端口的 `127.0.0.1` 或 `localhost`。浏览器跨站 Origin / Sec-Fetch-Site 会被拒绝。JSON 请求体上限 8 MiB。工作台不接收 MiniMax API key，也不需要复制 Design token。

## HTTP 接口列表

| 方法与路径 | 请求 | 返回 |
|---|---|---|
| `GET /api/status` | 无 | 网关是否在线、版本、项目文件路径 |
| `GET /api/models` | 无 | `{image: [...], video: [...]}`，Design 实时目录 |
| `GET /api/project` | 无 | 完整项目，含 `revision` |
| `PUT /api/project` | 完整项目 JSON | 保存后的完整项目，`revision` 自增 |
| `GET /api/jobs` | 无 | `{jobs: [...]}` |
| `GET /api/jobs/{id}` | 32位十六进制本地任务 id | 单个任务当前状态 |
| `POST /api/jobs` | 一个已确认的生成请求 | 新任务 HTTP 202；同请求幂等重放 HTTP 200 |
| `POST /api/jobs/{id}/refresh` | `{}` | 返回当前任务并安排刷新；不重新生成 |
| `GET /media/{相对路径}` | 无 | 本地生成媒体；支持普通 `bytes=start-end` Range |

`/api/status` 示例：

```json
{
  "online": true,
  "error": null,
  "gateway": "http://127.0.0.1:8001",
  "version": "0.1.0",
  "projectPath": "C:\\Users\\XiaoyunLiu\\Downloads\\minimax-studio\\data\\project.json"
}
```

`online:true` 仅说明网关健康，不保证模型有权限、钱包余额足够或生成必定成功。

## 项目结构与版本

```json
{
  "id": "cangtou-film-v1-0",
  "title": "苍头｜116镜头分镜工作台",
  "revision": 1,
  "script": "完整剧本原文",
  "style": "完整视觉规范",
  "sourceProject": "C:\\Users\\XiaoyunLiu\\OneDrive\\桌面\\project\\Cangtou_Film_v1_0",
  "shots": [
    {
      "id": "M002",
      "number": 2,
      "title": "停住的手",
      "scene": "序｜擦地的人",
      "shotType": "待细化",
      "productionMode": "复用视频",
      "camera": "固定俯拍",
      "action": "擦地的手停住，指节压白。",
      "prompt": "用于图片的画面描述",
      "videoPrompt": "用于视频的动作与镜头描述",
      "referencePaths": [],
      "kind": "video",
      "modelId": "wan3.0-video",
      "params": {
        "duration": "2",
        "resolution": "480P",
        "aspect_ratio": "16:9",
        "image_mode": "reference",
        "generate_audio": "false"
      },
      "plannedDuration": 4,
      "position": {"x": 422, "y": 90},
      "status": "draft",
      "sourceData": {}
    }
  ],
  "edges": [],
  "updatedAt": "ISO-8601 UTC"
}
```

上例为结构说明，省略其他115镜头；不要用它覆盖现有项目。当前真实项目含完整原始 `sourceData`、候选资产元数据和来源记录。

- `PUT` 是整份项目保存，不是局部 patch。先 GET，保留所有未改字段，再提交同一 `revision`。
- 服务验证镜头 id 唯一、坐标合理、参考路径字段为数组、连线端点存在，最多1000镜头。
- 成功后 revision 自增，旧版自动保存到 `<工作台目录>\data\snapshots`。
- 版本不一致返回 HTTP 409 / `REVISION_CONFLICT`。重新读取最新项目，再合并修改；不要强行改 revision 覆盖别人。
- `plannedDuration` 是原片剪辑时长；`params.duration` 是这次模型生成时长，两者不可混为一谈。
- `sourceData` 保留原始镜头、台词、连续性、复用和后期信息。新改编写入工作字段，并注明依据，不篡改来源证据。
- `candidateReferences` 是候选素材元数据；`referencePaths` 才是实际提交所用图片路径。候选图、环境预览图不自动等于批准首帧。

## 实时模型与参数

每次更换模型或正式生成前读取 `/api/models`。目录中通常含：`id`、`backend`、`name`、`model_name`、`params`、`max_refs`、`promptMaxLength`、`paramConstraints` 等。工作台请求 `modelId` 使用目录 id；下游内部调用则须正确映射 model_name。例如目录 `g-image-2` 对应 `gpt-image-2`，`banana-2` 对应 `nano_banana_2_flash`。图片后端还依赖 `params.model_name`，不能只替换请求顶层 model_id。

`params` 中的字段可能有 `type`、`options`、`default`、`min`、`max`。服务器用实时目录的默认值补齐参数，校验传入字段、选项、滑块范围及条件禁用组合。不要给图片模型套视频 `480P`，也不要把不同视频模型的时长和参考模式混用。参数值建议明确使用字符串；布尔值会转换为 `"true"` / `"false"`。

当前查明：Wan 3.0 最短2秒；MiniMax-H3 最短4秒。请求1秒会越过模型支持范围。要得到1秒交付片段，先生成支持的源时长，再传 `trimSeconds:1` 本地裁切；费用仍按模型生成的源时长计算，实际规则由 Design 云端决定。

更换镜头的 kind/model 时，同时更新其 params。CLI `generate` 会从镜头现有 params 开始合并 `--param`，不会自动删除旧模型字段；因此跨模型切换前应先编辑并保存匹配的参数对象。

## 单次生成请求

下面是结构示例，不是再次执行验证的授权。已有验证任务应先查询，避免再创建一次。

```json
{
  "shotId": "M002",
  "kind": "video",
  "modelId": "wan3.0-video",
  "prompt": "固定俯拍，只见手和褪色青布。手在青砖上短暂擦动后停住，指节缓慢压紧。自然冷光，写实历史电影质感，不露脸，无文字，无配乐。",
  "params": {
    "duration": "2",
    "resolution": "480P",
    "aspect_ratio": "16:9",
    "image_mode": "reference",
    "generate_audio": "false"
  },
  "imagePaths": [],
  "trimSeconds": 1,
  "requestId": "unique-stable-id-for-one-authorized-job",
  "confirmed": true
}
```

字段说明：

| 字段 | 规则 |
|---|---|
| `shotId` | 必须存在于已保存项目中 |
| `kind` | `image` 或 `video` |
| `modelId` | 当前 Design 目录中的真实 id |
| `prompt` | 明确非空，不能超过模型上限 |
| `params` | 对应模型参数对象；未知字段会拒绝 |
| `imagePaths` | 存在的本地 PNG/JPG/JPEG/WebP 绝对路径数组；符合 max_refs |
| `trimSeconds` | 可选，仅视频，必须大于0且不超过生成时长 |
| `requestId` | 8–100位字母、数字、下划线或短横线；一次逻辑提交一个固定值 |
| `confirmed` | 必须严格为 JSON `true`，表示调用方已获得此次付费操作授权 |

用户当前会话已经明确授权的选定任务，不需要再重复问同一权限；调用方用 `confirmed:true` / CLI `--confirm` 表达这份现有授权。仅阅读本文档或导入剧本不等于授权生成。

幂等规则：服务器持久保存 `requestId` 和整个请求体的哈希。相同 requestId、相同请求体返回原任务；相同 id、不同请求体返回 `IDEMPOTENCY_CONFLICT`。网络重试必须复用原 requestId 和完整请求体。不要为“重试”换新 UUID。

此幂等机制是工作台本地的保护，不是上游云服务提供的全局幂等。若丢失 `jobs.json`、更换工作台数据目录或直接调用 Design，不能假设仍有同样保护。

新任务返回 HTTP 202，类似：

```json
{
  "id": "32位十六进制本地任务id",
  "requestId": "unique-stable-id-for-one-authorized-job",
  "shotId": "M002",
  "kind": "video",
  "modelId": "wan3.0-video",
  "status": "queued",
  "params": {"duration": "2"},
  "trimSeconds": 1
}
```

HTTP 202 仅代表进入本地队列。上游成功接收后才出现 `taskId`；生成完成并下载后才有 `mediaUrl`、`sourceMediaUrl`、`thumbnailUrl`、`mediaPath`、`sourcePath`、`mediaInfo` 等。

## 任务状态与恢复

| 状态 | 含义与下一步 |
|---|---|
| `queued` / `submitting` | 本地排队或正在提交；不要再发一条 |
| `processing` | 已取得上游 taskId，继续查询 |
| `downloading` | 上游完成，正在保存和处理媒体 |
| `succeeded` | 本地媒体已保存；查看 mediaUrl 和结果 |
| `failed` | 已知失败，检查错误；不得自动换模型或扩大重试预算 |
| `unknown` | 提交结果不确定，可能已收费；先核对 Design 任务与账单 |
| `download_failed` | 已完成生成但保存失败；刷新现有任务，不要重新生成 |

后台约每8秒调度一次有 taskId 的进行中任务查询。刷新接口是异步调度，立即返回的可能仍是旧状态。网关返回替代 task_id 时，工作台保存新 id。`pollError` 不自动判定为生成失败。

服务重启会将遗留 queued/submitting 标为 unknown，绝不自动重发付费 POST。已经有 taskId 的任务可以继续查询。`download_failed` 可用 `job {id} --refresh` 重试原结果回收。

下载成功后，工作台给对应镜头写入 `mediaUrl`、`thumbnailUrl`、`lastJobId`、`status=succeeded`。这个 succeeded 只代表技术任务完成，不等于用户已验收镜头或批准作为正式成片。正式审批仍应另行记录。

## 错误格式

工作台 HTTP 错误：

```json
{"error":"项目已被 Codex 或其他窗口更新，请重新载入后再保存。","code":"REVISION_CONFLICT","detail":null}
```

常见错误：

| code | 含义 |
|---|---|
| `CONFIRMATION_REQUIRED` | 未确认此次付费提交 |
| `REVISION_CONFLICT` | 项目版本过期 |
| `IDEMPOTENCY_CONFLICT` | 相同 requestId 携带不同请求 |
| `SHOT_NOT_FOUND` / `MODEL_NOT_FOUND` | 镜头未保存或模型不在目录 |
| `UNSUPPORTED_PARAMETER` | 参数选项不支持 |
| `LOCAL_ONLY` / `ORIGIN_REJECTED` | 非本机地址或跨站访问 |
| `REQUEST_HEADER_REQUIRED` | 写请求缺少 X-Studio-Request |
| `GATEWAY_UNAVAILABLE` | Design 网关未运行或地址不匹配 |
| `SUBMISSION_STATUS_UNKNOWN` | 上游提交结果不确定，禁止自动重发 |
| `OUTPUT_PENDING` / `DOWNLOAD_FAILED` | 结果本地化尚未完成或失败 |
| `MEDIA_TOOL_MISSING` | 无可用 FFmpeg / FFprobe |

Design 可能直接返回账号、内容、并发、余额或确认相关错误，例如 `billing_insufficient_balance`、`CREDIT_CONFIRMATION_TIMEOUT`、`GENERATION_CANCELLED`。工作台保留错误状态，不屏蔽或绕过这些检查。

## CLI

CLI 绝对路径：`<工作台目录>\studio.py`。所有输出为 JSON，错误退出码非0。以下例子可在 PowerShell 中执行。

```powershell
python "<工作台目录>\studio.py" doctor
python "<工作台目录>\studio.py" models
python "<工作台目录>\studio.py" project
python "<工作台目录>\studio.py" shots
python "<工作台目录>\studio.py" shot M002
python "<工作台目录>\studio.py" jobs
```

分镜编辑：先读取最新完整项目，在副本中修改，保留 revision。然后：

```powershell
python "<工作台目录>\studio.py" apply "<工作台目录>\data\proposed-project.json" --dry-run
python "<工作台目录>\studio.py" apply "<工作台目录>\data\proposed-project.json"
```

`apply --dry-run` 校验结构并报告 `revisionMatches`，不会保存或生成。`import-script` 只把 UTF-8 `.md/.txt` 原文写入项目，不会自动拆镜、替换现有镜头或调用模型。

生成预览，不提交：

```powershell
python "<工作台目录>\studio.py" generate --shot M002 --kind video --model wan3.0-video --param duration=2 --param resolution=480P --param aspect_ratio=16:9 --param generate_audio=false --no-reference --trim 1 --request-id one-authorized-job-example --dry-run
```

`generate --dry-run` 只构造请求；它不执行实时模型参数的全部服务端校验。实际执行需要当前用户授权后去掉 `--dry-run` 并加 `--confirm`；可用 `--prompt-file` 指定已审阅的 UTF-8 提示词文件。不要因为看到示例就重复创建本次验证任务。

已有任务查看/刷新：

```powershell
python "<工作台目录>\studio.py" job 0eeb81c6704b48c38e9fc008346d9bbf
python "<工作台目录>\studio.py" job 0eeb81c6704b48c38e9fc008346d9bbf --refresh
```

## Design 内部适配合同

`gateway.py` 提供 `Gateway.health/models/workspace/billing_scope/wallet/pricing/estimate/submit/query`。本模块不提取 token。Design 网关按正常 TokenService 登录向云端发送请求。

| Design 路径 | 用途 |
|---|---|
| `GET /api/health/live` | 存活检查 |
| `GET /api/models/image` / `video` | 实时生成目录 |
| `GET /api/workspace` | `{dir: 工作区绝对路径}` |
| `GET /api/internal/sessions/billing-current-scope` | 已安装 MCP 同样使用的无 plugin/session 计费归属回退 |
| `GET /api/internal/sessions/{runtimeSessionId}/request-group` | 已绑定真实会话时的计费归属 |
| `GET /api/v1/credit/wallet` | 已登录账户钱包 |
| `GET /api/v1/billing/pricing` | 定价配置，不等于准确单任务报价 |
| `POST /api/generate/{image或video}/submit` | 一次生成提交 |
| `GET /api/generate/tasks/{task_id}/query` | 任务查询及网关结果落盘 |

生成 body 由适配器组装：`backend`、`model_id`、`prompt`、字符串值 `params`、`image_paths`、受控 basename `filename`、`source_tool`。例如 Wan 使用 backend=`wan_i2v`；MiniMax-H3 使用 `minimax_v3`。没有直接调用、伪造或改写上游模型服务的授权机制。

当前无 Design plugin/session 的外部调用沿用客户端明确提供的 billing-current-scope fallback；canonical 返回的 group_id 用于归属，legacy 沿用原个人账户路径。该模式没有 Design 会话中的额度提醒卡，工作台要求自己的 confirmed。若将来绑定真实 runtime session，适配器保留相应 preflight。不要设置 `x-hilo-source:canvas` 来去掉 session，也不要修改提醒门槛或伪造 session、group、账号。

查询成功通常返回：

```json
{
  "ok": true,
  "task_id": "Design任务id",
  "status": "succeeded",
  "result": {"ok": true, "path": "工作区相对路径.mp4", "width": 832, "height": 480, "duration": 2.0},
  "asset": {"path": "工作区相对路径.mp4"}
}
```

尺寸和时长仅为格式示例，不是本次实测结果。工作台通过 workspace.dir 定位相对路径，校验路径范围，复制到自己的 media 目录；完整2秒源文件保留，1秒裁切文件单独保存。

本地 CreditController 未暴露可直接用的准确 `calculate-cost` 路由。`Gateway.estimate()` 返回 `available:false` / `EXACT_QUOTE_NOT_EXPOSED`。不要把模型目录、促销折扣、钱包余额差（可能有其他并行活动）伪称准确单次报价或正式账单。

## 文件与验证证据

| 绝对路径 | 内容 |
|---|---|
| `<工作台目录>\data\project.json` | 当前项目与 revision |
| `<工作台目录>\data\jobs.json` | 本地任务台账与幂等键 |
| `<工作台目录>\data\snapshots` | 保存前快照 |
| `<工作台目录>\data\import_notes.md` | 116镜头来源与导入说明 |
| `<工作台目录>\data\verification.json` | 本次真实验证结果；由执行者完成后写入 |
| `<工作台目录>\media` | 生成源文件、裁切视频、封面 |

模型目录、钱包与计费归属读取已跑通。**一次真实视频链路已完成**：

| 实测项 | 记录 |
|---|---|
| 镜头 / 模型 | M002 / wan3.0-video，无参考图 |
| 本地 job id | `0eeb81c6704b48c38e9fc008346d9bbf` |
| 上游 task id | `brGV2xVDm3XQ` |
| 固定 requestId | `cangtou-m002-verify-20260924-01` |
| 源片 / 交付裁切 | 2.000秒 / 1.000秒 |
| 画面 / 音轨 | 854×480，30fps，H.264；无音轨 |
| 测试前后余额 | 174792 → 174712，观察余额减少80积分 |
| 本地生成提交数 | 1 |
| 结果 | 已保存并回填 M002；未自动表示正式镜头已验收 |

完整源片：`<工作台目录>\media\0eeb81c6704b48c38e9fc008346d9bbf\source1.mp4`。

1秒裁切：`<工作台目录>\media\0eeb81c6704b48c38e9fc008346d9bbf\preview-1s.mp4`。

哈希、探测输出与时间记录在 verification.json。余额差是本次观察值，不伪称独立正式账单。目录参考64没有准确预测80积分的余额变化，不能用于保证后续报价。图片接口已适配，但尚未单独付费验证。不要再次运行此验证请求；检查现有产物即可。


## 0.4 导演分析、选音与参考覆盖

所有接口保留 X-Studio-Workspace；写入加 X-Studio-Request:1。

- GET /api/direction：读取独立 revision 的导演分析，无分析时返回 draft scaffold。
- PUT /api/direction：保存 schemaVersion=1、projectId、scriptHash、revision、characters/spaces/shots；核对原文段落与稳定 ID，保存旧快照。
- GET /api/direction/report：逐镜 requirements 真实文件、enabled 绑定、mediaType/usage、固定 hash、来源变更、continuityLinks 和复用依赖检查。summary 分参考覆盖与待审首帧，不代表成片完成。
- POST /api/direction/audition：{voiceId,caseId,mode:design|speech,assetId?} 返回 {spec,willSubmitGeneration:false}。不付费。
- POST /api/direction/cast：{revision,projectRevision,voiceId,assetId,notes?} 选用该角色候选声线，同步各镜 voice_identity，保留其他绑定。只批准音色，不批准对白。

角色字段：id（声音槽位）、characterId、name、facts、interpretation、arc、voiceDesign、evidenceBlockIds、visualAssetIds、candidateAssetIds、selectedAssetId、auditionCases。case 含 id、shotId、cueId、text、situation、listener、intention、params、performance。

镜头分析字段：shotId、sourceHash（direction.shot_digest）、evidenceBlockIds、analysisStatus:prepared、requirements、continuityIn/Out、continuityLinks、direction、voiceCues、reuseFromShotId、frameReview。requirement 含 id,label,category,mediaType,usage,assetIds,assetHashes,reason,gapKey。requirements 要从画面需求人工/由 Codex 分析，接口不会自动理解语义。

POST /api/audio/speech 新增 performance:[{after:唯一原文短语,pause:0.01至2秒}]，或 tag:breath|inhale|exhale|sighs|chuckle。保持 text 原句，后端编成支持的标记，提交文本和原台词分开保存。不能在末尾/同位置重复插入；导演指令不作为台词。

CLI：direction get；direction apply FILE --dry-run；direction report --all；direction audition VOICE CASE --mode speech --asset ASSET。既有音频提交与幂等规则不变。


## 0.6 本机服务、声音处理与剪辑

所有资源和本机任务仍按workspace隔离。浏览器默认用当前活动workspace；自动化请求建议发送`X-Studio-Workspace: ID`。本机任务不经过Design，也不扣MiniMax积分；云端图片/视频/Design声音接口仍调用当前已登录的Design并按该账号规则计费。

- `GET /api/health`：轻量就绪检查，返回`{ok,version,pid,appRoot,projectPath,workspaceId}`；不访问Design。桌面启动器用它等待服务就绪并核对进程身份。
- `GET /api/models`：读取本机缓存的image/video/speech目录，不访问Design。目录内容不代表当前在线可提交，也不代表免费。`POST /api/models/refresh`显式刷新Design在线目录；执行图片/视频任务时服务器仍会在线校验。
- `GET /api/local-capabilities`：FFmpeg、Demucs及本机声音引擎的部署/实测状态。引擎只有部署记录ready且Python和worker文件存在时才返回ready；文件在磁盘上或界面有入口都不算已验证。
- `POST /api/local-jobs`和`GET /api/local-jobs`：创建/查看当前workspace的串行本机任务。每个POST带稳定`requestId`（8–100位字母、数字、下划线或连字符）；重用ID必须保持相同参数。任务状态为queued/running/succeeded/failed，结果作为candidate素材登记，原素材不覆盖。

本机任务请求：

| operation | 关键字段 | 用途和约束 |
|---|---|---|
| `transcribe` | `{assetId,language?,device?,requestId}` | 音频或带声音的视频转文字，输出带时间点识别稿和SRT；识别稿与原剧本分开，须人工校对。 |
| `tts` | `{referenceAssetId,text,referenceText?,language?,shotId?,cueId?,device?,requestId}` | 以不超过30秒的声音样本作本地参考，生成新台词；不继承录音表演或视频口型。文本最多600字。 |
| `voice_convert` | `{assetId,referenceAssetId,device?,requestId}` | 保留输入对白和表演，转换至参考声线；结果仍须核对发音、时长与伪影。 |
| `speech_edit` | `{assetId,sourceText,instruction,device?,requestId}` | 以校对后的原台词和编辑指令生成局部精修候选；网页当前支持唯一词句替换。 |
| `lip_sync` | `{assetId,referenceAssetId,device?,requestId}` | 用视频和已确认对白音频生成口型候选；原视频不覆盖，需检查身份、嘴部、牙齿和遮挡。 |
| `extract_audio` | `{assetId,in?,out?,device?,requestId}` | 用FFmpeg提取原音轨，不去音乐。 |
| `separate_vocals` | `{assetId,in?,out?,device?,requestId}` | 用Demucs拆分人声与其余声音；不按演员分轨。 |
| `render` | `{revision,requestId}` | 渲染已保存的时间线。必须先加入至少一个视频片段。 |

ASR/TTS需要音频源，voice_convert需要源音频及30秒以内的参考录音，speech_edit需要音频源、原台词及指令，lip_sync需要视频和对白音频。媒体必须属于当前workspace。所有AI工作进程设置离线环境变量；缺少部署、资源不足或推理错误时任务失败，不会改走收费云端。

当前FFmpeg、Demucs、faster-whisper、Qwen3-TTS、Seed-VC、dots.tts.edit、MuseTalk1.5均有真实运行证据，具体设备和输入规模见deployment JSON及data/creator-v6-verification.json。ASR与Qwen同时验证CPU/GPU；Seed、dots和MuseTalk验证RTX4060。结果仍为候选；模型可运行不代表自然度、人物身份或成片质量通过。以GET /api/local-capabilities实时结果为准。

时间线`GET/PUT /api/timeline`有独立revision，保存剪辑不会改写原素材或项目revision。结构为`{schemaVersion:1,projectId,revision,name,settings:{width,height,fps},clips:[{id,assetId,in,out,gain,mute}],audio:[{id,assetId,in,out,at,gain,mute}],subtitles:[{start,end,text}]}`。视频按数组顺序拼接，声音`at`为时间线起点。`POST /api/timeline/import-subtitles`接收`{jobId,revision,offset?}`，仅导入当前workspace中已成功且含transcript的本机识别任务；要求时间线上至少有一个视频以确定片长。可逐句校订。渲染MP4与SRT分别输出，SRT不是烧录字幕。

声音制作顺序：确定角色声线/voice_identity → 为每句选择或编写dialogue_performance → 审听台词、时长和cue绑定 → 需要时用已ready的本机TTS/换声/局部精修 → 对口型并审查 → 分轨混音与时间线验收。声线身份、逐句表演、环境/音乐post_mix、视频模型参考是不同用途；克隆声线、合成新台词、转换已有表演和口型修复不是同一操作。Design试听/配音任务仍走云端并可能收费；“本机优先”不把已有云端入口改成免费服务。

## 0.5 制作与剪辑（旧版接口记录）

全部接口继续要求显式workspace；本机任务不调用付费模型。

- POST /api/generation/preflight：与image/video任务输入相同；只验证，不写jobs、不提交云端。
- POST /api/audio/reference-speech：{name,modelId:seed-audio-1.0,text,direction,referenceAssetIds:[音频ID],params,confirmed,requestId}。参考顺序明确，对应@音频1等。
- POST /api/audio/voice-clone：{name,referenceAssetId,previewText,voiceId?,confirmed,requestId}。10秒至5分钟、20MB内。设计/克隆ID正常保留，下载失败可回收，不重新收费生成。
- GET /api/media：当前项目真实素材及旧生成源片。虚拟job:ID可用于剪辑，不是跨项目文件路径。
- GET /api/local-capabilities：FFmpeg/Demucs实际部署状态、设备能力。
- GET/PUT /api/timeline：独立revision，保留历史快照。{schemaVersion:1,projectId,revision,name,settings:{width,height,fps},clips:[{id,assetId,in,out,gain,mute}],audio:[{id,assetId,in,out,at,gain,mute}],subtitles:[{start,end,text}]}。视频顺序拼接，音轨at为时间线秒数。
- POST /api/local-jobs：{operation:render,revision,requestId}，或{operation:extract_audio|separate_vocals,assetId,in?,out?,device:cpu|cuda,requestId}。串行本机队列，状态queued/running/succeeded/failed，输入与结果留当前workspace。
- GET /api/local-jobs：本机进度、错误、输出资源、字幕URL。
- POST /api/direction/recheck：{shotId,projectRevision,reviewed:true}，只在用户实际复核后更新本镜分析版本，已有参考缺口/剧本变更仍拒绝。

SeedAudio与克隆适配依据Design 3.0.18.74安装代码；已做合同/无网络测试，未在本轮追加付费云端实测。

POST /api/services/design/start opens only the installed MiniMax Design executable for normal login; accepts no executable/path arguments and does not submit generation. Local TTS with shotId/cueId automatically attaches a candidate post_mix binding only while the original cue text still matches; attachmentWarning preserves a successful audio result when the shot changed.


## 0.7 桌面、多轨剪辑与个人资料库

主服务仍为127.0.0.1:8766。每次HTTP操作显式发送 `X-Studio-Workspace: ID`，写入加 `X-Studio-Request: 1`、Content-Type:application/json。剪辑服务由主服务按workspace启动，URL是临时loopback端口，不能硬编码。调用方只通过主服务的 `/api/editor/*` 定位当前工作区。

### 多轨剪辑

- `POST /api/editor/open {}` → `{url,workspaceId,pid,nonce}`；首次复制旧timeline，之后复用独立剪辑数据。
- `GET /api/editor/project` → 多轨完整文档。`PUT /api/editor/project` 保存完整文档，revision必须是读取值+1；409必须重新读取合并。保存前保留快照。
- `POST /api/editor/import-assets {assetIds:[...]}` → `{added,revision}`。导入当前workspace媒体到素材箱，不自动排上时间线。
- `POST /api/editor/proxy {assetId}` / `GET /api/editor/proxies`：创建/查询720p预览代理。成功src属于剪辑服务，不属于主服务；赋给media.previewSrc，原src不变。
- `POST /api/editor/captions {src,language,requestId}`：src是剪辑服务内已上传的混音WAV；返回本机ASR任务。通过 `/api/local-jobs` 查询。
- `POST /api/editor/captions/apply {jobId,revision,position:top|center|bottom,replaceAuto:true,offset?}`：使用当前workspace成功识别稿，创建可视文字片段。revision传读取原值，服务自行+1；replaceAuto只替换有自动字幕标记的文字，手工标题保留。
- `POST /api/editor/recover {name}`：将本workspace剪辑exports中的文件作为candidate注册到媒体库；支持URL编码的中文名称。

文档关键字段：`{name,revision,width,height,fps,tracks:[{id,kind}],media:[{id,name,kind,src,duration,width,height,studioAssetId?,previewSrc?}],clips:[{id,mediaId,kind,track,start,in,duration,name,props,keyframes?,linkGroup?}]}`。

start/in/duration的单位是秒：start为片段在时间线的位置，in为源素材入点。V1/V2/V3是视频/文字层，A1等为音频层；kind可为video/audio/image/text/adjust。文字props含text,font,fontSize,x,y，x/y相对画幅中心（像素）；`studioCaptionBatch`标记自动字幕。`studioUnlinked:true`保留解除音画关联状态。media.src必须是剪辑服务内 `/media/` 或 `/library/` 路径，不能传主服务的 `/w/ID/media/...`。

新UI导出逐帧合成（含烧录字幕）并回收成片。历史 `/api/timeline` / CLI timeline 仍是旧顺序剪辑接口，不会同步覆盖多轨编辑器。浏览器编辑未保存时，不从外部写同一项目；先flush，再读版本合并。原素材、旧timeline和分镜workflow分别保存。

### 个人资料库

- `GET /api/library` → `{revision,people,assets}`，媒体URL为 `/library/media/...`。
- `POST /api/library/people {id?,name,description?,tags?}` 建立/重命名人物组。
- `POST /api/library/assets {personId?,path,name?,role,spokenText?,notes?}` 导入绝对路径；或用 `{filename,dataBase64,...}` 上传≤64MB。
- `PATCH /api/library/assets/ID {name?,characterId?,role?,spokenText?,notes?,reviewStatus?}` 修改资料；blocked用于归档，candidate恢复。
- `POST /api/library/use {assetId}` 将固定版本复制到请求指定workspace，返回项目asset；再通过workflow API创建asset节点和连线，或 `/api/editor/import-assets` 加入剪辑。

个人库跨项目共用，但素材进入项目之后有独立副本与来源记录。不能把别的workspace的assetId直接交给当前视频节点或本机处理。未提供个人素材时保持空库，不从电脑自动扫描。


## 0.8 片段模型处理

显示名为“灵镜AI”，原路径、workspace与接口保持兼容。

`POST /api/editor/process {clipId,revision,operation,requestId,device?,referenceAssetId?,referenceText?,text?,sourceText?,instruction?,language?}`：读取该workspace保存的剪辑文档，核对revision和片段，按in/duration截取真实输入后加入本机队列。operation为extract_audio/separate_vocals/transcribe/tts/voice_convert/speech_edit/lip_sync。TTS以所选片段原声为参考；换声与口型需另选referenceAssetId。拒绝变速/速度关键帧，限制片段时长（口型15秒，TTS/换声/编辑30秒，其他30分钟）。不烘焙时间线效果，不修改原素材。

返回本机job，包含editorClip:{id,mediaId,start,in,duration,track,kind}。通过/api/local-jobs轮询并审阅结果；/api/editor/import-assets加入素材箱。界面放回新轨道前再次检查editorClip一致性。字幕apply可传replaceClipAuto:true，依据job.editorClip只替换该范围重叠的自动字幕，仍保留手工标题；片段改变会拒绝自动替换。裁切输入保存在workspace/media/editor-inputs，记录原始hash、入点、时长与片段ID。


## 0.9 配音调试台

所有请求保持明确X-Studio-Workspace，写入加X-Studio-Request:1。调试台试听不通过/api/local-jobs自动入库，复用同一串行LOCAL_POOL执行，避免8GB显卡同时驻留多个模型。

- GET /api/voice-lab → {draftRevision,draft,profiles,takes,engines}；角色初稿、各workspace的试听历史和本机已验证设备。
- PUT /api/voice-lab/draft {revision,draft} → 独立draftRevision+1；冲突409，不覆盖其他窗口配置。
- POST /api/voice-lab/preview {requestId,mode:design|reference,text,description?,performance?,referenceTakeId?,referenceAssetId?,referenceText?,name?,voiceSlotId?,characterId?,seed?,temperature?,device?} → {id,status,spec,...}。text 1–200字；设计模式需description；固定样本模式不接受performance，参考必须属于当前workspace且≤30秒。同requestId同参数复用任务，换参数409。每项目最多三个排队/运行试听。
- POST /api/voice-lab/cancel {takeId} → 取消队列或结束本任务的工作进程，保留记录；不能结束其他项目任务。
- POST /api/voice-lab/adopt {takeId,confirmed:true,target:project|library,role:voice_identity|dialogue_performance,name?,personId?} → {asset,target,takeId}。只有成功试听可以入库，必须显式确认，项目资产按takeId/role去重。个人资料保存origin溯源和可读备注。

试听state为queued/running/succeeded/failed/cancelled。成功试听的mediaUrl属于workspace/media/voice-lab；它不会出现在assets列表，直到adopt。音色/台词主观审核仍独立，存入资产默认candidate。服务重启将中断任务标为失败，不自动重复生成。VoiceDesign就绪以data/voice-design-deployment.json及实际文件为准；源码和tokenizer存在不代表模型已就绪。

本机模型面板新增voice_lab_design展示路由，打开配音调试台；它不是/api/local-jobs可直接提交的operation。固定声线模式复用已验证0.6B Base，不能把它当作VoiceDesign或支持任意表演instruct。

## 0.10 配音调试台阶段进度

`GET /api/voice-lab`中每个take的`progress`是阶段状态，不是对模型内部采样进度的估算：

```json
{"phase":"generating","indeterminate":true,"percent":null}
```

`progress.phase`只取`queued`、`loading`、`generating`、`encoding`、`complete`、`failed`、`cancelled`。前三个处理中阶段和`encoding`均为`indeterminate:true, percent:null`；不编造百分比。只有输出音频已实际验证并可试听后，成功记录才为`phase:"complete", indeterminate:false, percent:100`。失败、取消分别报告对应phase，percent保持null。历史记录缺少progress时，按take的status映射补齐阶段。

`startedAt`记录任务实际开始时间；排队时可为null。界面耗时由`startedAt`计算，未开始时可回退到`createdAt`。前端按进行中的take定期读取状态；遇到短暂断连应自动延迟重试，不应把一次读取失败当成任务结束。进入complete/failed/cancelled后停止该take的spinner和进度动画。
