FROM python:3.11-slim

WORKDIR /app

# 安装 Node.js（用于 alphaTab 补丁）和系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 安装 alphaTab 并打补丁
RUN npm install && node patch_min.js && node patch_min2.js

# 创建上传/输出目录
RUN mkdir -p uploads outputs

# HF Spaces 要求监听 7860 端口
EXPOSE 7860

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:7860", "--workers", "1", "--threads", "4", "--timeout", "120"]
