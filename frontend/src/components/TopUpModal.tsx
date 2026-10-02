import { ArrowRight, Lock, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, BillingInfo } from "../api";
import { plural, rub } from "../lib/format";
import { Modal, useToast } from "./ui";

export function useBilling() {
  const [info, setInfo] = useState<BillingInfo | null>(null);
  const load = () => api<BillingInfo>("/api/billing").then(setInfo);
  useEffect(() => {
    load().catch(() => {});
  }, []);
  return { info, reload: load };
}

/** Выбор суммы и переход на оплату в Робокассу. */
export function TopUpForm({ info, compact }: { info: BillingInfo; compact?: boolean }) {
  const toast = useToast();
  const pkgs = info.packages;
  const [amount, setAmount] = useState<number>(pkgs[1] ?? pkgs[0] ?? 1000);
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(false);
  const value = custom ? parseInt(custom, 10) || 0 : amount;
  const task = info.prices[0]?.typical_task_rub || 0;
  const valid = value >= info.topup_min_rub && value <= info.topup_max_rub;

  async function pay() {
    setBusy(true);
    try {
      const r = await api<{ url: string }>("/api/billing/topup", { method: "POST", json: { amount_rub: value } });
      window.location.href = r.url;
    } catch (e: any) {
      toast("error", e.message);
      setBusy(false);
    }
  }

  if (!info.payments_enabled)
    return <div className="callout">Онлайн-оплата скоро появится. Пока баланс пополняет администратор.</div>;

  return (
    <div className="stack" style={{ gap: 14 }}>
      <div className="packages" style={compact ? { gridTemplateColumns: "repeat(2, 1fr)" } : undefined}>
        {pkgs.map((p, i) => (
          <button
            key={p}
            className={"package" + (!custom && amount === p ? " selected" : "")}
            onClick={() => {
              setAmount(p);
              setCustom("");
            }}
          >
            {i === 1 && <span className="tag">Популярно</span>}
            <div className="sum">{p.toLocaleString("ru-RU")} ₽</div>
            {task > 0 && (
              <div className="faint small">
                ≈ {Math.max(1, Math.floor(p / task))} {plural(Math.max(1, Math.floor(p / task)), "задача", "задачи", "задач")}
              </div>
            )}
          </button>
        ))}
      </div>
      <div className="field">
        <label>Или своя сумма</label>
        <input
          className="input"
          inputMode="numeric"
          placeholder={`от ${info.topup_min_rub} до ${info.topup_max_rub.toLocaleString("ru-RU")} ₽`}
          value={custom}
          onChange={(e) => setCustom(e.target.value.replace(/\D/g, ""))}
        />
      </div>
      <button className="btn primary lg block" disabled={!valid || busy} onClick={pay}>
        {busy ? <Loader2 size={18} className="spin" /> : null}
        Оплатить {valid ? rub(value, 0) : ""} <ArrowRight size={17} />
      </button>
      <div className="row faint small" style={{ justifyContent: "center" }}>
        <Lock size={13} /> Безопасная оплата через Робокассу: карты, СБП, SberPay
      </div>
    </div>
  );
}

export default function TopUpModal({ onClose }: { onClose: () => void }) {
  const { info } = useBilling();
  return (
    <Modal title="Пополнение баланса" subtitle="Средства списываются только за фактически использованные токены" onClose={onClose}>
      {info ? <TopUpForm info={info} compact /> : <div className="skeleton" style={{ height: 220 }} />}
    </Modal>
  );
}
