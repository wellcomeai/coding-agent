import { AlertTriangle, CheckCircle2, ExternalLink, KeyRound, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../App";
import { GitHubMark, TimewebMark, useToast } from "../components/ui";

type TwStatus = {
  connected: boolean;
  valid?: boolean;
  error?: string;
  account?: string;
  providers?: { login: string; type: string; repos_count: number | null; error?: string }[];
  providers_error?: string;
};

function TimewebStatus({ refreshKey }: { refreshKey: number }) {
  const [st, setSt] = useState<TwStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSt(await api<TwStatus>("/api/settings/timeweb/status"));
    } catch (e: any) {
      setSt({ connected: true, valid: false, error: e.message });
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (!st) return <div className="skeleton" style={{ height: 64 }} />;
  if (!st.connected) return null;
  if (!st.valid)
    return (
      <div className="callout danger">
        <AlertTriangle size={16} />
        <div>{st.error}</div>
      </div>
    );
  const gh = (st.providers || []).filter((p) => p.type === "github" || !p.type);
  return (
    <div className="tw-status">
      <div className="row">
        <CheckCircle2 size={15} style={{ color: "var(--success)" }} />
        <span>
          Токен действителен · аккаунт Timeweb <b>{st.account}</b>
        </span>
        <div className="spacer" />
        <button className="btn ghost sm" onClick={load} disabled={loading} title="Проверить ещё раз">
          <RefreshCw size={13} className={loading ? "spin" : ""} /> Проверить
        </button>
      </div>
      {st.providers_error ? (
        <div className="callout danger">
          <AlertTriangle size={16} />
          <div>{st.providers_error}</div>
        </div>
      ) : gh.length === 0 ? (
        <div className="callout">
          <AlertTriangle size={16} style={{ color: "var(--warning)" }} />
          <div>
            В Timeweb не подключён GitHub — агент не сможет деплоить. Подключите его один раз:{" "}
            <a href="https://timeweb.cloud/my/apps/create" target="_blank" rel="noreferrer">
              Apps → Создать → «Добавить аккаунт»
            </a>
            , затем нажмите «Проверить».
          </div>
        </div>
      ) : (
        gh.map((p) => (
          <div key={p.login} className="row">
            {p.error ? (
              <AlertTriangle size={15} style={{ color: "var(--danger)" }} />
            ) : (
              <CheckCircle2 size={15} style={{ color: "var(--success)" }} />
            )}
            <span>
              GitHub в Timeweb: <b>{p.login}</b>
              <span className="muted">
                {" "}
                · {p.error ? p.error : `${p.repos_count ?? 0} репозиториев доступно для деплоя`}
              </span>
            </span>
          </div>
        ))
      )}
    </div>
  );
}

export default function Settings() {
  const { me, refreshMe } = useApp();
  const toast = useToast();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(!me.has_timeweb);
  const [statusKey, setStatusKey] = useState(0);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api<{ login: string }>("/api/settings/timeweb", { method: "PUT", json: { token } });
      toast("success", `Timeweb Cloud подключён (аккаунт ${r.login})`);
      setToken("");
      setEditing(false);
      setStatusKey((k) => k + 1);
      refreshMe();
    } catch (err: any) {
      toast("error", err.message);
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    if (!confirm("Отключить Timeweb Cloud? Агент больше не сможет деплоить.")) return;
    await api("/api/settings/timeweb", { method: "DELETE" });
    setEditing(true);
    refreshMe();
  }

  return (
    <div className="page" style={{ maxWidth: 760 }}>
      <h1 className="page-title">Настройки</h1>
      <p className="page-sub">Интеграции и доступы агента</p>

      <div className="stack">
        <div className="card">
          <div className="card-head">
            <div className="integration" style={{ flex: 1 }}>
              <span className="mark">
                <TimewebMark size={22} />
              </span>
              <div>
                <h3>Timeweb Cloud</h3>
                <p className="muted small">Деплой ваших проектов в Timeweb Cloud Apps</p>
              </div>
            </div>
            {me.has_timeweb ? (
              <span className="badge success">
                <CheckCircle2 size={12} /> Подключено
              </span>
            ) : (
              <span className="badge">Не подключено</span>
            )}
          </div>
          <div className="card-pad stack" style={{ gap: 14 }}>
            {editing ? (
              <>
                <ol className="muted" style={{ margin: 0, paddingLeft: 20, lineHeight: 1.8 }}>
                  <li>
                    Создайте API-токен в панели Timeweb:{" "}
                    <a href="https://timeweb.cloud/my/api-keys" target="_blank" rel="noreferrer">
                      API и Terraform <ExternalLink size={12} />
                    </a>
                  </li>
                  <li>
                    Подключите GitHub в Timeweb (один раз):{" "}
                    <a href="https://timeweb.cloud/my/apps/create" target="_blank" rel="noreferrer">
                      Apps → Создать <ExternalLink size={12} />
                    </a>
                  </li>
                  <li>Вставьте токен ниже</li>
                </ol>
                <form onSubmit={save} className="row">
                  <div className="input-wrap" style={{ flex: 1 }}>
                    <KeyRound size={15} />
                    <input
                      className="input"
                      type="password"
                      autoComplete="off"
                      placeholder="API-токен Timeweb Cloud"
                      value={token}
                      onChange={(e) => setToken(e.target.value)}
                    />
                  </div>
                  <button className="btn primary" disabled={token.length < 20 || busy}>
                    {busy && <Loader2 size={15} className="spin" />} Подключить
                  </button>
                  {me.has_timeweb && (
                    <button type="button" className="btn ghost" onClick={() => setEditing(false)}>
                      Отмена
                    </button>
                  )}
                </form>
              </>
            ) : (
              <>
              <TimewebStatus refreshKey={statusKey} />
              <div className="row wrap">
                <span className="muted" style={{ flex: 1 }}>
                  Агент может создавать приложения, запускать деплой и читать логи. Платные действия — только с вашего
                  подтверждения в чате.
                </span>
                <button className="btn sm" onClick={() => setEditing(true)}>
                  Заменить токен
                </button>
                <button className="btn sm danger" onClick={disconnect}>
                  Отключить
                </button>
              </div>
              </>
            )}
            <div className="row faint small">
              <ShieldCheck size={14} /> Токен хранится в зашифрованном виде и никогда не передаётся языковой модели.
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <div className="integration" style={{ flex: 1 }}>
              <span className="mark">
                <GitHubMark size={20} />
              </span>
              <div>
                <h3>GitHub</h3>
                <p className="muted small">Вы вошли как @{me.login}</p>
              </div>
            </div>
            <span className="badge success">
              <CheckCircle2 size={12} /> Подключено
            </span>
          </div>
          <div className="card-pad row wrap">
            <span className="muted" style={{ flex: 1 }}>
              Агент работает только с репозиториями, к которым вы дали доступ, и пушит только в свои ветки{" "}
              <code>agent/…</code>.
            </span>
            <a className="btn sm" href={me.install_url} target="_blank" rel="noreferrer">
              Управлять доступом <ExternalLink size={13} />
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
