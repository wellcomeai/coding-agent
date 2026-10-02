export default function Login() {
  return (
    <div className="login">
      <div className="card login-card">
        <h1>⌘ Coding Agent</h1>
        <p className="muted">
          AI-агент, который работает с вашим GitHub-репозиторием: пишет код, запускает тесты, создаёт ветки и
          pull request'ы и разворачивает проекты в Timeweb Cloud.
        </p>
        <a className="btn primary big" href="/api/auth/github/login">
          Войти через GitHub
        </a>
        <p className="muted small">
          Доступ выдаётся только к тем репозиториям, которые вы выберете при установке GitHub App.
        </p>
      </div>
    </div>
  );
}
