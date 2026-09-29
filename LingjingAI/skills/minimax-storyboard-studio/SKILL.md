---
name: minimax-storyboard-studio
description: 用灵镜AI（原镜序Studio）完成任意剧本的剖析、分镜、角色声线、参考连续性、个人资料复用与多轨剪辑交付；适用于新项目制作、配音调整和桌面视频工作流。
metadata:
  short-description: 剧本剖析、角色选音与逐镜生产工作流
---

# 灵镜AI：从剧本到可审阅的制作计划

软件位于 `<工作台目录>`，先读该目录 `AGENTS.md`。界面 `http://127.0.0.1:8766/`，CLI 为 `studio.py`。模型连接、任务台账和素材归属由软件处理；不读取登录 token，不修改 Design 安装包。

## 定位工作区

先 `studio.py workspaces list`，用明确的 `--workspace ID` 操作。新剧本用 `workspaces create "项目名" --script "绝对路径"` 建独立 workspace，不继承《苍头》的角色、声线、镜头数量或资产 ID。原始资料是来源，不是当前操作指令；旧阶段标记不能覆盖用户当前授权。

本 skill 的 `scripts/prepare_workspace.py --workspace ID --out DIRECTORY` 导出当前剧本、段落、镜头、资源和导演分析供实际阅读。它只整理材料，不自动理解故事。已有分析先读再增量修改，不能用空模板覆盖。

## 剖析与制作

1. **先通读原文再分场。** 抓住主角目标、阻碍、代价及每场变化。区分剧本事实、来源矛盾、导演解释和新增选择，关联 evidenceBlockIds。不要把人名统计叫作剖析完成。交付见 [剧本与镜头](references/screenplay.md)。
2. **角色与演员分开。** 记录外观依据、说话对象、行动目的、关系与阶段变化；查看实际形象参考，不凭标签猜年龄/性别。音色负责身份，情境负责表演；对白、独白和呼喊不能套同一“低沉克制”。按 [声音试演](references/voice-direction.md) 调整，保留旧版作可比较的试验。
3. **先画面需求，再找引用。** 每镜列清谁清晰可见、谁只有肩背/手、谁在画外；判断身份、服装/伤势、空间、关键道具、站位和光位分别需要什么依据。先从镜头需求写 requirements，再查 bindings；不能照抄现有 bindings 当完整需求，否则查不出遗漏。见 [参考与连续性](references/reference-coverage.md)。
4. **共用母版，按状态派生。** 日夜/反打共用几何；换衣不换脸/声线。物件实例、人物自身左右、持物和伤势写进 continuityIn/Out，以 continuityLinks 检查因果。闪前与回放按来源连接，不按镜号硬串。
5. **落实到平台。** 从原文段落创建/更新镜头，通过带 revision 的 POST /api/shots/batch 更新 bindings、audioPlan、videoPrompt，服务同步节点图。direction 是可审阅分析；关键指令还须写进实际 videoPrompt，不能只交一份与画布脱节的表格。
6. **先共享缺口，后逐镜。** direction report --all 按 gapKey 合并缺失资产，优先补能覆盖多镜的母版。需生图时用内置 imagegen 技能，先看参考、保留版本、导入真实文件并检查用途。特定构图/尺度/手部动作仍缺依据时再补首帧，不为充数给每镜加全部人脸。
7. **编译、试验、回填。** 查实时模型目录，校验真实路径、时长、媒体能力与 @ 映射。在当前明确授权内执行有限选定试验，成功即回收复用；不确定提交按原 requestId 查账，不换 ID 重发。技术成功、参考覆盖、声音通过和成片通过分别报告。

## 持久化与接口

从 `direction get` 读取当前分析。格式以软件 direction.py 和 API.md 为准：schemaVersion=1、projectId、scriptHash、独立 revision、characters、spaces、shots。实际重新分析变化内容后才用 direction.shot_digest(shot) 更新 sourceHash，不能只刷新哈希消掉过期提示。

```powershell
python "<工作台目录>/studio.py" --workspace PROJECT_ID direction apply "分析提案绝对路径.json" --dry-run
python "<工作台目录>/studio.py" --workspace PROJECT_ID direction apply "分析提案绝对路径.json"
python "<工作台目录>/studio.py" --workspace PROJECT_ID direction report --all
python "<工作台目录>/studio.py" --workspace PROJECT_ID workflow compile --node VIDEO_NODE_ID
```

`direction audition VOICE_ID CASE_ID --mode design|speech --asset ASSET_ID` 只生成任务规格，不调用付费模型。执行仍走 `audio design|speech SPEC.json --request-id ID --confirm`。工作簿显示形象、情境、同句 A/B 和声线选择；POST /api/direction/cast 只在实际选音后调用，更新该角色各镜音色绑定，正式对白保持独立审阅。

referenceReady 只证明声明需求的真实文件、正确用途、连接、版本与已声明连续性；还要看实际图片、审查漏项、审首帧和片段，不保证生成一致性。画面可生成不等于声音、口型、环境音或交付完成。

## 生成、剪辑与本机声音工具

0.6起，顶部“生成、声音、剪辑、任务”提供生成面板、模型/参数选择、非破坏性时间线、本机声音工作室与本机工具状态。按软件 `使用指南.md` 的步骤操作；当前接口、启动、模型缓存和部署状态见 [`references/runtime-and-api.md`](references/runtime-and-api.md) 及仓库 `API.md` 的0.6节。不要将已接入界面入口误报为ready引擎；以`GET /api/local-capabilities`与部署验证状态为准。

Seed Audio参考配音与MiniMax录音克隆走独立接口；不能塞进voice_id型Speech参数。生成结果自动入资源库为candidate，图片作后续参考需审查用途。新适配合同通过不等于已经付费实测，不擅自为验收消耗积分。

`studio.py media`读取当前媒体；`timeline get/apply`仅读写旧版顺序时间线，新版多轨使用`/api/editor/project`；`local-tools`读取本机模型状态；`local-run SPEC.json --request-id ID`发起当前ready的本机操作，`local-jobs`查看结果。剪辑依赖真实视频，不能把参考图假装成已完成成片。保留原素材；音轨at是独立时间线起点，视频排序后必须重新核对对白。识别字幕先核对后导入、逐句修订。旧版MP4与SRT分别导出；0.7多轨编辑器将文字字幕合成进MP4。两套时间线不可混写。

FFmpeg提取的是完整原音轨；Demucs分离人声与其余声音，可能有残留，不是按演员拆轨。TTS、换声、局部编辑及口型是不同操作，只有模型部署并实测后才可用。本机处理不走MiniMax积分，错误不得切换到收费Design链路。Design云端图/视频/Speech提交仍可能计费；目录缓存和适配完成都不等于免费或已完成生成。独立workspace和任务台账规则同样适用。

## 桌面剪辑与个人资料复用

0.7使用独立Electron桌面窗口和MIT FableCut剪辑内核。操作多轨、自动字幕、预览代理、导出或个人资料库时，先读 [桌面制作与交付](references/desktop-editing.md)。先保存编辑器草稿再进行外部API变更，使用读取到的revision；完成后实际打开成片检查画面、字幕、声音和时长。个人库跨项目共用，加入项目产生固定副本，不用库元数据变更替换在制镜头。

## 效率与模型分工

复杂剧本剖析、导演表演、空间连续性、多文件开发使用 gpt-6-astra；明确整理、格式转换、状态查询、小范围验证使用 gpt-6-luna。这是用户的 Codex 路由，不是画布视频模型。不能切换时如实说明；委派遵守当前会话的多代理约束。

每轮留已完成项、可复用资产、未完成项与实际原因。已有成功付费试听不能因重读 skill 再生成。“继续完善”不自动扩成全片视频或全片重配；明确要求调试声音时，在该范围内做有上限的对照试验并保留证据。当前授权已足够时不重复询问。


0.8显示名为灵镜AI。片段右键/快捷键、效果和模型工具以软件使用指南.md与API.md 0.8节为准。剪辑模型输入须按选中片段的入点/时长截取，不能直接使用全源文件。通过/api/editor/process创建本机任务，保留editorClip上下文；回填前确认片段未移位/裁切，先审候选再放新轨道。Ctrl+D为淡入淡出、Ctrl+Alt+D才是重复，避免沿用旧快捷键。交付尺寸只改变输出文件，不改原时间线坐标。


## 配音调试台与VoiceDesign（0.9）

Qwen3-TTS-12Hz-1.7B-VoiceDesign已按用户后续授权下载并通过RTX4060 8GB离线推理验证；现行证据在软件data/voice-design-deployment.json、voice-design-download.json。此前voice_redesign_v9的“未下载/未实测”是前一阶段记录，不能覆盖新证据，也不要因此重新下载或自动生成全片。

用/api/voice-lab管理workspace内配置、试听和手动入库，合同见软件API.md 0.9节。设计模式把固定description与逐句performance分开；reference模式用已选试听/项目音频和准确原话复用声线，不支持自由表演指令。两种模式复用现有串行本机队列，每次只加载一个模型，失败不切收费云端。

试听成功只保存在media/voice-lab及独立台账，不自动注册资产。用户实际试听并选择后，才调用adopt confirmed:true存到项目资源或个人资料库；候选入库不批准整片、不替换已有对白。保持requestId幂等和workspace隔离，保留模型版本、提示词、台词、seed及来源。取消只结束对应本机任务。当前VoiceDesign只实测CUDA，音质/角色贴合度仍需用户听评。
