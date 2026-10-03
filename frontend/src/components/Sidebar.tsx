import {
  ChevronsUpDown,
  CreditCard,
  GitCommitHorizontal,
  LogOut,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Plus,
  Search,
  Settings as SettingsIcon,
  Shield,
  Sun,
  Trash2,
  Wallet,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, deleteSession, renameSession, SessionInfo, SessionPage } from "../api";
import { useApp } from "../App";
import { dateGroup, relTime, repoName, rub } from "../lib/format";
import { Logo, useMedia, useOutside, useTheme, useToast } from "./ui";

export default function Sidebar({ path, onClose }: { path: string; onClose: () => void }) {
  const { me, sessions, openTopUp, sidebarCollapsed, toggleSidebar, refreshSessions, hasMoreSessions, loadMoreSessions } =
    useApp();
  const toast = useToast();
  const [q, setQ] = useState("");
  // Поиск идёт на сервере: так находятся и старые чаты, которых нет среди загруженных
  const [found, setFound] = useState<SessionPage | null>(null);
  const [searchTick, setSearchTick] = useState(0);
  const [editing, setEditing] = useState<string | null>(null);

  useEffect(() => {
    const query = q.trim();
    if (!query) {
      setFound(null);
      return;
    }
    let alive = true;
    const t = setTimeout(() => {
      api<SessionPage>(`/api/sessions?limit=100&q=${encodeURIComponent(query)}`)
        .then((r) => alive && setFound(r))
        .catch(() => alive && setFound({ sessions: [], has_more: false }));
    }, 250);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [q, searchTick]);

  async function changed() {
    await refreshSessions();
    if (q.trim()) setSearchTick((n) => n + 1);
  }

  async function rename(s: SessionInfo, title: string) {
    setEditing(null);
    const t = title.trim();
    if (!t || t === s.title) return;
    try {
      await renameSession(s.id, t);
      await changed();
    } catch (err: any) {
      toast("error", err.message);
    }
  }
  const [menu, setMenu] = useState(false);
  const [theme, setTheme] = useTheme();
  const menuRef = useRef<HTMLDivElement>(null);
  useOutside(menuRef, () => setMenu(false), menu);

  const groups = useMemo(() => {
    const list = (q.trim() ? found?.sessions : sessions) || [];
    const out: [string, typeof list][] = [];
    for (const s of list) {
      const g = dateGroup(s.last_activity_at);
      const last = out[out.length - 1];
      if (last && last[0] === g) last[1].push(s);
      else out.push([g, [s]]);
    }
    return out;
  }, [sessions, found, q]);

  async function remove(e: React.MouseEvent, s: SessionInfo) {
    e.preventDefault();
    e.stopPropagation();
    try {
      if (!(await deleteSession(s))) return;
    } catch (err: any) {
      return toast("error", err.message);
    }
    if (path === `/s/${s.id}`) window.location.hash = "#/";
    changed();
  }

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
        {((sessions && sessions.length > 5) || hasMoreSessions || q) && (
          <div className="input-wrap">
            <Search size={15} />
            <input className="input" placeholder="Поиск по сессиям" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        )}
      </div>

      <nav className="sidebar-list">
        {sessions === null &&
          [0, 1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 40, margin: "8px 6px" }} />)}
        {q.trim() && found && found.sessions.length === 0 && (
          <div className="faint small" style={{ padding: "16px 10px" }}>
            Ничего не найдено
          </div>
        )}
        {!q.trim() && sessions && sessions.length === 0 && (
          <div className="faint small" style={{ padding: "16px 10px" }}>
            Здесь появятся ваши задачи. Начните с первой — опишите, что нужно сделать в репозитории.
          </div>
        )}
        {groups.map(([label, items]) => (
          <div key={label}>
            <div className="group-label">{label}</div>
            {items.map((s) =>
              editing === s.id ? (
                <div key={s.id} className="side-item active">
                  <input
                    className="input side-rename"
                    defaultValue={s.title}
                    maxLength={255}
                    autoFocus
                    onFocus={(e) => e.currentTarget.select()}
                    onBlur={(e) => rename(s, e.currentTarget.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") e.currentTarget.blur();
                      if (e.key === "Escape") {
                  // Вернуть прежнее название: при потере фокуса сохранять нечего
                  e.currentTarget.value = s.title;
                  e.currentTarget.blur();
                }
                    }}
                    aria-label="Название чата"
                  />
                </div>
              ) : (
              <a key={s.id} href={`#/s/${s.id}`} className={"side-item" + (path === `/s/${s.id}` ? " active" : "")}>
                <span
                  className={"status-dot " + (s.status === "running" ? "running" : s.status === "error" ? "error" : s.pr_url ? "pr" : "")}
                  title={s.status === "running" ? "Работает" : s.status === "error" ? "Ошибка" : s.pr_url ? "PR создан" : "Ожидает"}
                />
                <span className="body">
                  <span className="t ellipsis">{s.title}</span>
                  <span className="s ellipsis">{repoName(s.repo_full_name)}</span>
                </span>
                <button
                  className="side-del"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setEditing(s.id);
                  }}
                  title="Переименовать"
                  aria-label={`Переименовать чат ${s.title}`}
                >
                  <Pencil size={13} />
                </button>
                <button
                  className="side-del danger"
                  onClick={(e) => remove(e, s)}
                  title="Удалить чат"
                  aria-label={`Удалить чат ${s.title}`}
                >
                  <Trash2 size={14} />
                </button>
              </a>
              ),
            )}
          </div>
        ))}
        {!q.trim() && hasMoreSessions && (
          <button className="btn ghost sm block" style={{ margin: "6px 0 10px" }} onClick={loadMoreSessions}>
            Показать ещё
          </button>
        )}
        {q.trim() && found?.has_more && (
          <div className="faint small" style={{ padding: "8px 10px" }}>
            Показаны первые 100 — уточните запрос
          </div>
        )}
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
