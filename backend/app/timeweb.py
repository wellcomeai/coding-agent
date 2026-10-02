"""Клиент Timeweb Cloud API (Apps, VCS-провайдеры, прайс Cloud AI)."""

from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from .config import get_settings


class TimewebError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Timeweb API {status}: {message}")
        self.status = status


class TimewebClient:
    def __init__(self, token: str, base_url: str | None = None):
        self.token = token
        self.base_url = base_url or get_settings().timeweb_api_url

    async def request(self, method: str, path: str, **kw) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.request(
                method,
                self.base_url + path,
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                **kw,
            )
        if r.status_code >= 400:
            try:
                body = r.json()
                msg = body.get("message") or body.get("error_code") or r.text
                if isinstance(msg, list):
                    msg = "; ".join(map(str, msg))
            except ValueError:
                msg = r.text
            raise TimewebError(r.status_code, str(msg))
        return r.json() if r.content else {}

    # --- аккаунт ---
    async def account_status(self) -> dict:
        return (await self.request("GET", "/api/v1/account/status"))["status"]

    # --- Apps ---
    async def list_apps(self) -> list[dict]:
        return (await self.request("GET", "/api/v1/apps"))["apps"]

    async def get_app(self, app_id: str) -> dict:
        return (await self.request("GET", f"/api/v1/apps/{app_id}"))["app"]

    async def create_app(self, payload: dict) -> dict:
        return (await self.request("POST", "/api/v1/apps", json=payload))["app"]

    async def update_app(self, app_id: str, payload: dict) -> dict:
        return await self.request("PATCH", f"/api/v1/apps/{app_id}", json=payload)

    async def deploy(self, app_id: str, commit_sha: str) -> dict:
        return (await self.request("POST", f"/api/v1/apps/{app_id}/deploy", json={"commit_sha": commit_sha}))["deploy"]

    async def list_deploys(self, app_id: str, limit: int = 5) -> list[dict]:
        return (await self.request("GET", f"/api/v1/apps/{app_id}/deploys?limit={limit}"))["deploys"]

    async def deploy_logs(self, app_id: str, deploy_id: str) -> list[str]:
        return (await self.request("GET", f"/api/v1/apps/{app_id}/deploy/{deploy_id}/logs"))["deploy_logs"]

    async def app_logs(self, app_id: str) -> list[str]:
        return (await self.request("GET", f"/api/v1/apps/{app_id}/logs"))["app_logs"]

    async def presets(self) -> dict:
        return await self.request("GET", "/api/v1/presets/apps")

    async def frameworks(self) -> dict:
        return await self.request("GET", "/api/v1/frameworks/apps")

    async def deploy_settings(self) -> dict:
        return await self.request("GET", "/api/v1/deploy-settings/apps")

    # --- VCS ---
    async def list_providers(self) -> list[dict]:
        return (await self.request("GET", "/api/v1/vcs-provider"))["providers"]

    async def list_provider_repos(self, provider_id: str, page_size: int = 100, max_pages: int = 50) -> list[dict]:
        """Все репозитории аккаунта VCS (с перебором страниц, если API отдаёт их порциями)."""
        repos: list[dict] = []
        seen: set[str] = set()
        for page in range(max_pages):
            try:
                body = await self.request(
                    "GET", f"/api/v1/vcs-provider/{provider_id}?limit={page_size}&offset={page * page_size}"
                )
            except TimewebError as e:
                if page == 0 and e.status in (400, 422):
                    # API не принимает параметры постраничной выдачи — берём список целиком
                    return (await self.request("GET", f"/api/v1/vcs-provider/{provider_id}")).get("repositories") or []
                raise
            batch = body.get("repositories") or []
            new = [r for r in batch if str(r.get("id") or r.get("full_name") or r.get("name")) not in seen]
            for r in new:
                seen.add(str(r.get("id") or r.get("full_name") or r.get("name")))
            repos.extend(new)
            total = (body.get("meta") or {}).get("total")
            # Конец списка: страница неполная, новых нет (API игнорирует offset) или достигли total
            if len(batch) < page_size or not new or (isinstance(total, int | float) and len(repos) >= total):
                break
        return repos

    async def diagnose_repository(self, full_name: str) -> "RepoLookup":
        """Ищет репозиторий среди всех VCS-аккаунтов Timeweb и объясняет результат."""
        result = RepoLookup(target=full_name)
        result.providers = []
        for p in await self.list_providers():
            info = {
                "provider_id": p.get("provider_id"),
                "login": p.get("login"),
                "type": str(p.get("provider_type") or p.get("provider") or "").lower(),
                "repos_count": 0,
                "sample": [],
            }
            result.providers.append(info)
            try:
                repos = await self.list_provider_repos(p["provider_id"])
            except TimewebError as e:
                info["error"] = str(e)
                continue
            info["repos_count"] = len(repos)
            info["sample"] = [r.get("full_name") or r.get("name") for r in repos[:15]]
            if result.match is None:
                for r in repos:
                    if repo_matches(full_name, r, p.get("login")):
                        result.match = (p["provider_id"], r)
                        break
        return result

    async def find_repository(self, full_name: str) -> tuple[str, dict] | None:
        """Найти репозиторий GitHub среди подключённых к аккаунту Timeweb провайдеров."""
        return (await self.diagnose_repository(full_name)).match

    # --- Базы данных ---
    async def db_types(self) -> list[dict]:
        return (await self.request("GET", "/api/v1/database-types"))["types"]

    async def db_presets(self) -> list[dict]:
        return (await self.request("GET", "/api/v2/presets/dbs"))["databases_presets"]

    async def list_databases(self) -> list[dict]:
        return (await self.request("GET", "/api/v1/databases"))["dbs"]

    async def get_database(self, cluster_id: int) -> dict:
        body = await self.request("GET", f"/api/v1/databases/{cluster_id}")
        return body.get("db") or body.get("database") or body

    async def create_database(self, payload: dict) -> dict:
        body = await self.request("POST", "/api/v1/databases", json=payload)
        return body.get("db") or body.get("database") or body

    # --- Cloud AI (прайс) ---
    async def ai_models(self) -> list[dict]:
        return (await self.request("GET", "/api/v3/cloud-ai/models?limit=500"))["models"]


def _norm(value: str | None) -> str:
    return (value or "").strip().strip("/").lower().removesuffix(".git")


def repo_matches(full_name: str, repo: dict, provider_login: str | None = None) -> bool:
    """Сравнение без учёта регистра по full_name, по логину аккаунта + имени и по URL репозитория."""
    target = _norm(full_name)
    owner, _, name = target.partition("/")
    candidates = {_norm(repo.get("full_name"))}
    if repo.get("name"):
        candidates.add(_norm(repo.get("name")) if "/" in (repo.get("name") or "") else f"{_norm(provider_login)}/{_norm(repo.get('name'))}")
    if repo.get("url"):
        candidates.add(_norm(urlparse(str(repo["url"])).path))
    candidates.discard("")
    if target in candidates:
        return True
    # Имя совпадает, а владелец не указан в ответе API — считаем совпадением, если логин аккаунта тот же
    return bool(name) and _norm(repo.get("name")) == name and (not provider_login or _norm(provider_login) == owner)


@dataclass
class RepoLookup:
    target: str
    providers: list[dict] = field(default_factory=list)
    match: tuple[str, dict] | None = None

    def summary(self) -> str:
        """Человекочитаемое объяснение для агента и пользователя."""
        if self.match:
            pid, repo = self.match
            return f"Репозиторий {self.target} подключён в Timeweb (аккаунт провайдера {pid}, repository_id {repo.get('id')})."
        if not self.providers:
            return (
                "В аккаунте Timeweb не подключён ни один GitHub-аккаунт. Пользователю нужно подключить GitHub в панели "
                "Timeweb: https://timeweb.cloud/my/apps/create → «Добавить аккаунт»."
            )
        lines = [f"Репозиторий {self.target} не найден в Timeweb. Что видно через API:"]
        for p in self.providers:
            line = f"- {p.get('type') or '?'} аккаунт «{p.get('login')}»: "
            if p.get("error"):
                line += f"ошибка при получении репозиториев — {p['error']}"
            else:
                line += f"{p['repos_count']} репоз." + (f" (например: {', '.join(map(str, p['sample'][:10]))})" if p["sample"] else "")
            lines.append(line)
        lines.append(
            "Проверьте, что в установке GitHub-приложения Timeweb выдан доступ к этому репозиторию: "
            "https://github.com/settings/installations → Timeweb → Configure."
        )
        return "\n".join(lines)


HIDDEN_ENV = "hidden-by-api-key-policy"


def db_public_ip(db: dict) -> str | None:
    for net in db.get("networks") or []:
        if net.get("type") == "public":
            for ip in net.get("ips") or []:
                if ip.get("type") in ("ipv_4", "ipv4") and ip.get("ip"):
                    return ip["ip"]
    return None


def db_family(db_type: str) -> str:
    t = (db_type or "").lower()
    for fam in ("postgres", "mysql", "redis", "valkey", "mongodb", "clickhouse", "opensearch", "kafka", "rabbitmq"):
        if t.startswith(fam):
            return fam
    return t


SCHEMES = {"postgres": "postgresql", "mysql": "mysql", "redis": "redis", "valkey": "redis", "mongodb": "mongodb"}
