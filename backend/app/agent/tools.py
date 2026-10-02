"""Инструменты агента. Секреты (GitHub/Timeweb) никогда не попадают в контекст LLM:
инструменты выполняет сервер, подставляя токены сам."""

import base64
import json
import logging
import posixpath
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .. import github_app
from ..config import get_settings
from ..sandbox import Sandbox
from ..timeweb import TimewebClient, TimewebError

log = logging.getLogger(__name__)


class ToolError(Exception):
    pass


@dataclass
class ToolContext:
    sandbox: Sandbox
    repo: str
    installation_id: int
    base_branch: str
    work_branch: str
    timeweb: TimewebClient | None
    pr_url: str | None = None
    on_pr: Callable[[str], Awaitable[None]] | None = None
    # Токены, которые нужно вырезать из любого вывода
    secrets: list[str] = field(default_factory=list)

    async def gh_token(self) -> str:
        token = await github_app.installation_token(self.installation_id, self.repo)
        if token not in self.secrets:
            self.secrets.append(token)
        return token


def truncate(text: str, limit: int | None = None) -> str:
    limit = limit or get_settings().tool_output_limit
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n\n… [обрезано {len(text) - limit} символов] …\n\n{text[-tail:]}"


def scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
            text = text.replace(base64.b64encode(f"x-access-token:{s}".encode()).decode(), "***")
    return text


def git_auth_args(token: str) -> str:
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"-c http.https://github.com/.extraheader={shlex.quote('AUTHORIZATION: basic ' + basic)}"


def resolve(ctx: ToolContext, path: str | None) -> str:
    root = ctx.sandbox.repo_dir
    p = posixpath.normpath(posixpath.join(root, path or "."))
    if p != root and not p.startswith(root + "/"):
        raise ToolError(f"Путь вне репозитория запрещён: {path}")
    return p


async def run(ctx: ToolContext, command: str, timeout: int | None = None) -> tuple[int, str]:
    res = await ctx.sandbox.exec(command, timeout=timeout or get_settings().tool_timeout_seconds)
    out = scrub(res.output, ctx.secrets)
    if res.timed_out:
        out += f"\n[команда прервана по таймауту {timeout or get_settings().tool_timeout_seconds} c]"
    return res.exit_code, out


# ---------------- файловые и shell-инструменты ----------------


async def t_bash(ctx: ToolContext, command: str, timeout: int = 120) -> str:
    timeout = max(1, min(int(timeout), get_settings().tool_timeout_seconds))
    code, out = await run(ctx, command, timeout)
    return truncate(f"{out}\n[exit code: {code}]" if code else out or "(пустой вывод)")


async def t_read_file(ctx: ToolContext, path: str, offset: int = 1, limit: int = 2000) -> str:
    p = resolve(ctx, path)
    try:
        raw = await ctx.sandbox.read_file(p)
    except (FileNotFoundError, IsADirectoryError) as e:
        raise ToolError(f"Не удалось прочитать {path}: {e.__class__.__name__}") from e
    if b"\0" in raw[:8000]:
        return f"{path}: бинарный файл, {len(raw)} байт"
    lines = raw.decode("utf-8", errors="replace").splitlines()
    offset = max(1, int(offset))
    chunk = lines[offset - 1 : offset - 1 + int(limit)]
    body = "\n".join(f"{i:6}\t{line[:2000]}" for i, line in enumerate(chunk, start=offset))
    more = len(lines) - (offset - 1 + len(chunk))
    if more > 0:
        body += f"\n… ещё {more} строк (используйте offset)"
    return truncate(body or "(пустой файл)")


async def t_write_file(ctx: ToolContext, path: str, content: str) -> str:
    p = resolve(ctx, path)
    await ctx.sandbox.write_file(p, content.encode())
    return f"Записано {len(content)} символов в {path}"


async def t_edit_file(ctx: ToolContext, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    p = resolve(ctx, path)
    try:
        text = (await ctx.sandbox.read_file(p)).decode("utf-8")
    except FileNotFoundError as e:
        raise ToolError(f"Файл не найден: {path}") from e
    count = text.count(old_string)
    if not old_string or count == 0:
        raise ToolError("old_string не найден в файле. Прочитайте файл заново и укажите точный фрагмент.")
    if count > 1 and not replace_all:
        raise ToolError(f"old_string встречается {count} раз. Расширьте фрагмент или передайте replace_all=true.")
    text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
    await ctx.sandbox.write_file(p, text.encode())
    return f"Изменено вхождений: {count if replace_all else 1} в {path}"


async def t_list_files(ctx: ToolContext, path: str = ".", max_depth: int = 3) -> str:
    p = resolve(ctx, path)
    cmd = (
        f"find {shlex.quote(p)} -maxdepth {int(max_depth)} "
        r"\( -name .git -o -name node_modules -o -name .venv -o -name __pycache__ -o -name dist -o -name build \) -prune "
        r"-o -print | sort | head -500"
    )
    code, out = await run(ctx, cmd, 60)
    return truncate(out.replace(ctx.sandbox.repo_dir + "/", "").replace(ctx.sandbox.repo_dir, "."))


async def t_grep(ctx: ToolContext, pattern: str, path: str = ".", glob: str | None = None) -> str:
    p = resolve(ctx, path)
    include = f"--include={shlex.quote(glob)} " if glob else ""
    cmd = (
        f"grep -rnIE {include}--exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv "
        f"-e {shlex.quote(pattern)} {shlex.quote(p)} | head -300"
    )
    code, out = await run(ctx, cmd, 120)
    out = out.replace(ctx.sandbox.repo_dir + "/", "")
    return truncate(out or "Совпадений нет")


# ---------------- git / GitHub ----------------


async def t_git_commit_and_push(ctx: ToolContext, message: str) -> str:
    code, status = await run(ctx, "git add -A && git status --porcelain", 120)
    if code:
        raise ToolError(status)
    if status.strip():
        code, out = await run(ctx, f"git commit -q -m {shlex.quote(message)}", 120)
        if code:
            raise ToolError(f"git commit завершился с ошибкой:\n{out}")
    token = await ctx.gh_token()
    code, out = await run(ctx, f"git {git_auth_args(token)} push -u origin HEAD:refs/heads/{ctx.work_branch} 2>&1", 300)
    if code:
        raise ToolError(f"git push завершился с ошибкой:\n{out}")
    _, sha = await run(ctx, "git rev-parse HEAD", 30)
    return (
        f"Запушено в ветку {ctx.work_branch}, коммит {sha.strip()[:12]}.\n"
        f"https://github.com/{ctx.repo}/tree/{ctx.work_branch}"
    )


async def t_create_pull_request(ctx: ToolContext, title: str, body: str = "", draft: bool = False) -> str:
    token = await ctx.gh_token()
    try:
        existing = await github_app.find_pull_request(token, ctx.repo, ctx.work_branch)
        if existing:
            url = existing["html_url"]
            result = f"PR уже существует: {url}"
        else:
            body = (body or "") + "\n\n---\n_Создано Coding Agent_"
            pr = await github_app.create_pull_request(token, ctx.repo, ctx.work_branch, ctx.base_branch, title, body, draft)
            url = pr["html_url"]
            result = f"PR создан: {url}"
    except github_app.GitHubError as e:
        raise ToolError(f"{e}. Убедитесь, что изменения запушены (git_commit_and_push).") from e
    if ctx.on_pr:
        await ctx.on_pr(url)
    ctx.pr_url = url
    return result


async def t_pull_request_status(ctx: ToolContext) -> str:
    if not ctx.pr_url:
        raise ToolError("PR для этой сессии ещё не создан")
    token = await ctx.gh_token()
    number = int(ctx.pr_url.rstrip("/").split("/")[-1])
    return json.dumps(await github_app.get_pull_request_status(token, ctx.repo, number), ensure_ascii=False)


async def t_list_branches(ctx: ToolContext) -> str:
    token = await ctx.gh_token()
    return "\n".join(await github_app.list_branches(token, ctx.repo))


# ---------------- Timeweb Cloud ----------------


def _tw(ctx: ToolContext) -> TimewebClient:
    if not ctx.timeweb:
        raise ToolError("Токен Timeweb Cloud не подключён. Попросите пользователя добавить его в Настройках.")
    return ctx.timeweb


def _short_app(a: dict) -> dict:
    return {
        "id": a.get("id"),
        "name": a.get("name"),
        "type": a.get("type"),
        "status": a.get("status"),
        "framework": a.get("framework"),
        "branch": a.get("branch_name"),
        "repository": (a.get("repository") or {}).get("full_name"),
        "domains": [d.get("fqdn") for d in a.get("domains") or []],
        "ip": a.get("ip"),
        "location": a.get("location"),
    }


async def t_timeweb_list_apps(ctx: ToolContext) -> str:
    apps = await _tw(ctx).list_apps()
    return json.dumps([_short_app(a) for a in apps], ensure_ascii=False) if apps else "Приложений нет"


async def t_timeweb_find_repository(ctx: ToolContext) -> str:
    found = await _tw(ctx).find_repository(ctx.repo)
    if not found:
        return (
            f"Репозиторий {ctx.repo} не найден среди подключённых к Timeweb GitHub-аккаунтов. "
            "Пользователю нужно подключить GitHub в панели Timeweb: https://timeweb.cloud/my/apps/create "
            "(и выдать доступ к этому репозиторию)."
        )
    provider_id, repo = found
    return json.dumps({"provider_id": provider_id, "repository_id": repo["id"], "full_name": repo["full_name"]})


async def t_timeweb_deploy_options(ctx: ToolContext, app_type: str = "backend") -> str:
    tw = _tw(ctx)
    presets = await tw.presets()
    key = "backend_presets" if app_type == "backend" else "frontend_presets"
    items = [
        {k: p.get(k) for k in ("id", "description_short", "price", "cpu", "ram", "disk", "location")}
        for p in presets.get(key, [])
    ]
    out: dict[str, Any] = {"presets (price — руб./мес.)": items}
    try:
        out["frameworks"] = await tw.frameworks()
    except TimewebError:
        pass
    return truncate(json.dumps(out, ensure_ascii=False), 15000)


async def _sha(ctx: ToolContext, branch: str) -> str:
    try:
        return await github_app.branch_sha(await ctx.gh_token(), ctx.repo, branch)
    except github_app.GitHubError as e:
        raise ToolError(f"Ветка {branch} не найдена на GitHub — сначала сделайте git_commit_and_push") from e


async def t_timeweb_create_app(
    ctx: ToolContext,
    name: str,
    app_type: str,
    framework: str,
    preset_id: int,
    build_cmd: str,
    confirmed: bool,
    run_cmd: str | None = None,
    index_dir: str | None = None,
    env_version: str | None = None,
    envs: dict | None = None,
    branch: str | None = None,
    is_auto_deploy: bool = True,
) -> str:
    if not confirmed:
        raise ToolError("Создание приложения платное. Сначала получите явное согласие пользователя в чате.")
    tw = _tw(ctx)
    found = await tw.find_repository(ctx.repo)
    if not found:
        return await t_timeweb_find_repository(ctx)
    provider_id, repo = found
    branch = branch or ctx.work_branch
    payload: dict[str, Any] = {
        "provider_id": provider_id,
        "repository_id": repo["id"],
        "type": app_type,
        "name": name,
        "comment": "Создано Coding Agent",
        "framework": framework,
        "preset_id": int(preset_id),
        "branch_name": branch,
        "commit_sha": await _sha(ctx, branch),
        "build_cmd": build_cmd,
        "is_auto_deploy": is_auto_deploy,
        "envs": envs or {},
    }
    if app_type == "backend":
        if not run_cmd:
            raise ToolError("Для backend-приложения нужен run_cmd")
        payload["run_cmd"] = run_cmd
    else:
        payload["index_dir"] = index_dir or "/dist"
    if env_version:
        payload["env_version"] = env_version
    try:
        app = await tw.create_app(payload)
    except TimewebError as e:
        raise ToolError(str(e)) from e
    return "Приложение создано, деплой запущен:\n" + json.dumps(_short_app(app), ensure_ascii=False)


async def t_timeweb_deploy(ctx: ToolContext, app_id: str, branch: str | None = None) -> str:
    tw = _tw(ctx)
    app = await tw.get_app(str(app_id))
    branch = branch or app.get("branch_name") or ctx.work_branch
    d = await tw.deploy(str(app_id), await _sha(ctx, branch))
    return json.dumps({"deploy_id": d.get("id"), "status": d.get("status"), "commit": d.get("commit_sha")})


async def t_timeweb_app_status(ctx: ToolContext, app_id: str) -> str:
    tw = _tw(ctx)
    app = await tw.get_app(str(app_id))
    deploys = await tw.list_deploys(str(app_id), 3)
    return json.dumps(
        {
            "app": _short_app(app),
            "deploys": [
                {k: d.get(k) for k in ("id", "status", "commit_sha", "started_at", "ended_at")} for d in deploys
            ],
        },
        ensure_ascii=False,
    )


async def t_timeweb_deploy_logs(ctx: ToolContext, app_id: str, deploy_id: str | None = None) -> str:
    tw = _tw(ctx)
    if not deploy_id:
        deploys = await tw.list_deploys(str(app_id), 1)
        if not deploys:
            return "Деплоев нет"
        deploy_id = deploys[0]["id"]
    logs = await tw.deploy_logs(str(app_id), str(deploy_id))
    return truncate("\n".join(logs[-200:]) or "Логи пусты", 15000)


async def t_timeweb_app_logs(ctx: ToolContext, app_id: str) -> str:
    logs = await _tw(ctx).app_logs(str(app_id))
    return truncate("\n".join(logs[-200:]) or "Логи пусты", 15000)


# ---------------- описание для LLM ----------------


def _fn(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []},
        },
    }


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}

CORE_TOOLS: list[tuple[dict, Callable]] = [
    (
        _fn(
            "bash",
            "Run a bash command in the repository root of the sandbox. Use for builds, tests, installing deps, git status/diff/log.",
            {"command": S, "timeout": {**I, "description": "seconds, default 120, max 300"}},
            ["command"],
        ),
        t_bash,
    ),
    (
        _fn(
            "read_file",
            "Read a text file (path relative to repo root). Returns numbered lines.",
            {"path": S, "offset": {**I, "description": "1-based start line"}, "limit": {**I, "description": "max lines"}},
            ["path"],
        ),
        t_read_file,
    ),
    (_fn("write_file", "Create or overwrite a file with the full content.", {"path": S, "content": S}, ["path", "content"]), t_write_file),
    (
        _fn(
            "edit_file",
            "Replace an exact unique fragment old_string with new_string in a file.",
            {"path": S, "old_string": S, "new_string": S, "replace_all": B},
            ["path", "old_string", "new_string"],
        ),
        t_edit_file,
    ),
    (_fn("list_files", "List files of a directory tree (skips .git, node_modules).", {"path": S, "max_depth": I}), t_list_files),
    (
        _fn(
            "grep",
            "Search file contents with an extended regex. Optional glob filter like '*.py'.",
            {"pattern": S, "path": S, "glob": S},
            ["pattern"],
        ),
        t_grep,
    ),
    (
        _fn(
            "git_commit_and_push",
            "Stage all changes, commit with the message and push the working branch to GitHub.",
            {"message": S},
            ["message"],
        ),
        t_git_commit_and_push,
    ),
    (
        _fn(
            "create_pull_request",
            "Open a pull request from the working branch into the base branch (or return the existing one).",
            {"title": S, "body": {**S, "description": "markdown description"}, "draft": B},
            ["title"],
        ),
        t_create_pull_request,
    ),
    (_fn("pull_request_status", "Get state, mergeability and CI checks of this session's pull request.", {}), t_pull_request_status),
    (_fn("list_branches", "List branches of the GitHub repository.", {}), t_list_branches),
]

TIMEWEB_TOOLS: list[tuple[dict, Callable]] = [
    (_fn("timeweb_list_apps", "List the user's Timeweb Cloud Apps.", {}), t_timeweb_list_apps),
    (
        _fn("timeweb_find_repository", "Check that this GitHub repository is connected to the user's Timeweb account.", {}),
        t_timeweb_find_repository,
    ),
    (
        _fn(
            "timeweb_deploy_options",
            "Get available presets (tariffs with monthly price in RUB) and frameworks for a Timeweb App.",
            {"app_type": {"type": "string", "enum": ["backend", "frontend"]}},
        ),
        t_timeweb_deploy_options,
    ),
    (
        _fn(
            "timeweb_create_app",
            "Create a Timeweb Cloud App from this repository and start the first deploy. PAID: requires explicit user confirmation.",
            {
                "name": S,
                "app_type": {"type": "string", "enum": ["backend", "frontend"]},
                "framework": {**S, "description": "e.g. fastapi, express, django, docker, react, vue, next.js"},
                "preset_id": I,
                "build_cmd": {**S, "description": "e.g. 'npm run build' or 'pip install -r requirements.txt'"},
                "run_cmd": {**S, "description": "backend only, e.g. 'uvicorn main:app --host 0.0.0.0 --port 8080'"},
                "index_dir": {**S, "description": "frontend only, build output dir starting with '/', e.g. /dist"},
                "env_version": {**S, "description": "runtime version, e.g. '3.12' or '20'"},
                "envs": {"type": "object", "description": "environment variables"},
                "branch": {**S, "description": "branch to deploy, default: working branch"},
                "is_auto_deploy": B,
                "confirmed": {**B, "description": "true only if the user explicitly approved the paid preset"},
            },
            ["name", "app_type", "framework", "preset_id", "build_cmd", "confirmed"],
        ),
        t_timeweb_create_app,
    ),
    (
        _fn("timeweb_deploy", "Redeploy an existing app from the latest commit of its branch.", {"app_id": S, "branch": S}, ["app_id"]),
        t_timeweb_deploy,
    ),
    (_fn("timeweb_app_status", "Get app status, domains and recent deploys.", {"app_id": S}, ["app_id"]), t_timeweb_app_status),
    (
        _fn("timeweb_deploy_logs", "Get build/deploy logs (latest deploy by default).", {"app_id": S, "deploy_id": S}, ["app_id"]),
        t_timeweb_deploy_logs,
    ),
    (_fn("timeweb_app_logs", "Get runtime logs of the app.", {"app_id": S}, ["app_id"]), t_timeweb_app_logs),
]


def toolset(with_timeweb: bool) -> tuple[list[dict], dict[str, Callable]]:
    items = CORE_TOOLS + (TIMEWEB_TOOLS if with_timeweb else [])
    return [spec for spec, _ in items], {spec["function"]["name"]: fn for spec, fn in items}


async def execute(ctx: ToolContext, registry: dict[str, Callable], name: str, raw_args: str) -> tuple[str, bool]:
    """Выполнить вызов инструмента. Возвращает (вывод, is_error)."""
    fn = registry.get(name)
    if not fn:
        return f"Неизвестный инструмент: {name}", True
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
    except ValueError as e:
        return f"Некорректные аргументы JSON: {e}", True
    try:
        return scrub(await fn(ctx, **args), ctx.secrets), False
    except ToolError as e:
        return scrub(str(e), ctx.secrets), True
    except TypeError as e:
        return f"Неверные параметры для {name}: {e}", True
    except (TimewebError, github_app.GitHubError) as e:
        return scrub(str(e), ctx.secrets), True
    except Exception as e:  # noqa: BLE001  — ошибка инструмента не должна обрывать ход агента
        log.exception("Ошибка инструмента %s", name)
        return scrub(f"Внутренняя ошибка инструмента {name}: {e.__class__.__name__}: {e}", ctx.secrets), True
