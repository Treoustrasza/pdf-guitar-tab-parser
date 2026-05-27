---
title: PDF Guitar Tab Parser
emoji: 🎸
colorFrom: pink
colorTo: purple
sdk: docker
pinned: false
---

# PDF Guitar Tab Parser

上传 PDF 吉他六线谱，自动解析并导出 **MusicXML**（可在 MuseScore / TuxGuitar 中打开）或 **ASCII Tab** 文本。

## 使用方法

1. 拖拽或点击上传 PDF 文件
2. 填写曲目标题和速度 BPM（可选）
3. 点击「开始转换」
4. 下载 MusicXML 或 ASCII Tab

## 本地运行

```bash
pip install -r requirements.txt
python app.py
```

访问 http://127.0.0.1:7860
