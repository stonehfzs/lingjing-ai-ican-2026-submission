# 灵镜AI 0.11 源码评审包

参赛项目：中国海洋大学，刘小允、孙景昱、余俊杭、徐钰翔。
2026年iCAN大学生创新创业大赛 AI应用创新挑战赛，高校组，软件赛道。

## 快速运行

这是可运行源码与独立示例工程，不是携带全部模型的离线安装器。

1. 安装 Python 3.12、Node.js 20或更新版本，以及包含ffmpeg和ffprobe的FFmpeg，并加入PATH。
2. 解压整个目录，Windows双击“启动评审.cmd”；也可在终端运行 `python launch_review.py`。
3. 启动器转换示例文件路径、检查依赖，在127.0.0.1上启动服务并打开浏览器。默认8878端口。
4. 进入《苍头 · 参赛演示》，查看剧本、分镜、资源、节点、既有试听和多轨剪辑。示例静帧标记为参考预演。
5. 验证剪辑：选择片段，Ctrl+K分割，Ctrl+Z撤销；Ctrl+C/V复制粘贴；右键检查音画分离、效果与模型处理；导出一小段MP4并回看。

基础服务只使用Python标准库。Node运行多轨剪辑服务，FFmpeg/ffprobe用于探测、代理、声音处理和导出。Python/PIL可选，用于辅助读取静态图片尺寸。

停止：关闭启动器终端即可停止当前评审服务。关闭浏览器前先保存剪辑并等待导出完成。

## AI能力与配置

示例的两条试听由本机Qwen实际生成，可直接播放。新电脑未安装模型时，生成入口显示未就绪，不会自动联网下载或调用收费接口。
配置本机VoiceDesign见MODEL_SETUP.md；工具worker保留在tools目录。其余可选引擎的接口与部署字段见API.md。
云端Design客户端适配是可选、依赖外部客户端版本的实验性接口；不包含任何账号、token或免费调用资格。未登录/未配置时可继续使用项目编辑、已有结果与本机剪辑。
Codex是外部工具编排助手。技能在skills/minimax-storyboard-studio，配合CLI和HTTP API读取并保存项目，不假称软件自带通用大模型。

## 工程目录

- server.py、workspaces.py：HTTP服务、工作区和任务范围。
- workflow.py、direction.py：节点、引用编译与导演检查。
- voice_lab.py、local_ai.py、tools/：声音调试与本机worker。
- web/：创作工作台前端；desktop/：Electron源码，可另行npm install后启动。
- vendor/fablecut/：MIT剪辑内核及本地集成修改，原许可证保留。
- data/、media/、personal-library/：隔离的演示数据与可复用素材。
- API.md、使用指南.md：操作与接口。旧版本章节是历史记录，实际启动路径以本README为准。

## 复现与测试

`python -m unittest discover -s tests` 执行隔离单元测试。测试依赖模拟输入；部分媒体集成测试需要FFmpeg。
源码中的历史工具验证记录不代表每台电脑均可达到相同速度。录制设备为RTX4060 8GB，VoiceDesign短样本峰值分配显存约4.01GiB。
不得把116镜计划误写为116条已生成视频。原始演示素材是团队原创或经团队确认已有展示授权。

## 原创与第三方

项目围绕实际需求定义工作流并使用Codex辅助开发。团队新增的项目组织、引用规则、任务与配音调试、编辑器桥接等代码与第三方代码分开说明。FableCut采用MIT许可；Qwen模型卡标注Apache-2.0。其他运行时及可选模型遵循各自许可。
本评审包未替团队新增代码选择公开开源许可，不将第三方MIT许可证扩展为整套项目的许可。第三方来源和许可证见THIRD_PARTY_NOTICES.md及vendor中的LICENSE。
