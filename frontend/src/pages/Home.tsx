import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, Me, Repo, rub, SessionInfo } from "../api";

export default function Home({ me }: { me: Me }) {
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [installUrl, setInstallUrl] = useState(me.install_url);
  const [repo, setRepo] = useState("");
  const [branches, setBranches] = useState<string[]>([]);
  const [branch, setBranch] = useState("");
  const [model, setModel] = useState(me.default_model);
  const [message, setMessage] = useState("");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ sessions: SessionInfo[] }>("/api/sessions").then((r) => setSessions(r.sessions)).catch((e) => setError(e.message));
    api<{ repos: Repo[]; install_url: string }>("/api/repos")
      .then((r) => {
        setRepos(r.repos);
        setInstallUrl(r.install_url);
        if (r.repos.length) setRepo(r.repos[0].full_name);
      })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!repo) return;
    const r = repos?.find((x) => x.full_name === repo);
    setBranch(r?.default_branch || "");
    setBranches([]);
    api<{ branches: string[] }>(`/api/repos/${repo}/branches`)
      .then((b) => setBranches(b.branches))
      .catch(() => setBranches([]));
  }, [repo, repos]);

  const filtered = useMemo(
    () => (repos || []).filter((r) => r.full_name.toLowerCase().includes(filter.toLowerCase())),
    [repos, filter],
  );

  async function create(e: FormEvent) {
    e.preventDefault();
    if (!repo || !message.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const s = await api<SessionInfo>("/api/sessions", {
        method: "POST",
        json: { repo_full_name: repo, base_branch: branch || undefined, model, message },
      });
      window.location.hash = `#/s/${s.id}`;
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="home">
      <section className="card">
        <h2>Новая задача</h2>
        {repos && repos.length === 0 && (
          <div className="notice">
            Нет доступных репозиториев. <a href={installUrl} target="_blank" rel="noreferrer">Установите GitHub App</a> и
            выберите репозитории, затем обновите страницу.
          </div>
        )}
        <form onSubmit={create} className="new-session">
          <div className="row">
            <label className="grow">
              Репозиторий
              <input placeholder="Фильтр…" value={filter} onChange={(e) => setFilter(e.target.value)} />
              <select value={repo} onChange={(e) => setRepo(e.target.value)} size={Math.min(6, Math.max(2, filtered.length))}>
                {filtered.map((r) => (
                  <option key={r.full_name} value={r.full_name}>
                    {r.private ? "🔒 " : ""}
                    {r.full_name}
                  </option>
                ))}
              </select>
              <a className="small" href={installUrl} target="_blank" rel="noreferrer">
                + Добавить репозитории
              </a>
            </label>
            <div className="col">
              <label>
                Базовая ветка
                <select value={branch} onChange={(e) => setBranch(e.target.value)}>
                  {(branches.length ? branches : [branch]).filter(Boolean).map((b) => (
                    <option key={b}>{b}</option>
                  ))}
                </select>
              </label>
              <label>
                Модель
                <select value={model} onChange={(e) => setModel(e.target.value)}>
                  {me.models.map((m) => (
                    <option key={m}>{m}</option>
                  ))}
                </select>
              </label>
            </div>
          </div>
          <label>
            Задача для агента
            <textarea
              rows={4}
              value={message}
              placeholder="Например: добавь эндпоинт /health, напиши тест и задеплой в Timeweb"
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) create(e as any);
              }}
            />
          </label>
          {error && <div className="error">{error}</div>}
          {me.balance_rub <= 0 && (
            <div className="notice">Баланс пуст — пополните его, чтобы запустить агента.</div>
          )}
          <button className="btn primary" disabled={busy || !repo || !message.trim() || me.balance_rub <= 0}>
            {busy ? "Запуск…" : "Запустить агента"}
          </button>
        </form>
      </section>

      <section className="card">
        <h2>Сессии</h2>
        {!sessions && <div className="muted">Загрузка…</div>}
        {sessions && sessions.length === 0 && <div className="muted">Пока нет сессий.</div>}
        <ul className="sessions">
          {sessions?.map((s) => (
            <li key={s.id}>
              <a href={`#/s/${s.id}`}>
                <span className={`dot ${s.status}`} />
                <span className="title">{s.title}</span>
                <span className="muted small">
                  {s.repo_full_name} · {s.work_branch} · {rub(s.cost_rub)}
                </span>
                {s.pr_url && <span className="tag">PR</span>}
              </a>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
