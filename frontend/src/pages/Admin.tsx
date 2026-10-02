import { Search } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { useApp } from "../App";
import { useToast } from "../components/ui";
import { rub } from "../lib/format";

type U = { id: number; login: string; balance_rub: number; created_at: string };

export default function Admin() {
  const { refreshMe } = useApp();
  const toast = useToast();
  const [users, setUsers] = useState<U[]>([]);
  const [q, setQ] = useState("");
  const [login, setLogin] = useState("");
  const [amount, setAmount] = useState("");
  const [comment, setComment] = useState("");

  const load = () => api<{ users: U[] }>("/api/admin/users").then((r) => setUsers(r.users));
  useEffect(() => {
    load();
  }, []);

  const list = useMemo(() => users.filter((u) => u.login.toLowerCase().includes(q.toLowerCase())), [users, q]);

  async function topup(e: FormEvent) {
    e.preventDefault();
    try {
      const r = await api("/api/admin/topup", {
        method: "POST",
        json: { login, amount_rub: parseFloat(amount.replace(",", ".")), comment: comment || null },
      });
      toast("success", `${r.login}: баланс ${rub(r.balance_rub)}`);
      setAmount("");
      setComment("");
      load();
      refreshMe();
    } catch (err: any) {
      toast("error", err.message);
    }
  }

  return (
    <div className="page">
      <h1 className="page-title">Администрирование</h1>
      <p className="page-sub">Пользователи и ручные начисления</p>
      <div className="stack">
        <form className="card card-pad row wrap" onSubmit={topup} style={{ alignItems: "flex-end" }}>
          <div className="field" style={{ width: 180 }}>
            <label>GitHub-логин</label>
            <input className="input" value={login} onChange={(e) => setLogin(e.target.value)} />
          </div>
          <div className="field" style={{ width: 130 }}>
            <label>Сумма, ₽</label>
            <input className="input" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="500 или -100" />
          </div>
          <div className="field" style={{ flex: 1, minWidth: 180 }}>
            <label>Комментарий</label>
            <input className="input" value={comment} onChange={(e) => setComment(e.target.value)} />
          </div>
          <button className="btn primary" disabled={!login || !amount}>
            Начислить
          </button>
        </form>
        <div className="card">
          <div className="card-head">
            <h3 style={{ flex: 1 }}>Пользователи · {users.length}</h3>
            <div className="input-wrap" style={{ width: 240 }}>
              <Search size={15} />
              <input className="input" placeholder="Поиск" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Логин</th>
                <th>Регистрация</th>
                <th className="num">Баланс</th>
              </tr>
            </thead>
            <tbody>
              {list.map((u) => (
                <tr key={u.id} style={{ cursor: "pointer" }} onClick={() => setLogin(u.login)}>
                  <td style={{ fontWeight: 500 }}>@{u.login}</td>
                  <td className="muted">{new Date(u.created_at).toLocaleDateString("ru-RU")}</td>
                  <td className="num">{rub(u.balance_rub)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
