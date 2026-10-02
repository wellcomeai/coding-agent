import { FormEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AgentEvent, api, Me, rub, SessionInfo } from "../api";

type Item =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool"; id: string; name: string; args: string; output?: string; isError?: boolean }
  | { kind: "error"; text: string }
  | { kind: "info"; text: string };

function summarizeArgs(name: string, raw: string): string {
  try {
    const a = JSON.parse(raw || "{}");
    if (name === "bash") return a.command;
    if (a.path) return a.path;
    if (a.pattern) return a.pattern;
    if (a.message) return a.message;
    if (a.title) return a.title;
    if (a.app_id) return `app ${a.app_id}`;
    return Object.keys(a).length ? JSON.stringify(a) : "";
  } catch {
    return raw;
  }
}

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
      return [...items, { kind: "info", text: "Остановлено пользователем" }];
    case "pr":
      return [...items, { kind: "info", text: `Pull request: ${d.url}` }];
    default:
      return items;
  }
}

export default function SessionView({ id, me, onBalance }: { id: string; me: Me; onBalance: () => void }) {
  const [info, setInfo] = useState<SessionInfo | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [draft, setDraft] = useState("");
  const [running, setRunning] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);
  const [cost, setCost] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    api<SessionInfo>(`/api/sessions/${id}`)
      .then(setInfo)
      .catch((e) => setError(e.message));
    setItems([]);
    setDraft("");
    setCost(0); // стоимость суммируется из событий usage (они воспроизводятся при подключении)
    const es = new EventSource(`/api/sessions/${id}/events`);
    es.onmessage = (m) => {
      const ev: AgentEvent = JSON.parse(m.data);
      if (ev.type === "assistant_delta") {
        setDraft((d) => d + ev.data.text);
        return;
      }
      if (ev.type === "assistant_message") setDraft("");
      if (ev.type === "tool_call") setDraft("");
      if (ev.type === "status") {
        setRunning(ev.data.status === "running");
        setDetail(ev.data.detail || null);
        if (ev.data.status !== "running") {
          setDraft("");
          onBalance();
          api<SessionInfo>(`/api/sessions/${id}`).then(setInfo).catch(() => {});
        }
      }
      if (ev.type === "ready") setRunning(ev.data.running);
      if (ev.type === "usage") setCost((c) => (c ?? 0) + ev.data.cost_rub);
      if (ev.type === "pr") setInfo((i) => (i ? { ...i, pr_url: ev.data.url } : i));
      setItems((prev) => reduce(prev, ev));
    };
    return () => es.close();
  }, [id, onBalance]);

  useEffect(() => {
    if (stick.current) bottom.current?.scrollIntoView({ block: "end" });
  }, [items, draft]);

  useEffect(() => {
    const on = () => {
      stick.current = window.innerHeight + window.scrollY >= document.body.scrollHeight - 120;
    };
    window.addEventListener("scroll", on);
    return () => window.removeEventListener("scroll", on);
  }, []);

  async function send(e?: FormEvent) {
    e?.preventDefault();
    if (!text.trim()) return;
    setError(null);
    try {
      await api(`/api/sessions/${id}/messages`, { method: "POST", json: { text } });
      setText("");
      setRunning(true);
      stick.current = true;
    } catch (err: any) {
      setError(err.message);
    }
  }

  async function stop() {
    await api(`/api/sessions/${id}/stop`, { method: "POST" }).catch((e) => setError(e.message));
  }

  async function remove() {
    if (!confirm("Удалить сессию? Незакоммиченные изменения будут сохранены WIP-коммитом в рабочую ветку.")) return;
    await api(`/api/sessions/${id}`, { method: "DELETE" });
    window.location.hash = "#/";
  }

  return (
    <div className="session">
      <div className="session-head card">
        <div className="grow">
          <h2>{info?.title ?? "…"}</h2>
          {info && (
            <div className="muted small">
              <a href={`https://github.com/${info.repo_full_name}`} target="_blank" rel="noreferrer">
                {info.repo_full_name}
              </a>{" "}
              · {info.base_branch} →{" "}
              <a href={info.branch_url} target="_blank" rel="noreferrer">
                {info.work_branch}
              </a>{" "}
              · {info.model} · потрачено {rub(cost ?? 0)}
            </div>
          )}
        </div>
        {info?.pr_url && (
          <a className="btn" href={info.pr_url} target="_blank" rel="noreferrer">
            Открыть PR
          </a>
        )}
        <button className="btn danger-outline" onClick={remove}>
          Удалить
        </button>
      </div>

      <div className="timeline">
        {items.map((it, i) => (
          <ItemView key={i} item={it} />
        ))}
        {draft && (
          <div className="msg assistant">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{draft}</ReactMarkdown>
          </div>
        )}
        {running && (
          <div className="working">
            <span className="spinner" /> {detail || "Агент работает…"}
          </div>
        )}
        <div ref={bottom} />
      </div>

      <form className="composer card" onSubmit={send}>
        {error && <div className="error">{error}</div>}
        {!me.has_timeweb && (
          <div className="muted small">
            Чтобы агент мог деплоить в Timeweb Cloud, добавьте API-токен в <a href="#/settings">Настройках</a>.
          </div>
        )}
        <textarea
          rows={3}
          value={text}
          placeholder={running ? "Агент работает… можно остановить" : "Сообщение агенту (Ctrl+Enter — отправить)"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
          }}
          disabled={running}
        />
        <div className="row end">
          {running ? (
            <button type="button" className="btn danger" onClick={stop}>
              ■ Остановить
            </button>
          ) : (
            <button className="btn primary" disabled={!text.trim()}>
              Отправить
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

function ItemView({ item }: { item: Item }) {
  if (item.kind === "user") return <div className="msg user">{item.text}</div>;
  if (item.kind === "assistant")
    return (
      <div className="msg assistant">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
      </div>
    );
  if (item.kind === "error") return <div className="msg error">{item.text}</div>;
  if (item.kind === "info") return <div className="msg info">{item.text}</div>;
  const pending = item.output === undefined;
  return (
    <details className={"tool" + (item.isError ? " failed" : "")}>
      <summary>
        <span className="tool-name">{item.name}</span>
        <code className="tool-args">{summarizeArgs(item.name, item.args)}</code>
        {pending ? <span className="spinner small" /> : item.isError ? <span className="tag err">ошибка</span> : null}
      </summary>
      <pre className="args">{item.args}</pre>
      {!pending && <pre className="output">{item.output}</pre>}
    </details>
  );
}
