"""Клиент Timeweb Cloud API (Apps, VCS-провайдеры, прайс Cloud AI)."""

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

    async def list_provider_repos(self, provider_id: str) -> list[dict]:
        return (await self.request("GET", f"/api/v1/vcs-provider/{provider_id}"))["repositories"]

    async def find_repository(self, full_name: str) -> tuple[str, dict] | None:
        """Найти репозиторий GitHub среди подключённых к аккаунту Timeweb провайдеров."""
        for p in await self.list_providers():
            if p.get("provider_type") != "github":
                continue
            for repo in await self.list_provider_repos(p["provider_id"]):
                if repo.get("full_name", "").lower() == full_name.lower():
                    return p["provider_id"], repo
        return None

    # --- Cloud AI (прайс) ---
    async def ai_models(self) -> list[dict]:
        return (await self.request("GET", "/api/v3/cloud-ai/models?limit=500"))["models"]
