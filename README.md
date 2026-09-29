# 灵镜AI · iCAN 2026 提交仓库

本仓库包含比赛提交所需的应用方案、演示视频和可运行程序源码。灵镜AI是本机 AI 视频创作工作台；评审可以先观看视频，再在 Windows 本机启动源码查看《苍头》示例工作区。

## 交付文件

- [应用方案 PDF](./01_灵镜AI_应用方案.pdf)：20 页。
- [演示视频 MP4](./02_灵镜AI_演示视频.mp4)：3 分 17.6 秒，1920×1080。
- [可运行源码](./LingjingAI/)：启动器、前后端、剪辑组件、示例数据及测试。详细功能和限制见[源码说明](./LingjingAI/README.md)。

## 本机启动

准备 Windows、Python 3.12、Node.js 20 或更新版本，以及 FFmpeg/ffprobe；把后三者加入 PATH。使用 Git LFS 获取完整 PDF 和视频：

```powershell
git lfs install
git clone https://github.com/stonehfzs/lingjing-ai-ican-2026-submission.git
cd lingjing-ai-ican-2026-submission\LingjingAI
python -X utf8 launch_review.py
```

启动器默认在本机 `http://127.0.0.1:8878/` 打开示例工作区。也可双击源码目录的 `启动评审.cmd`。基础工作台和已有演示结果可以本机查看；新生成任务需要按[模型配置说明](./LingjingAI/MODEL_SETUP.md)另行准备模型。模型权重、账号和密钥不包含在仓库中。

在上述 `LingjingAI` 目录运行源码测试：

```powershell
python -X utf8 -m unittest discover -s tests
```

本仓库不提供公网部署地址或单文件安装包；比赛所需的“可运行程序”采用源码提交方式。
