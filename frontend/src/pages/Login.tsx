import { Cloud, GitPullRequest, ShieldCheck, TerminalSquare } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { GitHubMark, Logo } from "../components/ui";

const FEATURES = [
  { icon: TerminalSquare, t: "Работает как разработчик", d: "Читает код, пишет изменения, запускает тесты и сборку" },
  { icon: GitPullRequest, t: "Ветки и pull request'ы", d: "Каждая задача — отдельная ветка и аккуратный PR" },
  { icon: Cloud, t: "Деплой в Timeweb Cloud", d: "Разворачивает проект в Apps и чинит ошибки сборки" },
  { icon: ShieldCheck, t: "Безопасно", d: "Изолированная песочница, доступ только к выбранным репозиториям" },
];

export default function Login() {
  const [configured, setConfigured] = useState<boolean | null>(null);
  useEffect(() => {
    api<{ github_configured: boolean }>("/api/status")
      .then((s) => setConfigured(s.github_configured))
      .catch(() => setConfigured(true));
  }, []);

  return (
    <div className="login">
      <section className="login-left">
        <div className="brand" style={{ color: "#fff" }}>
          <Logo size={30} /> Coding Agent
        </div>
        <div>
          <h1>AI-разработчик для ваших репозиториев</h1>
          <p className="lead">
            Опишите задачу словами — агент напишет код, проверит его, откроет pull request и задеплоит проект в Timeweb
            Cloud. Лучшие модели, оплата в рублях.
          </p>
          <div className="features">
            {FEATURES.map((f) => (
              <div key={f.t} className="feature">
                <span className="ic">
                  <f.icon size={17} />
                </span>
                <div>
                  <div className="t">{f.t}</div>
                  <div className="d">{f.d}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div style={{ fontSize: 13, color: "rgba(255,255,255,.5)" }}>Claude · GPT · Kimi · DeepSeek — через Timeweb AI Gateway</div>
      </section>
      <section className="login-right">
        <div className="login-card">
          <div style={{ display: "grid", placeItems: "center" }}>
            <Logo size={44} />
          </div>
          <h2>Вход в Coding Agent</h2>
          <p className="muted" style={{ margin: "0 0 24px" }}>
            Войдите через GitHub, чтобы подключить репозитории
          </p>
          {configured === false ? (
            <div className="callout" style={{ textAlign: "left" }}>
              <div>Сервис ещё не настроен: администратору нужно создать GitHub App на странице /setup.</div>
            </div>
          ) : (
            <a className="btn lg block gh-btn" href="/api/auth/github/login">
              <GitHubMark size={18} /> Продолжить с GitHub
            </a>
          )}
          <p className="faint small" style={{ marginTop: 18 }}>
            Мы запросим доступ только к тем репозиториям, которые вы выберете.
          </p>
        </div>
      </section>
    </div>
  );
}
