# wechat-route v1.0.0 — 微信 iLink 消息路由代理
# 纯 Python stdlib，镜像 ~56MB
FROM alpine:3.21

WORKDIR /app

# Install Python 3 + ca-certificates only; remove pip/build tools after use
RUN apk add --no-cache python3 ca-certificates && \
    find /usr/lib/python3.* -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; \
    rm -rf /usr/lib/python3.*/test /usr/lib/python3.*/idlelib /usr/lib/python3.*/tkinter /usr/lib/python3.*/turtle* 2>/dev/null; \
    true

# Symlink python3 for consistency
RUN ln -sf python3 /usr/bin/python

# Copy project files
COPY router.py agents.json ./
COPY bin/ ./bin/

# Runtime data directory
RUN mkdir -p logs

# Proxy ports
EXPOSE 19998 19999

# Default: launch the router directly
CMD ["python3", "router.py"]
