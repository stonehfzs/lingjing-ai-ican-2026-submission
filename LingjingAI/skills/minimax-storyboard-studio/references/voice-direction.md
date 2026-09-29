# 选音和表演是两次决定

先定位机械感：年龄/形象不合、播音腔、均匀重音、同样停顿、过分低慢、喘息夸张、台词密度不合时长，还是干声缺少空间关系。不要一律降 speed 或加气声。

角色卡区分 facts、interpretation、arc。voiceDesign 简述音区、质地、身体感和自然说话习惯，少用叠加否定。基础音色不要绑定单场悲伤/愤怒。auditionCases 使用真实台词，记录说给谁、想让对方做什么、潜台词、句内转折、目标时长；普通对白、关系变化、关键情绪选代表案例。角色只有一句就只用那一句，不为凑长度补写成片台词。

优先保持身份、调整表演。比较声线须用同句原文、同模型、同参数/标记与播放音量。voice_design 预览不能与正式 TTS 假装同等条件。选音前只做小样，不全片重配；没有实际听评能力就报告未听评，非静音/波形/响度不能证明自然或适配形象。

Studio 的 Speech 2.8 适配支持 speed/pitch/vol/language_boost/emotion 等，以实时目录为准。situation/listener/intention 不是该接口的自由表演参数，不能拼进 text 让模型念导演说明。

performance 单独保存受控标记，canonical text 保持原句：

```json
{"text":"我想走。可走了，那天的事就算过去了？","performance":[{"after":"我想走。","pause":0.28}],"params":{"speed":0.96,"pitch":0,"vol":1,"language_boost":"Chinese"}}
```

after 必须在原句唯一匹配；0.01–2 秒内部停顿或少量 breath/inhale/exhale/sighs/chuckle 标记按 [官方 T2A 文档](https://platform.minimax.io/docs/api-reference/speech-t2a-http) 编译。不改字幕、不每句加吸气。音色过期先核对报错，不自动再付费设计。

保留角色形象、场景、版本、模型、真实音色 ID、参数、原句/提交文本、任务 ID、音频时长/文件与听评决定。听评维度：形象/年龄、自然交流、角色辨识、情境转折、台词准确、呼吸节奏。不能自动给自己打满分。

voice_identity 是演员身份，dialogue_performance 是本镜精确表演，旁白不驱动画内口型。群声需层次与独立声音，回放用同一批准文件。声音先定时，再做口型或视频音频条件，最后单独铺环境/拟声/音乐，不能把完整混音塞给视频模型期待分轨保持。
