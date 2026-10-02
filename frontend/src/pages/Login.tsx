import { useEffect, useState } from "react";
import { api } from "../api";

export default function Login() {
  const [configured, setConfigured] = useState<boolean | null>(null);

  useEffect(() => {
    api<{ github_configured: boolean }>("/api/status")
      .then((s) => setConfigured(s.github_configured))
      .catch(() => setConfigured(true));
  }, []);

  return (
    <div className="login">
      <div className="card login-card">
        <h1>⌘ Coding Agent</h1>
        <p className="muted">
          AI-агент, который работает с вашим GitHub-репозиторием: пишет код, запускает тесты, создаёт ветки и
          pull request'ы и разворачивает проекты в Timeweb Cloud.
        </p>
        {configured === false ? (
          <div className="notice">
            Сервис ещё не настроен: администратору нужно создать GitHub App на странице <code>/setup</code>{" "}
            (ссылка с токеном настройки).
          </div>
        ) : (
          <a className="btn primary big" href="/api/auth/github/login">
            Войти через GitHub
          </a>
        )}
        <p className="muted small">
          Доступ выдаётся только к тем репозиториям, которые вы выберете при установке GitHub App.
        </p>
      </div>
    </div>
  );
}
