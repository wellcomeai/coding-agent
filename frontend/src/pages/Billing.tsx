import { ArrowDownLeft, CheckCircle2, Gift, Loader2, Sparkles, XCircle, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../App";
import { TopUpForm, useBilling } from "../components/TopUpModal";
import { modelLabel, rub } from "../lib/format";

const KIND: Record<string, { label: string; icon: any; cls?: string }> = {
  topup: { label: "Пополнение", icon: ArrowDownLeft, cls: "in" },
  bonus: { label: "Бонус", icon: Gift, cls: "in" },
  adjust: { label: "Корректировка", icon: Sparkles },
  usage: { label: "Работа агента", icon: Zap },
};

export default function Billing({ query }: { query: URLSearchParams }) {
  const { refreshMe } = useApp();
  const { info, reload } = useBilling();
  const paidId = query.get("paid");
  const failed = query.has("failed");
  const [payState, setPayState] = useState<"waiting" | "paid" | "timeout" | null>(paidId ? "waiting" : null);

  // После возврата из Робокассы ждём серверное уведомление об оплате
  useEffect(() => {
    if (!paidId) return;
    let n = 0;
    const t = setInterval(async () => {
      n++;
      try {
        const p = await api<{ status: string }>(`/api/billing/payments/${paidId}`);
        if (p.status === "paid") {
          setPayState("paid");
          clearInterval(t);
          reload();
          refreshMe();
        }
      } catch {
        /* ignore */
      }
      if (n > 30) {
        setPayState("timeout");
        clearInterval(t);
      }
    }, 2000);
    return () => clearInterval(t);
  }, [paidId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Группируем списания агента по сессии и дню, чтобы история не была простынёй
  const ledger = (() => {
    if (!info) return [];
    const out: { key: string; kind: string; amount: number; date: string; session: string | null; meta: any; count: number }[] = [];
    for (const l of info.ledger) {
      const day = l.created_at.slice(0, 10);
      const prev = out[out.length - 1];
      if (l.kind === "usage" && prev && prev.kind === "usage" && prev.session === l.session_id && prev.date.slice(0, 10) === day) {
        prev.amount += l.amount_rub;
        prev.count++;
      } else out.push({ key: String(l.id), kind: l.kind, amount: l.amount_rub, date: l.created_at, session: l.session_id, meta: l.meta, count: 1 });
    }
    return out;
  })();

  return (
    <div className="page">
      <h1 className="page-title">Баланс и оплата</h1>
      <p className="page-sub">Платите только за то, что использует агент: стоимость считается по фактическим токенам.</p>

      {payState === "waiting" && (
        <div className="callout accent" style={{ marginBottom: 16 }}>
          <Loader2 size={17} className="spin" /> Проверяем оплату… Обычно это занимает несколько секунд.
        </div>
      )}
      {payState === "paid" && (
        <div className="callout success" style={{ marginBottom: 16 }}>
          <CheckCircle2 size={17} /> Оплата прошла, баланс пополнен. Спасибо!
        </div>
      )}
      {payState === "timeout" && (
        <div className="callout" style={{ marginBottom: 16 }}>
          Платёж ещё обрабатывается. Баланс обновится автоматически, как только Робокасса подтвердит оплату.
        </div>
      )}
      {failed && (
        <div className="callout danger" style={{ marginBottom: 16 }}>
          <XCircle size={17} /> Оплата не завершена. Деньги не списаны — можно попробовать ещё раз.
        </div>
      )}

      <div className="stack" style={{ gap: 20 }}>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1.25fr)", gap: 20 }} className="billing-grid">
          <div className="balance-hero">
            <div className="label">Текущий баланс</div>
            <div className="value">{info ? rub(info.balance_rub) : "…"}</div>
            {info && info.prices[0] && (
              <div className="hint">
                Хватит примерно на {Math.max(0, Math.floor(info.balance_rub / info.prices[0].typical_task_rub))} типичных задач с{" "}
                {modelLabel(info.prices[0].model)}
              </div>
            )}
          </div>
          <div className="card card-pad">
            <div style={{ fontWeight: 600, marginBottom: 12 }}>Пополнить</div>
            {info ? <TopUpForm info={info} compact /> : <div className="skeleton" style={{ height: 200 }} />}
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <div>
              <h3>Цена</h3>
              <p className="muted small">За 1 млн токенов. «Типичная задача» — небольшая доработка: ~250 тыс. токенов на входе (большая часть из кэша) и 10 тыс. на выходе.</p>
            </div>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Модель</th>
                  <th className="num">Вход</th>
                  <th className="num">Выход</th>
                  <th className="num">Типичная задача</th>
                </tr>
              </thead>
              <tbody>
                {info?.prices.map((p) => (
                  <tr key={p.model}>
                    <td style={{ fontWeight: 500 }}>{modelLabel(p.model)}</td>
                    <td className="num">{rub(p.input_per_m_rub, 0)}</td>
                    <td className="num">{rub(p.output_per_m_rub, 0)}</td>
                    <td className="num">
                      <span className="badge">≈ {rub(p.typical_task_rub, 0)}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>История операций</h3>
          </div>
          {info && ledger.length === 0 && (
            <div className="card-pad muted" style={{ textAlign: "center" }}>
              Операций пока нет
            </div>
          )}
          <table className="table">
            <tbody>
              {ledger.map((l) => {
                const k = KIND[l.kind] || { label: l.kind, icon: Sparkles };
                return (
                  <tr key={l.key}>
                    <td>
                      <div className="row" style={{ gap: 12 }}>
                        <span className={"tx-icon " + (k.cls || "")}>
                          <k.icon size={15} />
                        </span>
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontWeight: 500 }}>
                            {k.label}
                            {l.kind === "usage" && l.session && (
                              <>
                                {" · "}
                                <a href={`#/s/${l.session}`}>сессия</a>
                              </>
                            )}
                          </div>
                          <div className="faint small">
                            {new Date(l.date).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
                            {l.kind === "usage" && ` · ${l.count} ${l.count === 1 ? "запрос" : "запросов"}`}
                            {l.meta?.comment ? ` · ${l.meta.comment}` : ""}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className={"num " + (l.amount > 0 ? "pos" : "neg")} style={{ fontWeight: 550 }}>
                      {l.amount > 0 ? "+" : ""}
                      {rub(l.amount)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
