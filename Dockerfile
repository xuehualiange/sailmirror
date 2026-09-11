# 出海镜 SailMirror — 全平台部署镜像（Railway / Sealos / 任意容器云）
FROM python:3.12-slim

WORKDIR /app

# 先装依赖，利用构建缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝全部项目文件
COPY . .

# Flask 服务端口（平台通过 PORT 环境变量注入时自动适配）
EXPOSE 8080

# 启动 Web 服务（同时托管前端与 /detect API）
CMD ["python", "api_server.py"]
