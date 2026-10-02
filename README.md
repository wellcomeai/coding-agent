# Coding Agent — веб-аналог Claude Code для Timeweb Cloud

Веб-сервис, в котором пользователь входит через GitHub, выбирает свой репозиторий и ставит задачу AI-агенту.
Агент работает в изолированной песочнице: читает и меняет код, запускает тесты и сборку, коммитит в отдельную
ветку, открывает pull request и по API-токену пользователя разворачивает проект в **Timeweb Cloud Apps**.

LLM подключаются через **Timeweb AI Gateway** (Claude, GPT, Kimi, DeepSeek и др.): доступ из РФ, оплата в рублях.
Пользователи платят нам с внутреннего баланса, списание идёт за фактические токены по прайсу Timeweb с наценкой.

## Возможности MVP

- Вход через GitHub App: пользователь сам выбирает, к каким репозиториям дать доступ.
- Сессии агента: чат со стримингом ответа, вызовы инструментов видны в интерфейсе, кнопка «Остановить».
- Инструменты агента: `bash`, `read_file`, `write_file`, `edit_file`, `list_files`, `grep`,
  `git_commit_and_push`, `create_pull_request`, `pull_request_status`, `list_branches`.
- Деплой в Timeweb Cloud Apps: подбор тарифа и фреймворка, создание приложения (только после подтверждения
  пользователя), повторный деплой, статус, логи сборки и работы.
- Баланс в рублях: прайс моделей подгружается из Timeweb Cloud AI, наценка `PRICE_MARKUP`, журнал операций,
  пополнение администратором (онлайн-оплату подключим следующим этапом).
- Безопасность: токены GitHub и Timeweb зашифрованы (Fernet) и не попадают в контекст модели, все
  внешние вызовы делает сервер. Каждая сессия работает в отдельном Docker-контейнере без root,
  с лимитами CPU, RAM и числа процессов. Простаивающие песочницы удаляются, незакоммиченная работа
  сохраняется WIP-коммитом в рабочую ветку.

## Структура

```
backend/            FastAPI: API, цикл агента, песочницы, GitHub App, Timeweb API, биллинг
  app/agent/        llm.py (AI Gateway, стриминг + tool calling), tools.py, runner.py, prompts.py
  app/sandbox/      docker_sandbox.py (прод), local_sandbox.py (только для разработки)
  tests/            pytest: юнит-тесты, разбор стрима LLM, полный ход агента с git-remote
frontend/           React + Vite SPA (собирается в образ и отдаётся бэкендом)
sandbox/Dockerfile  образ песочницы: Node 22, Python 3, uv, git, ripgrep, build-essential
docker-compose.yml  app + PostgreSQL + Caddy (HTTPS) + сборка образа песочницы
docs/ARCHITECTURE.md  архитектура и план масштабирования
```

## Развёртывание на Timeweb Cloud

### 1. Сервер

Создайте облачный сервер в панели Timeweb Cloud: Ubuntu 24.04, от 4 vCPU и 8 ГБ RAM (каждой активной сессии
по умолчанию выделяется до 1 vCPU и 2 ГБ). Установите Docker:

```bash
curl -fsSL https://get.docker.com | sh
```

Направьте домен (A-запись) на IP сервера.

### 2. GitHub App

**Автоматически (рекомендуется).** Задайте в `.env` `SETUP_TOKEN` (`openssl rand -hex 16`). После запуска
откройте `https://ваш-домен/setup?token=<SETUP_TOKEN>` и нажмите «Создать GitHub App». GitHub создаст
приложение с нужными правами и адресами и вернёт ключи сервису, они сохранятся в БД в зашифрованном виде.
Затем GitHub предложит установить приложение на репозитории.

**Вручную.** GitHub → Settings → Developer settings → GitHub Apps → **New GitHub App**:

| Поле | Значение |
|---|---|
| Homepage URL | `https://ваш-домен` |
| Callback URL | `https://ваш-домен/api/auth/github/callback` |
| Expire user authorization tokens | ✅ |
| Request user authorization (OAuth) during installation | ✅ |
| Webhook → Active | ❌ (пока не нужен) |
| Repository permissions | **Contents: Read & write**, **Pull requests: Read & write**, **Checks: Read**, **Commit statuses: Read**, Metadata: Read |
| Where can this GitHub App be installed? | **Any account** |

После создания впишите в `.env` App ID, Client ID, slug (из адреса страницы приложения), Client secret и
Private key (.pem).

### 3. Ключ Timeweb AI Gateway

Панель Timeweb Cloud → **AI Gateway** → создать API-ключ (вида `sk-...`). Пополните баланс Timeweb: с него
оплачиваются токены всех пользователей. Также нужен обычный API-токен Timeweb Cloud
(«API и Terraform»), он используется только для загрузки прайса моделей.

### 4. Запуск

```bash
git clone https://github.com/wellcomeai/coding-agent.git && cd coding-agent
cp .env.example .env
nano .env        # заполнить все поля, инструкции внутри файла
docker compose up -d --build
docker compose logs -f app
```

Для `GITHUB_PRIVATE_KEY` можно взять вывод `awk 'NF {sub(/\r/, ""); printf "%s\\n",$0;}' app.pem`.

### 5. Первый вход

1. Откройте `https://ваш-домен`, войдите через GitHub и установите App на нужные репозитории.
2. Ваш логин указан в `ADMIN_GITHUB_LOGINS`, поэтому в **Настройках** появится блок администратора:
   пополните себе баланс.
3. В **Настройках** добавьте API-токен Timeweb Cloud пользователя: с ним агент сможет деплоить.
   Для деплоя также нужно один раз подключить GitHub в панели Timeweb: Apps → Создать.
4. На главной выберите репозиторий и поставьте задачу, например: «Добавь эндпоинт /health, напиши тест,
   открой PR и задеплой в Timeweb».

## Разработка

```bash
# backend
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
SECRET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
SANDBOX_PROVIDER=local uvicorn app.main:app --reload    # local — БЕЗ изоляции, только для разработки

# frontend (проксирует /api на :8000)
cd frontend && npm install && npm run dev
```

Для docker-песочниц локально соберите образ: `docker build -t coding-agent-sandbox:latest sandbox/`.
