import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ChevronDown,
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  FileDiff,
  FileText,
  GitBranch,
  GitPullRequest,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  Sparkles,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AgentEvent, api, FileChange, SessionInfo, deleteSession } from "../api";
import { useApp } from "../App";
import AutoTextarea from "../components/AutoTextarea";
import { DiffView, parsePatch } from "../components/Diff";
import StepsGroup, { ToolItem } from "../components/Steps";
import { GitHubMark, Logo, useMedia, useOutside, useStoredFlag, useToast } from "../components/ui";
import { modelLabel, rub } from "../lib/format";

type Item =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | ({ kind: "tool" } & ToolItem)
  | { kind: "error"; text: string }
  | { kind: "pr"; url: string }
  | { kind: "progress"; text: string }
  | { kind: "info"; text: string };

function reduce(items: Item[], ev: AgentEvent): Item[] {
  const d = ev.data || {};
  switch (ev.type) {
    case "user_message":
      return [...items, { kind: "user", text: d.text }];
    case "assistant_message":
      return [...items, { kind: "assistant", text: d.text }];
    case "tool_call":
      return [...items, { kind: "tool", id: d.id, name: d.name, args: d.arguments }];
    case "tool_result":
      return items.map((it) =>
        it.kind === "tool" && it.id === d.id && it.output === undefined ? { ...it, output: d.output, isError: d.is_error } : it,
      );
    case "error":
      return [...items, { kind: "error", text: d.message }];
    case "stopped":
      return [...items, { kind: "info", text: "Выполнение остановлено" }];
    case "pr":
      return [...items, { kind: "pr", url: d.url }];
    case "progress":
      return [...items, { kind: "progress", text: d.text }];
    default:
      return items;
  }
}

type Block = Exclude<Item, { kind: "tool" }> | { kind: "steps"; tools: ToolItem[] };

function toBlocks(items: Item[]): Block[] {
  const out: Block[] = [];
  for (const it of items) {
    if (it.kind === "tool") {
      const last = out[out.length - 1];
      if (last && last.kind === "steps") last.tools.push(it);
      else out.push({ kind: "steps", tools: [it] });
    } else out.push(it);
  }
  return out;
}

function Md({ text, caret }: { text: string; caret?: boolean }) {
  return (
    <div className={"md" + (caret ? " caret" : "")}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ a: ({ node: _n, ...p }) => <a {...p} target="_blank" rel="noreferrer" /> }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export default function SessionView({ id }: { id: string }) {
  const { me, refreshMe, refreshSessions, openTopUp } = useApp();
  const wide = useMedia("(min-width: 1180px)");
  const [detailsPref, setDetailsPref] = useStoredFlag("sessionDetails", true);
  const [detailsMobile, setDetailsMobile] = useState(false);
  const details = wide ? detailsPref : detailsMobile;
  const setDetails = wide ? setDetailsPref : setDetailsMobile;
  const toast = useToast();
  const [info, setInfo] = useState<SessionInfo | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [draft, setDraft] = useState("");
  // Живой статус от лёгкой модели: не сохраняется, только строка состояния под лентой
  const [narration, setNarration] = useState<{ id: number; text: string } | null>(null);
  const [running, setRunning] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);
  const [cost, setCost] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [text, setText] = useState("");
  const [tab, setTab] = useState<"chat" | "changes">("chat");
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useOutside(menuRef, () => setMenu(false), menu);
  const stick = useRef(true);

  const loadInfo = useCallback(() => api<SessionInfo>(`/api/sessions/${id}`).then(setInfo), [id]);

  useEffect(() => {
    loadInfo().catch((e) => toast("error", e.message));
    const es = new EventSource(`/api/sessions/${id}/events`);
    es.onmessage = (m) => {
      const ev: AgentEvent = JSON.parse(m.data);
      if (ev.type === "assistant_delta") {
        setDraft((d) => d + ev.data.text);
        return;
      }
      if (ev.type === "narration_delta") {
        const { id: nid, text: chunk } = ev.data;
        setNarration((n) => (n && n.id === nid ? { id: nid, text: n.text + chunk } : { id: nid, text: chunk }));
        return;
      }
      if (ev.type === "narration") {
        setNarration({ id: ev.data.id, text: ev.data.text });
        return;
      }
      if (ev.type === "assistant_message" || ev.type === "tool_call") setDraft("");
      if (ev.type === "ready") {
        setRunning(ev.data.running);
        setLoaded(true);
      }
      if (ev.type === "status") {
        setRunning(ev.data.status === "running");
        setDetail(ev.data.detail || null);
        if (ev.data.status !== "running") {
          setDraft("");
          setNarration(null);
          refreshMe();
          refreshSessions();
          loadInfo().catch(() => {});
        }
      }
      if (ev.type === "usage") setCost((c) => c + ev.data.cost_rub);
      if (ev.type === "pr") setInfo((i) => (i ? { ...i, pr_url: ev.data.url } : i));
      if (ev.type === "branch") loadInfo().catch(() => {});
      setItems((prev) => reduce(prev, ev));
    };
    return () => es.close();
  }, [id, loadInfo, refreshMe, refreshSessions, toast]);

  const blocks = useMemo(() => toBlocks(items), [items]);

  // автопрокрутка, пока пользователь у нижнего края
  useEffect(() => {
    const main = document.querySelector(".main");
    if (!main) return;
    const on = () => {
      stick.current = main.scrollHeight - main.scrollTop - main.clientHeight < 140;
    };
    main.addEventListener("scroll", on);
    return () => main.removeEventListener("scroll", on);
  }, []);
  useEffect(() => {
    const main = document.querySelector(".main");
    if (main && stick.current && tab === "chat") main.scrollTop = main.scrollHeight;
  }, [blocks, draft, running, tab]);

  async function send(message?: string) {
    const body = (message ?? text).trim();
    if (!body || running) return;
    if (me.balance_rub <= 0) return openTopUp();
    try {
      await api(`/api/sessions/${id}/messages`, { method: "POST", json: { text: body } });
      if (!message) setText("");
      setRunning(true);
      setTab("chat");
      stick.current = true;
    } catch (e: any) {
      toast("error", e.message);
    }
  }

  async function stop() {
    await api(`/api/sessions/${id}/stop`, { method: "POST" }).catch((e) => toast("error", e.message));
  }

  async function remove() {
    if (!info) return;
    try {
      if (!(await deleteSession(info))) return;
    } catch (e: any) {
      return toast("error", e.message);
    }
    refreshSessions();
    window.location.hash = "#/";
  }

  const lastStepsIndex = blocks.map((b) => b.kind).lastIndexOf("steps");
  const changedFiles = useMemo(() => {
    const set = new Set<string>();
    items.forEach((i) => {
      if (i.kind === "tool" && (i.name === "edit_file" || i.name === "write_file") && !i.isError) {
        try {
          set.add(JSON.parse(i.args).path);
        } catch {
          /* ignore */
        }
      }
    });
    return [...set];
  }, [items]);

  return (
    <div className={"session-layout" + (details ? " with-details" : "")}>
      <div className="session-wrap">
      <header className="session-header">
        <div className="session-header-inner">
          <h1 className="session-title ellipsis" title={info?.title}>
            {info?.title ?? " "}
          </h1>
          {running && (
            <span className="badge accent">
              <Loader2 size={12} className="spin" /> Работает
            </span>
          )}
          {!running && info?.status === "error" && <span className="badge danger">Ошибка</span>}
          <div className="tabs">
            <button className={"tab" + (tab === "chat" ? " active" : "")} onClick={() => setTab("chat")}>
              <MessageSquare size={15} /> Чат
            </button>
            <button className={"tab" + (tab === "changes" ? " active" : "")} onClick={() => setTab("changes")}>
              <FileDiff size={15} /> Изменения {changedFiles.length > 0 && <span className="count">{changedFiles.length}</span>}
            </button>
          </div>
          <div className="spacer" />
          {info?.pr_url ? (
            <a className="btn sm" href={info.pr_url} target="_blank" rel="noreferrer">
              <GitPullRequest size={14} /> <span className="hide-sm">Открыть PR</span>
            </a>
          ) : (
            info &&
            items.length > 0 && (
              <button className="btn sm" disabled={running} onClick={() => send("Закоммить изменения и открой pull request.")}>
                <GitPullRequest size={14} /> <span className="hide-sm">Создать PR</span>
              </button>
            )
          )}
          <button
            className={"btn ghost icon sm" + (details ? " pressed" : "")}
            onClick={() => setDetails((d) => !d)}
            title={details ? "Скрыть детали" : "Показать детали"}
            aria-label="Детали сессии"
          >
            {details ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
          </button>
          <div style={{ position: "relative" }} ref={menuRef}>
            <button className="btn ghost icon sm" onClick={() => setMenu((m) => !m)} aria-label="Ещё">
              <MoreHorizontal size={16} />
            </button>
            {menu && info && (
              <div className="popover" style={{ right: 0, top: "calc(100% + 6px)" }}>
                <a className="menu-item" href={info.branch_url} target="_blank" rel="noreferrer">
                  <GitBranch size={15} /> Ветка на GitHub
                </a>
                {!info.same_branch && (
                  <a
                    className="menu-item"
                    href={`https://github.com/${info.repo_full_name}/compare/${info.base_branch}...${info.work_branch}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <ExternalLink size={15} /> Сравнение веток
                  </a>
                )}
                <div className="divider" />
                <button className="menu-item" style={{ color: "var(--danger)" }} onClick={remove}>
                  <Trash2 size={15} /> Удалить сессию
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {tab === "changes" ? (
        info && <Changes info={info} />
      ) : (
        <div className="timeline">
          {!loaded && [0, 1, 2].map((i) => <div key={i} className="skeleton" style={{ height: i === 1 ? 90 : 44 }} />)}
          {blocks.map((b, i) => {
            switch (b.kind) {
              case "user":
                return (
                  <div key={i} className="msg-user">
                    {b.text}
                  </div>
                );
              case "assistant":
                return (
                  <div key={i} className="msg-agent">
                    <Logo size={24} />
                    <div className="content">
                      <Md text={b.text} />
                    </div>
                  </div>
                );
              case "steps":
                return (
                  <StepsGroup
                    key={i}
                    tools={b.tools}
                    active={running && i === lastStepsIndex}
                    defaultOpen={i === lastStepsIndex || b.tools.length <= 3}
                  />
                );
              case "pr":
                return (
                  <div key={i} className="event-card pr">
                    <span className="ic">
                      <GitPullRequest size={17} />
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600 }}>Pull request открыт</div>
                      <div className="faint small ellipsis">{b.url}</div>
                    </div>
                    <a className="btn sm" href={b.url} target="_blank" rel="noreferrer">
                      Открыть <ExternalLink size={13} />
                    </a>
                  </div>
                );
              case "error":
                return (
                  <div key={i} className="event-card err">
                    <span className="ic">
                      <AlertTriangle size={17} />
                    </span>
                    <div style={{ flex: 1, minWidth: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{b.text}</div>
                    {/баланс/i.test(b.text) && (
                      <button className="btn sm primary" onClick={openTopUp}>
                        Пополнить
                      </button>
                    )}
                  </div>
                );
              case "progress":
                return (
                  <div key={i} className="progress-note">
                    <CheckCircle2 size={15} />
                    <span>{b.text}</span>
                  </div>
                );
              case "info":
                return (
                  <div key={i} className="event-card info">
                    {b.text}
                  </div>
                );
            }
          })}
          {draft && (
            <div className="msg-agent">
              <Logo size={24} />
              <div className="content">
                <Md text={draft} caret />
              </div>
            </div>
          )}
          {running && !draft && (
            <div className="thinking">
              <Loader2 size={15} className="spin faint" />
              <span className="shimmer">{narration?.text.trim() || detail || "Агент думает…"}</span>
            </div>
          )}
        </div>
      )}

      <div className="composer-dock">
        <div className="inner">
          <div className="composer">
            <AutoTextarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onSubmit={() => send()}
              placeholder={running ? "Агент работает — дождитесь ответа или остановите его" : "Ответьте агенту или поставьте следующую задачу"}
            />
            <div className="composer-bar">
              <span className="faint small hide-sm">
                <kbd>Enter</kbd> отправить · <kbd>Shift+Enter</kbd> новая строка
              </span>
              <div className="spacer" />
              {running ? (
                <button className="send-btn stop" onClick={stop} aria-label="Остановить" title="Остановить">
                  <Square size={13} fill="currentColor" />
                </button>
              ) : (
                <button className="send-btn" disabled={!text.trim()} onClick={() => send()} aria-label="Отправить">
                  <ArrowUp size={18} />
                </button>
              )}
            </div>
          </div>
          <div className="composer-meta">
            <span className="hide-sm">Агент работает в изолированной песочнице и пушит только в ветку {info?.work_branch}</span>
            <span className="nowrap">Потрачено {rub(cost)}</span>
          </div>
        </div>
      </div>
      </div>
      {details && info && (
        <DetailsPanel
          info={info}
          running={running}
          cost={cost}
          files={changedFiles}
          onClose={() => setDetails(false)}
          onOpenChanges={() => setTab("changes")}
        />
      )}
    </div>
  );
}

function DetailsPanel({
  info,
  running,
  cost,
  files,
  onClose,
  onOpenChanges,
}: {
  info: SessionInfo;
  running: boolean;
  cost: number;
  files: string[];
  onClose: () => void;
  onOpenChanges: () => void;
}) {
  const created = new Date(info.created_at).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
  return (
    <>
    <div className="details-backdrop" onClick={onClose} />
    <aside className="details">
      <div className="details-head">
        <span style={{ fontWeight: 600 }}>Детали</span>
        <div className="spacer" />
        <button className="btn ghost icon sm" onClick={onClose} aria-label="Скрыть детали">
          <X size={16} />
        </button>
      </div>
      <div className="details-body">
        <div className="detail">
          <div className="detail-label">Статус</div>
          {running ? (
            <span className="badge accent">
              <Loader2 size={12} className="spin" /> Работает
            </span>
          ) : info.status === "error" ? (
            <span className="badge danger">Ошибка</span>
          ) : info.pr_url ? (
            <span className="badge success">PR открыт</span>
          ) : (
            <span className="badge">Ожидает задачу</span>
          )}
        </div>
        <div className="detail">
          <div className="detail-label">Репозиторий</div>
          <a className="detail-link" href={`https://github.com/${info.repo_full_name}`} target="_blank" rel="noreferrer">
            <GitHubMark size={14} /> <span className="ellipsis">{info.repo_full_name}</span>
          </a>
        </div>
        <div className="detail">
          <div className="detail-label">{info.same_branch ? "Ветка" : "Ветки"}</div>
          <div className="branch-flow">
            {!info.same_branch && (
              <>
                <span className="mono ellipsis" title={info.base_branch}>
                  {info.base_branch}
                </span>
                <ArrowDown size={13} className="faint" />
              </>
            )}
            <a className="mono ellipsis" href={info.branch_url} target="_blank" rel="noreferrer" title={info.work_branch}>
              {info.work_branch}
            </a>
          </div>
        </div>
        {info.pr_url && (
          <div className="detail">
            <div className="detail-label">Pull request</div>
            <a className="detail-link" href={info.pr_url} target="_blank" rel="noreferrer">
              <GitPullRequest size={14} /> <span className="ellipsis">#{info.pr_url.split("/").pop()}</span>
              <ExternalLink size={12} className="faint" />
            </a>
          </div>
        )}
        <div className="detail">
          <div className="detail-label">Модель</div>
          <div className="row">
            <Sparkles size={14} className="faint" /> {modelLabel(info.model)}
          </div>
        </div>
        <div className="detail">
          <div className="detail-label">Потрачено</div>
          <div style={{ fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{rub(cost)}</div>
        </div>
        <div className="detail">
          <div className="detail-label">Создана</div>
          <div className="muted">{created}</div>
        </div>
        <div className="detail">
          <div className="detail-label">
            Изменённые файлы {files.length > 0 && <span className="faint">· {files.length}</span>}
          </div>
          {files.length === 0 ? (
            <div className="faint small">Пока нет изменений</div>
          ) : (
            <div className="file-list">
              {files.map((f) => (
                <button key={f} className="file-item" onClick={onOpenChanges} title={f}>
                  <FileText size={13} className="faint" />
                  <span className="mono ellipsis">{f}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </aside>
    </>
  );
}

function Changes({ info }: { info: SessionInfo }) {
  const [data, setData] = useState<{ available: boolean; files: FileChange[] } | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api(`/api/sessions/${info.id}/changes`));
    } catch {
      setData({ available: false, files: [] });
    } finally {
      setLoading(false);
    }
  }, [info.id]);

  useEffect(() => {
    load();
  }, [load]);

  const total = (data?.files || []).reduce((a, f) => [a[0] + f.additions, a[1] + f.deletions], [0, 0]);

  return (
    <div className="changes">
      <div className="row">
        <div style={{ fontWeight: 600 }}>
          {data?.files.length ?? "…"} изменённых файлов{" "}
          {data && data.files.length > 0 && (
            <span className="step-stat" style={{ marginLeft: 6 }}>
              <span className="add">+{total[0]}</span> <span className="del">−{total[1]}</span>
            </span>
          )}
        </div>
        <span className="faint small">
          {info.same_branch ? "с начала работы агента" : `относительно ${info.base_branch}`}
        </span>
        <div className="spacer" />
        <button className="btn sm ghost" onClick={load} disabled={loading}>
          <RefreshCw size={14} className={loading ? "spin" : ""} /> Обновить
        </button>
      </div>
      {data && !data.available && (
        <div className="callout">
          Песочница сейчас остановлена — изменения сохранены в ветке.{" "}
          <a
            href={
              info.same_branch
                ? `https://github.com/${info.repo_full_name}/commits/${info.work_branch}`
                : `https://github.com/${info.repo_full_name}/compare/${info.base_branch}...${info.work_branch}`
            }
            target="_blank"
            rel="noreferrer"
          >
            Посмотреть на GitHub
          </a>
        </div>
      )}
      {data?.available && data.files.length === 0 && <div className="callout">Пока изменений нет.</div>}
      {data?.files.map((f) => (
        <div key={f.path} className="file-card">
          <button className="file-head" onClick={() => setOpen((o) => ({ ...o, [f.path]: !(o[f.path] ?? true) }))}>
            {open[f.path] ?? true ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <span className="ellipsis" style={{ flex: 1 }}>
              {f.path}
            </span>
            <span className="step-stat">
              <span className="add">+{f.additions}</span> <span className="del">−{f.deletions}</span>
            </span>
          </button>
          {(open[f.path] ?? true) && <DiffView lines={parsePatch(f.patch)} compact={false} />}
        </div>
      ))}
    </div>
  );
}
