# Архитектура

```
Браузер (React SPA)
   │  REST + SSE (/api/sessions/{id}/events)
   ▼
Caddy (HTTPS) → FastAPI (app)
   ├─ Auth: GitHub App, user-to-server OAuth, подписанная cookie
   ├─ Секреты: GitHub- и Timeweb-токены пользователей, Fernet (SECRET_KEY)
   ├─ PostgreSQL: users, ledger, agent_sessions (история диалога), session_events
   ├─ EventBus: события → БД + live-рассылка подписчикам SSE
   └─ AgentRunner: по одной asyncio-задаче на активную сессию
        │
        ├─ LLM: Timeweb AI Gateway (OpenAI-совместимый chat/completions, stream + tools)
        ├─ Sandbox: Docker-контейнер на сессию (uid 1000, cap_drop ALL, no-new-privileges,
        │           лимиты mem/cpu/pids), в нём git clone репозитория
        ├─ GitHub API: installation-токен, ограниченный одним репозиторием (живёт 1 час)
        └─ Timeweb Cloud API: токен пользователя (Apps, VCS, деплой, логи)
```

## Ход агента

1. Пользователь отправляет сообщение, создаётся событие `user_message` и запускается задача `_turn`.
2. Если песочницы нет, создаётся контейнер. В нём клонируется репозиторий по installation-токену:
   токен передаётся через `http.extraheader` и не сохраняется в `.git/config`. Затем делается checkout
   рабочей ветки `agent/<id>` (продолжаем её, если она уже есть на GitHub).
3. Цикл до `AGENT_MAX_STEPS` шагов:
   - проверка баланса;
   - запрос в LLM со стримингом, дельты уходят в UI как `assistant_delta`;
   - списание стоимости по фактическому `usage` (себестоимость Timeweb × `PRICE_MARKUP`);
   - выполнение вызванных инструментов, события `tool_call` и `tool_result`.
4. Ход заканчивается ответом без вызовов инструментов, остановкой пользователя или ошибкой.

## Принципы безопасности

- Токены GitHub и Timeweb не попадают ни в контекст модели, ни в песочницу на постоянной основе.
  Push, PR и Timeweb-операции выполняет сервер; любые совпадения с токенами вырезаются из вывода.
- В песочнице нет git-учётных данных: `git push` из bash не сработает, только через инструмент.
- Платные действия в Timeweb (`timeweb_create_app`) требуют явного подтверждения пользователя
  (`confirmed=true`, правило в системном промпте).
- `SANDBOX_PROVIDER=local` не изолирует ничего и предназначен только для разработки.

## Ограничения MVP и план масштабирования

| Сейчас | Дальше |
|---|---|
| Один процесс API: задачи агента и шина событий в памяти | Вынести AgentRunner в воркеры (очередь в Redis), шину событий перевести на Redis Pub/Sub. API станет stateless и масштабируется горизонтально |
| Песочницы на том же сервере через docker.sock | Отдельный пул серверов под песочницы или Managed Kubernetes Timeweb (Pod на сессию, NetworkPolicy, gVisor/Kata для изоляции), автоскейлинг нод |
| Исходящий трафик песочниц не ограничен | Egress-прокси с allowlist (npm, PyPI, GitHub) и лимиты трафика |
| Пополнение баланса администратором | Онлайн-оплата (ЮKassa / CloudPayments), тарифы и пакеты токенов |
| `create_all` вместо миграций | Alembic |
| История диалога целиком в одной JSON-колонке, простое сокращение старых выводов | Суммаризация (compaction) длинных сессий, отдельная таблица сообщений |
| Логи в stdout | Метрики (Prometheus), трейсинг, алерты на ошибки LLM/Timeweb |
| Деплой только в Timeweb Apps | Деплой на облачные серверы и в Kubernetes через Timeweb API, БД и S3 под проект |
