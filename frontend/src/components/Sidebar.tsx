import {
  ChevronsUpDown,
  CreditCard,
  GitCommitHorizontal,
  LogOut,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings as SettingsIcon,
  Shield,
  Sun,
  Wallet,
  X,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useApp } from "../App";
import { dateGroup, relTime, repoName, rub } from "../lib/format";
import { Logo, useMedia, useOutside, useTheme } from "./ui";

export default function Sidebar({ path, onClose }: { path: string; onClose: () => void }) {
  const { me, sessions, openTopUp, sidebarCollapsed, toggleSidebar } = useApp();
  const [q, setQ] = useState("");
  const [menu, setMenu] = useState(false);
  const [theme, setTheme] = useTheme();
  const menuRef = useRef<HTMLDivElement>(null);
  useOutside(menuRef, () => setMenu(false), menu);

  const groups = useMemo(() => {
    const list = (sessions || []).filter(
      (s) => !q || (s.title + " " + s.repo_full_name).toLowerCase().includes(q.toLowerCase()),
    );
    const out: [string, typeof list][] = [];
    for (const s of list) {
      const g = dateGroup(s.last_activity_at);
      const last = out[out.length - 1];
      if (last && last[0] === g) last[1].push(s);
      else out.push([g, [s]]);
    }
    return out;
  }, [sessions, q]);

  const mobile = useMedia("(max-width: 860px)");
  if (sidebarCollapsed && !mobile) return <Rail path={path} />;

  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <div className="row">
          <a href="#/" className="brand">
            <Logo /> Coding Agent
          </a>
          <div className="spacer" />
          <button className="btn ghost icon sm desktop-only" onClick={toggleSidebar} title="Свернуть панель (Ctrl+B)" aria-label="Свернуть панель">
            <PanelLeftClose size={17} />
          </button>
          <button className="btn ghost icon sm mobile-only" onClick={onClose} aria-label="Закрыть" style={{ display: "none" }}>
            <X size={16} />
          </button>
        </div>
        <a href="#/" className="btn primary block">
          <Plus size={16} /> Новая задача
        </a>
        {sessions && sessions.length > 5 && (
          <div className="input-wrap">
            <Search size={15} />
            <input className="input" placeholder="Поиск по сессиям" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        )}
      </div>

      <nav className="sidebar-list">
        {sessions === null &&
          [0, 1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 40, margin: "8px 6px" }} />)}
        {sessions && sessions.length === 0 && (
          <div className="faint small" style={{ padding: "16px 10px" }}>
            Здесь появятся ваши задачи. Начните с первой — опишите, что нужно сделать в репозитории.
          </div>
        )}
        {groups.map(([label, items]) => (
          <div key={label}>
            <div className="group-label">{label}</div>
            {items.map((s) => (
              <a key={s.id} href={`#/s/${s.id}`} className={"side-item" + (path === `/s/${s.id}` ? " active" : "")}>
                <span
                  className={"status-dot " + (s.status === "running" ? "running" : s.status === "error" ? "error" : s.pr_url ? "pr" : "")}
                  title={s.status === "running" ? "Работает" : s.status === "error" ? "Ошибка" : s.pr_url ? "PR создан" : "Ожидает"}
                />
                <span className="body">
                  <span className="t ellipsis">{s.title}</span>
                  <span className="s ellipsis">{repoName(s.repo_full_name)}</span>
                </span>
              </a>
            ))}
          </div>
        ))}
      </nav>

      <div className="sidebar-bottom">
        <div className="balance-card">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="faint small">Баланс</div>
            <div className={"amount" + (me.balance_rub <= 0 ? " low" : "")}>{rub(me.balance_rub)}</div>
          </div>
          <button className="btn sm" onClick={openTopUp}>
            <Plus size={14} /> Пополнить
          </button>
        </div>

        <div style={{ position: "relative" }} ref={menuRef}>
          <button className="user-row" onClick={() => setMenu((m) => !m)}>
            {me.avatar_url ? <img className="avatar" src={me.avatar_url} alt="" /> : <span className="avatar" />}
            <span className="body" style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
              <span className="ellipsis" style={{ fontWeight: 550, fontSize: 13.5 }}>
                {me.name || me.login}
              </span>
              <span className="faint small ellipsis">@{me.login}</span>
            </span>
            <ChevronsUpDown size={15} className="faint" />
          </button>
          {menu && (
            <div className="popover" style={{ bottom: "calc(100% + 6px)", left: 0, right: 0 }}>
              <a className="menu-item" href="#/billing" onClick={() => setMenu(false)}>
                <CreditCard size={16} /> Баланс и оплата
              </a>
              <a className="menu-item" href="#/settings" onClick={() => setMenu(false)}>
                <SettingsIcon size={16} /> Настройки
              </a>
              {me.is_admin && (
                <a className="menu-item" href="#/admin" onClick={() => setMenu(false)}>
                  <Shield size={16} /> Администрирование
                </a>
              )}
              <div className="divider" />
              <div className="menu-label">Тема</div>
              <div className="row" style={{ padding: "2px 6px 6px", gap: 4 }}>
                {(
                  [
                    ["system", Monitor, "Авто"],
                    ["light", Sun, "Светлая"],
                    ["dark", Moon, "Тёмная"],
                  ] as const
                ).map(([key, Icon, label]) => (
                  <button
                    key={key}
                    className={"btn sm" + (theme === key ? "" : " ghost")}
                    style={{ flex: 1 }}
                    onClick={() => setTheme(key)}
                    title={label}
                  >
                    <Icon size={14} />
                  </button>
                ))}
              </div>
              {me.build && (
                <>
                  <div className="divider" />
                  <a
                    className="menu-item"
                    href={`https://github.com/${me.build.repo}/commit/${me.build.version}`}
                    target="_blank"
                    rel="noreferrer"
                    title="Версия сервиса (видно только администраторам)"
                  >
                    <GitCommitHorizontal size={16} />
                    <span style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1 }}>
                      <span className="small">
                        Версия <span className="mono">{me.build.version}</span> · {relTime(me.build.deployed_at)}
                      </span>
                      {me.build.branch && (
                        <span className="mono small ellipsis" style={{ marginLeft: 0, color: "var(--text-3)" }}>
                          {me.build.branch}
                        </span>
                      )}
                    </span>
                  </a>
                </>
              )}
              <div className="divider" />
              <form method="post" action="/api/auth/logout" style={{ margin: 0 }}>
                <button className="menu-item">
                  <LogOut size={16} /> Выйти
                </button>
              </form>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}

/** Свёрнутая левая панель: узкая полоса с иконками. */
function Rail({ path }: { path: string }) {
  const { me, sessions, openTopUp, toggleSidebar } = useApp();
  const recent = (sessions || []).slice(0, 8);
  return (
    <aside className="sidebar rail">
      <button className="btn ghost icon" onClick={toggleSidebar} title="Развернуть панель (Ctrl+B)" aria-label="Развернуть панель">
        <PanelLeftOpen size={18} />
      </button>
      <a href="#/" className="btn primary icon" title="Новая задача" aria-label="Новая задача">
        <Plus size={18} />
      </a>
      <div className="rail-list">
        {recent.map((s) => (
          <a
            key={s.id}
            href={`#/s/${s.id}`}
            className={"rail-item" + (path === `/s/${s.id}` ? " active" : "")}
            title={`${s.title} — ${repoName(s.repo_full_name)}`}
          >
            {s.title.trim().charAt(0).toUpperCase() || "•"}
            <span className={"status-dot " + (s.status === "running" ? "running" : s.status === "error" ? "error" : s.pr_url ? "pr" : "")} />
          </a>
        ))}
      </div>
      <div className="spacer" />
      <button
        className={"btn ghost icon" + (me.balance_rub <= 0 ? " danger" : "")}
        onClick={openTopUp}
        title={`Баланс ${rub(me.balance_rub)} — пополнить`}
        aria-label="Пополнить баланс"
      >
        <Wallet size={18} />
      </button>
      <a href="#/settings" title={`@${me.login} — настройки`} className="rail-avatar">
        {me.avatar_url ? <img className="avatar" src={me.avatar_url} alt="" /> : <span className="avatar" />}
      </a>
    </aside>
  );
}
