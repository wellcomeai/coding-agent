import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    login: Mapped[str] = mapped_column(String(100))
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Токены хранятся зашифрованными (Fernet)
    gh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    gh_refresh_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    gh_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeweb_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Баланс в микрорублях (1 руб. = 1_000_000)
    balance_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LedgerEntry(Base):
    __tablename__ = "ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_micro: Mapped[int] = mapped_column(BigInteger)  # >0 пополнение, <0 списание
    kind: Mapped[str] = mapped_column(String(20))  # topup | usage | bonus | adjust
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="Новая сессия")
    repo_full_name: Mapped[str] = mapped_column(String(255))
    installation_id: Mapped[int] = mapped_column(BigInteger)
    base_branch: Mapped[str] = mapped_column(String(255))
    work_branch: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(100))
    # idle | running | error
    status: Mapped[str] = mapped_column(String(20), default="idle")
    sandbox_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # История диалога в формате OpenAI messages (JSON-строка)
    history_json: Mapped[str] = mapped_column(Text, default="[]")
    cost_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SessionEvent(Base):
    __tablename__ = "session_events"
    __table_args__ = (UniqueConstraint("session_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppConfig(Base):
    """Настройки, созданные через веб-мастер (например, GitHub App). Значения зашифрованы."""

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Payment(Base):
    """Пополнение баланса через платёжную систему. id используется как InvId Робокассы."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_kop: Mapped[int] = mapped_column(BigInteger)  # сумма в копейках
    provider: Mapped[str] = mapped_column(String(20), default="robokassa")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | paid
    is_test: Mapped[bool] = mapped_column(default=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TimewebDatabase(Base):
    """Управляемая БД Timeweb, созданная агентом. Пароль известен только серверу (зашифрован)."""

    __tablename__ = "timeweb_databases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    cluster_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(255))
    db_type: Mapped[str] = mapped_column(String(50))
    db_name: Mapped[str] = mapped_column(String(255))
    login: Mapped[str] = mapped_column(String(100))
    password_enc: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SandboxState(Base):
    """Файлы из домашнего каталога песочницы, которые переживают её удаление (например, состояние
    deploy_timeweb.py с паролями базы и админа). Хранятся зашифрованными: {путь: base64} в JSON."""

    __tablename__ = "sandbox_states"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    files_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
