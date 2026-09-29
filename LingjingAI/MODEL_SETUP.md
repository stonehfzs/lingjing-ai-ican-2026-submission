# 可选本机模型配置

项目与示例回放不要求模型权重。新生成需要自行安装相应运行环境和权重，不存在离线安装包内的免费云端账号。

## VoiceDesign

建议使用支持BF16的NVIDIA GPU。本项目在RTX4060 8GB、Windows、Python3.12、Torch2.8.0 CUDA12.8和qwen-tts0.1.1上验证；其他设备需要重新实测。

在工作台目录中创建独立环境：

```powershell
python -m venv .runtime/speech-tts
.runtime/speech-tts/Scripts/python.exe -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu128
.runtime/speech-tts/Scripts/python.exe -m pip install qwen-tts==0.1.1 soundfile huggingface_hub
.runtime/speech-tts/Scripts/python.exe tools/setup_voice_design.py
```

最后一步明确联网下载约4.52GB的固定版本文件，然后离线生成一个短样本并检查音频。只有通过本机检查后才写入ready=true。首次下载需要可访问Hugging Face，不会自动改走收费接口。

VoiceDesign仓库：https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign

固定版本：5ecdb67327fd37bb2e042aab12ff7391903235d3。模型卡标注Apache-2.0，请保留其许可证。

## 其他可选工具

Qwen3-TTS Base参考声音模式、faster-whisper、Seed-VC、dots.tts.edit与MuseTalk各自需要模型与环境。对应worker在tools目录，部署格式见local_ai.py和API.md；不要仅因文件夹存在就把模型标记为已就绪。

为避免权重体积、依赖冲突和许可证混用，本源码包不携带这些模型。已生成的演示音频与剪辑结果可直接回放。商业使用条件需要逐个核对各模型及运行库，不以FableCut的MIT许可替代模型许可。
