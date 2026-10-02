import {
  Check,
  ChevronRight,
  Cloud,
  FilePen,
  FilePlus2,
  FileText,
  FolderTree,
  GitBranch,
  GitCommitHorizontal,
  GitPullRequest,
  Loader2,
  Rocket,
  Search,
  SquareTerminal,
  X,
} from "lucide-react";
import { ReactNode, useMemo, useState } from "react";
import { plural } from "../lib/format";
import { diffStat, DiffView, lineDiff } from "./Diff";

export type ToolItem = { id: string; name: string; args: string; output?: string; isError?: boolean };

type Meta = { icon: ReactNode; verb: string; detail: string; cls?: string; stat?: ReactNode };

function parse(raw: string): any {
  try {
    return JSON.parse(raw || "{}");
  } catch {
    return {};
  }
}

const TW: Record<string, string> = {
  timeweb_list_apps: "Список приложений Timeweb",
  timeweb_find_repository: "Проверка репозитория в Timeweb",
  timeweb_deploy_options: "Тарифы Timeweb",
  timeweb_create_app: "Создание приложения",
  timeweb_deploy: "Деплой",
  timeweb_app_status: "Статус приложения",
  timeweb_deploy_logs: "Логи сборки",
  timeweb_app_logs: "Логи приложения",
  timeweb_wait_deploy: "Ожидание деплоя",
  timeweb_db_options: "Тарифы баз данных",
  timeweb_list_databases: "Список баз данных",
  timeweb_create_database: "Создание базы данных",
  timeweb_database_status: "Статус базы данных",
  timeweb_wait_database: "Ожидание базы данных",
  timeweb_connect_database: "Подключение базы",
  timeweb_fix_database_access: "Доступ к базе",
  timeweb_set_app_env: "Переменные приложения",
};

export function describe(t: ToolItem): Meta {
  const a = parse(t.args);
  switch (t.name) {
    case "bash":
      return { icon: <SquareTerminal size={14} />, verb: "Команда", detail: a.command || "" };
    case "read_file":
      return { icon: <FileText size={14} />, verb: "Чтение", detail: a.path || "" };
    case "write_file": {
      const lines = (a.content || "").split("\n").length;
      return {
        icon: <FilePlus2 size={14} />,
        verb: "Запись",
        detail: a.path || "",
        cls: "edit",
        stat: <span className="add">+{lines}</span>,
      };
    }
    case "edit_file": {
      const s = diffStat(a.old_string || "", a.new_string || "");
      return {
        icon: <FilePen size={14} />,
        verb: "Правка",
        detail: a.path || "",
        cls: "edit",
        stat: (
          <>
            <span className="add">+{s.add}</span> <span className="del">−{s.del}</span>
          </>
        ),
      };
    }
    case "list_files":
      return { icon: <FolderTree size={14} />, verb: "Обзор файлов", detail: a.path && a.path !== "." ? a.path : "весь проект" };
    case "grep":
      return { icon: <Search size={14} />, verb: "Поиск", detail: a.pattern + (a.glob ? `  ${a.glob}` : "") };
    case "git_commit_and_push":
      return { icon: <GitCommitHorizontal size={14} />, verb: "Коммит и push", detail: a.message || "", cls: "git" };
    case "create_pull_request":
      return { icon: <GitPullRequest size={14} />, verb: "Pull request", detail: a.title || "", cls: "git" };
    case "pull_request_status":
      return { icon: <GitPullRequest size={14} />, verb: "Статус PR и CI", detail: "", cls: "git" };
    case "list_branches":
      return { icon: <GitBranch size={14} />, verb: "Ветки", detail: "", cls: "git" };
    default:
      if (t.name.startsWith("timeweb_"))
        return {
          icon: t.name === "timeweb_deploy" || t.name === "timeweb_create_app" ? <Rocket size={14} /> : <Cloud size={14} />,
          verb: TW[t.name] || t.name,
          detail: a.name || (a.app_id ? `app ${a.app_id}` : a.app_type || ""),
          cls: "deploy",
        };
      return { icon: <SquareTerminal size={14} />, verb: t.name, detail: "" };
  }
}

function Body({ t }: { t: ToolItem }) {
  const a = parse(t.args);
  if (t.name === "bash")
    return (
      <pre className="terminal">
        <span className="prompt">$ </span>
        {a.command}
        {"\n"}
        {t.output === undefined ? "…" : t.output}
      </pre>
    );
  if (t.name === "edit_file" && !t.isError) return <DiffView lines={lineDiff(a.old_string || "", a.new_string || "")} />;
  if (t.name === "write_file" && !t.isError)
    return (
      <DiffView
        compact={false}
        lines={(a.content || "")
          .split("\n")
          .slice(0, 300)
          .map((l: string, i: number) => ({ kind: "add" as const, text: l, newNo: i + 1 }))}
      />
    );
  if (t.output === undefined) return <div className="faint small">Выполняется…</div>;
  return <pre className={"codebox" + (t.isError ? " error" : "")}>{t.output}</pre>;
}

function Step({ t, last }: { t: ToolItem; last: boolean }) {
  const [open, setOpen] = useState(false);
  const m = describe(t);
  const pending = t.output === undefined;
  return (
    <div className="step-row">
      <button className="step-line" onClick={() => setOpen((o) => !o)}>
        <span className={"step-icon " + (m.cls || "")}>{m.icon}</span>
        <span className="step-verb">{m.verb}</span>
        <span className="step-detail ellipsis" style={{ flex: 1 }}>
          {m.detail}
        </span>
        {m.stat && <span className="step-stat">{m.stat}</span>}
        {pending ? (
          last ? (
            <Loader2 size={14} className="spin faint" />
          ) : null
        ) : t.isError ? (
          <X size={14} style={{ color: "var(--danger)" }} />
        ) : (
          <Check size={14} className="faint" />
        )}
      </button>
      {open && (
        <div className="step-body">
          <Body t={t} />
        </div>
      )}
    </div>
  );
}

export default function StepsGroup({ tools, active, defaultOpen }: { tools: ToolItem[]; active: boolean; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const files = useMemo(() => {
    const set = new Set<string>();
    tools.forEach((t) => {
      if ((t.name === "edit_file" || t.name === "write_file") && !t.isError) set.add(parse(t.args).path);
    });
    return set.size;
  }, [tools]);
  const errors = tools.filter((t) => t.isError).length;
  const running = active && tools.some((t) => t.output === undefined);
  const lastMeta = describe(tools[tools.length - 1]);

  return (
    <div className="steps-card">
      <button className="steps-head" onClick={() => setOpen((o) => !o)}>
        <ChevronRight size={15} className={"chev" + (open ? " open" : "")} />
        {running ? (
          <span className="shimmer" style={{ fontWeight: 500 }}>
            {lastMeta.verb}
            {lastMeta.detail ? `: ${lastMeta.detail.slice(0, 60)}` : ""}…
          </span>
        ) : (
          <span>
            {tools.length} {plural(tools.length, "действие", "действия", "действий")}
          </span>
        )}
        <span className="spacer" />
        {files > 0 && (
          <span className="badge accent">
            <FilePen size={12} /> {files} {plural(files, "файл", "файла", "файлов")}
          </span>
        )}
        {errors > 0 && <span className="badge danger">{errors} с ошибкой</span>}
      </button>
      {open && tools.map((t, i) => <Step key={t.id + i} t={t} last={i === tools.length - 1} />)}
    </div>
  );
}
