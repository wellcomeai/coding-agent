import { FormEvent, useEffect, useState } from "react";
import { api, Me, rub } from "../api";

type Billing = {
  balance_rub: number;
  prices: { model: string; input_per_m_rub: number; output_per_m_rub: number }[];
  ledger: { id: number; amount_rub: number; kind: string; session_id: string | null; meta: any; created_at: string }[];
};

const KIND: Record<string, string> = { topup: "Пополнение", usage: "Агент", bonus: "Бонус", adjust: "Корректировка" };

export default function Settings({ me, onChange }: { me: Me; onChange: () => void }) {
  const [token, setToken] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [billing, setBilling] = useState<Billing | null>(null);

  const load = () => api<Billing>("/api/billing").then(setBilling).catch((e) => setErr(e.message));
  useEffect(() => {
    load();
  }, []);

  async function saveToken(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    try {
      const r = await api<{ login: string }>("/api/settings/timeweb", { method: "PUT", json: { token } });
      setMsg(`Токен сохранён (аккаунт Timeweb: ${r.login})`);
      setToken("");
      onChange();
    } catch (e: any) {
      setErr(e.message);
    }
  }

  async function removeToken() {
    await api("/api/settings/timeweb", { method: "DELETE" });
    setMsg("Токен удалён");
    onChange();
  }

  return (
    <div className="settings">
      <section className="card">
        <h2>Timeweb Cloud</h2>
        <p className="muted">
          API-токен нужен, чтобы агент создавал и деплоил приложения (Apps) в вашем аккаунте Timeweb Cloud. Создать
          токен: <a href="https://timeweb.cloud/my/api-keys" target="_blank" rel="noreferrer">панель → API и Terraform</a>.
          Токен хранится в зашифрованном виде и не передаётся модели.
        </p>
        <p className="muted small">
          Для деплоя также подключите GitHub в панели Timeweb:{" "}
          <a href="https://timeweb.cloud/my/apps/create" target="_blank" rel="noreferrer">Apps → Создать</a>.
        </p>
        {me.has_timeweb ? (
          <div className="row">
            <span className="tag ok">Токен подключён</span>
            <button className="btn danger-outline" onClick={removeToken}>
              Отключить
            </button>
          </div>
        ) : null}
        <form onSubmit={saveToken} className="row">
          <input
            className="grow"
            type="password"
            placeholder={me.has_timeweb ? "Заменить токен…" : "Вставьте API-токен Timeweb Cloud"}
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
          <button className="btn primary" disabled={token.length < 20}>
            Сохранить
          </button>
        </form>
        {msg && <div className="ok-msg">{msg}</div>}
        {err && <div className="error">{err}</div>}
      </section>

      <section className="card">
        <h2>Баланс: {billing ? rub(billing.balance_rub) : "…"}</h2>
        <p className="muted small">Оплата онлайн появится позже. Пока баланс пополняет администратор.</p>
        {billing && (
          <>
            <h3>Цены моделей (за 1 млн токенов)</h3>
            <table>
              <thead>
                <tr>
                  <th>Модель</th>
                  <th>Вход</th>
                  <th>Выход</th>
                </tr>
              </thead>
              <tbody>
                {billing.prices.map((p) => (
                  <tr key={p.model}>
                    <td>{p.model}</td>
                    <td>{rub(p.input_per_m_rub)}</td>
                    <td>{rub(p.output_per_m_rub)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h3>История операций</h3>
            <table>
              <tbody>
                {billing.ledger.map((l) => (
                  <tr key={l.id}>
                    <td className="muted small">{new Date(l.created_at).toLocaleString("ru-RU")}</td>
                    <td>{KIND[l.kind] ?? l.kind}</td>
                    <td className="small muted">
                      {l.kind === "usage"
                        ? `${l.meta?.model ?? ""} · ${l.meta?.prompt_tokens ?? 0}/${l.meta?.completion_tokens ?? 0} ток.`
                        : l.meta?.comment ?? ""}
                      {l.session_id && (
                        <>
                          {" "}
                          · <a href={`#/s/${l.session_id}`}>сессия</a>
                        </>
                      )}
                    </td>
                    <td className={l.amount_rub < 0 ? "neg" : "pos"}>{rub(l.amount_rub)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>

      {me.is_admin && <Admin onDone={load} />}
    </div>
  );
}

function Admin({ onDone }: { onDone: () => void }) {
  const [users, setUsers] = useState<{ id: number; login: string; balance_rub: number }[]>([]);
  const [login, setLogin] = useState("");
  const [amount, setAmount] = useState("");
  const [comment, setComment] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => api("/api/admin/users").then((r) => setUsers(r.users));
  useEffect(() => {
    load();
  }, []);

  async function topup(e: FormEvent) {
    e.preventDefault();
    try {
      const r = await api("/api/admin/topup", {
        method: "POST",
        json: { login, amount_rub: parseFloat(amount), comment: comment || null },
      });
      setMsg(`${r.login}: баланс ${rub(r.balance_rub)}`);
      setAmount("");
      load();
      onDone();
    } catch (err: any) {
      setMsg(err.message);
    }
  }

  return (
    <section className="card">
      <h2>Администрирование</h2>
      <form onSubmit={topup} className="row">
        <input placeholder="GitHub-логин" value={login} onChange={(e) => setLogin(e.target.value)} />
        <input placeholder="Сумма, ₽" value={amount} onChange={(e) => setAmount(e.target.value)} />
        <input className="grow" placeholder="Комментарий" value={comment} onChange={(e) => setComment(e.target.value)} />
        <button className="btn primary" disabled={!login || !amount}>
          Пополнить
        </button>
      </form>
      {msg && <div className="muted">{msg}</div>}
      <table>
        <tbody>
          {users.map((u) => (
            <tr key={u.id} onClick={() => setLogin(u.login)} className="clickable">
              <td>{u.login}</td>
              <td>{rub(u.balance_rub)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
