from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Общие ---
    app_name: str = "Coding Agent"
    app_version: str = "dev"
    app_branch: str = ""
    app_repo: str = "wellcomeai/coding-agent"
    # Публичный адрес сервиса (без завершающего слэша), например https://agent.example.ru
    public_url: str = "http://localhost:8000"
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    # Мастер-ключ для шифрования токенов пользователей (Fernet, 32 байта base64).
    # Сгенерировать: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    secret_key: str = Field(default="", description="Fernet key")
    # Ключ для подписи cookie сессии
    session_secret: str = "change-me"
    cookie_secure: bool = False
    # Логины GitHub администраторов (через запятую): могут пополнять балансы
    admin_github_logins: str = ""
    frontend_dist: str = "../frontend/dist"
    # Одноразовый токен для страницы /setup (автосоздание GitHub App)
    setup_token: str = ""

    # --- GitHub App ---
    github_app_id: str = ""
    github_app_slug: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    # PEM приватного ключа GitHub App (можно с \n вместо переносов) или путь к файлу
    github_private_key: str = ""
    github_private_key_path: str = ""
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"

    # --- LLM: Timeweb AI Gateway (OpenAI-совместимый) ---
    ai_gateway_base_url: str = "https://api.timeweb.ai/v1"
    ai_gateway_api_key: str = ""
    # Модели, доступные пользователям (имена как их принимает gateway), через запятую.
    agent_models: str = "anthropic/claude-sonnet-5,openai/gpt-5.3-codex,moonshot/kimi-k2.7-code,deepseek/deepseek-v4-pro"
    default_model: str = "anthropic/claude-sonnet-5"
    agent_max_steps: int = 80
    agent_max_output_tokens: int = 16000
    llm_timeout_seconds: int = 300

    # --- Биллинг ---
    # Наш токен Timeweb Cloud: используется для загрузки прайса моделей Cloud AI
    timeweb_api_token: str = ""
    timeweb_api_url: str = "https://api.timeweb.cloud"
    # Наценка на себестоимость токенов. Если 0 — считается автоматически:
    #   (1 + target_margin) * (1 + infra_overhead) / (1 - payment_fee - tax_rate)
    price_markup: float = 0.0
    target_margin: float = 0.25  # чистая прибыль сверх всех расходов
    payment_fee: float = 0.05  # комиссия платёжной системы (Робокасса)
    tax_rate: float = 0.06  # налог с выручки (УСН 6%)
    infra_overhead: float = 0.05  # серверы песочниц и прочая инфраструктура
    # Цена по умолчанию (руб. за 1M токенов), если модели нет в прайсе Timeweb
    default_price_in_per_m: float = 500.0
    default_price_out_per_m: float = 2500.0
    # Стартовый бонус новым пользователям, руб.
    signup_bonus_rub: float = 0.0

    # --- Робокасса ---
    robokassa_login: str = ""
    robokassa_password1: str = ""
    robokassa_password2: str = ""
    robokassa_hash: str = "md5"  # md5 | sha256 | sha512 (как в настройках магазина)
    robokassa_test: bool = False
    robokassa_test_password1: str = ""
    robokassa_test_password2: str = ""
    # Передавать чек для фискализации (54-ФЗ), если магазин подключён к облачной кассе
    robokassa_receipt: bool = False
    robokassa_receipt_sno: str = "usn_income"
    robokassa_receipt_tax: str = "none"
    topup_min_rub: int = 10
    topup_max_rub: int = 100_000
    topup_packages: str = "300,1000,3000,10000"

    # --- Песочницы ---
    sandbox_provider: str = "docker"  # docker | local (local — только для разработки!)
    sandbox_image: str = "coding-agent-sandbox:latest"
    sandbox_memory: str = "2g"
    sandbox_cpus: float = 1.0
    sandbox_pids_limit: int = 512
    sandbox_network: str = "bridge"
    sandbox_idle_minutes: int = 30
    sandbox_local_root: str = "./data/sandboxes"
    tool_timeout_seconds: int = 300
    tool_output_limit: int = 30000

    @property
    def github_configured(self) -> bool:
        return bool(self.github_app_id and self.github_client_id and self.github_client_secret and self.github_private_key_pem())

    @property
    def markup(self) -> float:
        if self.price_markup > 0:
            return self.price_markup
        return (1 + self.target_margin) * (1 + self.infra_overhead) / (1 - self.payment_fee - self.tax_rate)

    @property
    def robokassa_enabled(self) -> bool:
        return bool(self.robokassa_login and self.robokassa_password1 and self.robokassa_password2)

    @property
    def packages(self) -> list[int]:
        return [int(x) for x in self.topup_packages.split(",") if x.strip()]

    @property
    def admin_logins(self) -> set[str]:
        return {x.strip().lower() for x in self.admin_github_logins.split(",") if x.strip()}

    @property
    def models(self) -> list[str]:
        return [m.strip() for m in self.agent_models.split(",") if m.strip()]

    def github_private_key_pem(self) -> str:
        if self.github_private_key:
            return self.github_private_key.replace("\\n", "\n")
        if self.github_private_key_path:
            with open(self.github_private_key_path) as f:
                return f.read()
        return ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
