# Образ песочницы агента: в нём клонируется репозиторий пользователя и выполняются команды.
# Пользователь node (uid 1000) — без root и без sudo.
FROM node:22-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv python-is-python3 \
      build-essential git curl wget ca-certificates jq ripgrep unzip zip less procps \
    && rm -rf /var/lib/apt/lists/* \
    && corepack enable \
    && pip install --no-cache-dir --break-system-packages uv

RUN mkdir -p /workspace && chown 1000:1000 /workspace
USER 1000
ENV HOME=/home/node \
    PATH=/home/node/.local/bin:/home/node/.npm-global/bin:$PATH \
    NPM_CONFIG_PREFIX=/home/node/.npm-global \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    GIT_TERMINAL_PROMPT=0
WORKDIR /workspace
CMD ["sleep", "infinity"]
