<img src="web/branding/lingjing-logo.png" width="108" alt="灵镜AI品牌Logo">

# 灵镜AI

从剧本到成片的本机AI视频创作工作台。将分镜、角色与场景参考、配音试听、多轨剪辑和任务记录放在同一个项目中。

**中国海洋大学：刘小允、孙景昱、余俊杭、徐钰翔**
2026年iCAN大学生创新创业大赛 AI应用创新挑战赛，高校组，软件赛道。

## 参赛材料

| 文件 | 内容 |
|---|---|
| [应用方案 PDF](submission/01_灵镜AI_应用方案.pdf) | 20页应用方案 |
| [宣传演示 MP4](submission/02_灵镜AI_演示视频.mp4) | 3分17.60秒，1080p/24fps，动画、真实操作、旁白与字幕 |
| [提交说明与报名文案](submission/00_提交说明与报名文案.md) | 要求核对与可复制文本 |
| [答辩提纲](submission/05_答辩提纲.md) | 讲述要点与常见问题 |
| [交付校验](submission/交付校验.json) | 格式、时长、文件SHA-256及运行验证 |
| [可编辑制作工程](production/制作工程说明.md) | 动画代码、录屏、旁白、配乐及应用方案PPT源文件 |

完整交付ZIP、源码评审ZIP和制作工程ZIP在同一仓库的 [Releases](https://github.com/Xiaoyun-0922/xiaoyun-AI/releases)。这里同时保留解压后的代码与材料，便于直接审阅。

## 功能

- 独立workspace、剧本与镜头关联、专注审阅。
- 图片、声音和文字资产管理，节点连线与提示词引用编译。
- 角色声线与逐句表演分开调试，A/B试听、真实阶段进度、取消及手动入库。
- 本机语音生成、换声、转录、局部精修及口型的独立worker适配。
- 多轨剪辑、右键与快捷键、字幕、效果、预览代理及MP4导出。
- 通过Skill、CLI和HTTP API与外部Codex助手协作。

《苍头》示例包含116镜、12场及相关参考资源。这是制作计划与技术样本，**不表示整部电影已经生成**。新项目不会自动继承这些角色和素材。

## 快速运行

准备Python 3.12、Node.js 20或更新版本、FFmpeg及ffprobe，并加入PATH。

```powershell
git clone https://github.com/Xiaoyun-0922/xiaoyun-AI.git
cd xiaoyun-AI
python launch_review.py
```

Windows也可双击根目录的“启动评审.cmd”。启动器初始化示例路径，默认从8878端口启动本机服务并打开浏览器。基础Python服务只使用标准库，Node运行剪辑服务，FFmpeg/ffprobe用于媒体探测和导出。先查看已有分镜、试听和剪辑示例；结束前保存并等待导出完成。

可选Electron桌面开发方式与上述启动方式二选一，不同时对同一项目启动两套后台：

```powershell
python -c "import launch_review; launch_review.relocate()"
cd desktop
npm ci
npm start
```

桌面开发默认使用8766端口。当前仓库提供源码，不将单个exe描述为任意电脑可直接运行的完整安装包。详见[使用指南](使用指南.md)、[API](API.md)和[源码运行说明](docs/SOURCE_REVIEW.md)。

## 模型与数据

模型权重、账号、登录态及虚拟环境不入库。新电脑未安装模型时显示未就绪；已保存的演示音频可直接回放。安装与验证见[MODEL_SETUP.md](MODEL_SETUP.md)。

默认评审启动器不连接Design。云端适配是可选、依赖外部客户端版本的实验性连接，不包含账号或免费调用资格；本机失败不会自动切换付费云端。Codex是外部编排助手，项目技能位于[skills/minimax-storyboard-studio](skills/minimax-storyboard-studio/SKILL.md)。

## 工程与验证

| 位置 | 作用 |
|---|---|
| server.py / workspaces.py | 本机服务与工作区 |
| workflow.py / direction.py | 节点、引用编译与导演检查 |
| voice_lab.py / local_ai.py / tools | 声音调试与本机worker |
| web / desktop | 前端与Electron源码 |
| vendor/fablecut | 多轨剪辑内核及集成修改 |
| data / media | 独立示例项目及媒体 |
| submission / production | 参赛文件与制作源文件 |

```powershell
python -X utf8 -m unittest discover -s tests
```

本次待发布源码重新运行203项测试，全部通过。视频完成解码与格式检查，源码评审包已在新目录验证图片、试听和编辑器加载，详见[验证记录](docs/publish-verification.json)。短样本结果不是所有设备和场景的性能承诺。

## 原创与第三方

项目使用Codex辅助开发。团队定义创作工作流，实现项目组织、镜头关联、引用规则、任务与配音调试、模型适配和编辑器桥接；基础模型与FableCut剪辑内核不称为团队自研。

FableCut保留MIT许可证，其他工具与模型遵循各自许可，见[第三方说明](THIRD_PARTY_NOTICES.md)。没有为团队新增代码另行授予MIT或其他公开开源许可。团队已确认示例剧本、角色和场景图片拥有参赛及公开展示权。
