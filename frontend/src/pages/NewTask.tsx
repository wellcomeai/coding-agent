import {
  ArrowUp,
  Bug,
  Check,
  FlaskConical,
  GitBranch,
  Globe,
  Loader2,
  Lock,
  Plus,
  Rocket,
  Sparkles,
  Wand2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, Repo, SessionInfo } from "../api";
import { useApp } from "../App";
import AutoTextarea from "../components/AutoTextarea";
import Picker from "../components/Picker";
import { GitHubMark, useToast } from "../components/ui";
import { modelHint, modelLabel } from "../lib/format";

const SUGGESTIONS = [
  { icon: Bug, text: "Найди и исправь баги", prompt: "Изучи проект, найди потенциальные баги и исправь самые важные из них. Покажи, что изменил." },
  { icon: FlaskConical, text: "Напиши тесты", prompt: "Добавь автотесты для основной логики проекта и убедись, что они проходят." },
  { icon: Wand2, text: "Улучши README", prompt: "Изучи проект и перепиши README: описание, установка, запуск, структура." },
  { icon: Rocket, text: "Задеплой в Timeweb", prompt: "Подготовь проект к деплою и разверни его в Timeweb Cloud Apps. Подбери недорогой тариф и спроси меня перед созданием." },
];

const LAST_REPO = "lastRepo";

export default function NewTask() {
  const { me, refreshSessions, openTopUp } = useApp();
  const toast = useToast();
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [installUrl, setInstallUrl] = useState(me.install_url);
  const [repo, setRepo] = useState("");
  const [branches, setBranches] = useState<string[] | null>(null);
  const [branch, setBranch] = useState("");
  const [model, setModel] = useState(me.default_model);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    api<{ repos: Repo[]; install_url: string }>("/api/repos")
      .then((r) => {
        setRepos(r.repos);
        setInstallUrl(r.install_url);
        let last = "";
        try {
          last = localStorage.getItem(LAST_REPO) || "";
        } catch {
          /* ignore */
        }
        const pick = r.repos.find((x) => x.full_name === last) || r.repos[0];
        if (pick) setRepo(pick.full_name);
      })
      .catch((e) => {
        setRepos([]);
        toast("error", e.message);
      });
    ta.current?.focus();
  }, [toast]);

  useEffect(() => {
    if (!repo) return;
    const r = repos?.find((x) => x.full_name === repo);
    setBranch(r?.default_branch || "");
    setBranches(null);
    try {
      localStorage.setItem(LAST_REPO, repo);
    } catch {
      /* ignore */
    }
    api<{ branches: string[] }>(`/api/repos/${repo}/branches`)
      .then((b) => setBranches(b.branches))
      .catch(() => setBranches([]));
  }, [repo, repos]);

  const repoItems = useMemo(
    () =>
      (repos || []).map((r) => ({
        value: r.full_name,
        label: r.full_name,
        icon: r.private ? <Lock size={14} className="faint" /> : <Globe size={14} className="faint" />,
      })),
    [repos],
  );
  const branchItems = useMemo(() => {
    const list = branches && branches.length ? branches : branch ? [branch] : [];
    const def = repos?.find((x) => x.full_name === repo)?.default_branch;
    return list.map((b) => ({ value: b, label: b, hint: b === def ? "основная ветка" : undefined }));
  }, [branches, branch, repo, repos]);
  const modelItems = me.models.map((m) => ({ value: m, label: modelLabel(m), hint: modelHint(m) }));

  const noRepos = repos !== null && repos.length === 0;
  const lowBalance = me.balance_rub <= 0;
  const canSend = !!repo && !!text.trim() && !busy && !lowBalance;

  async function start() {
    if (lowBalance) return openTopUp();
    if (!canSend) return;
    setBusy(true);
    try {
      const s = await api<SessionInfo>("/api/sessions", {
        method: "POST",
        json: { repo_full_name: repo, base_branch: branch || undefined, model, message: text.trim() },
      });
      refreshSessions();
      window.location.hash = `#/s/${s.id}`;
    } catch (e: any) {
      toast("error", e.message);
      setBusy(false);
    }
  }

  const hour = new Date().getHours();
  const greet = hour < 6 ? "Доброй ночи" : hour < 12 ? "Доброе утро" : hour < 18 ? "Добрый день" : "Добрый вечер";
  const firstName = (me.name || me.login).split(" ")[0];

  return (
    <div className="hero">
      <h1>
        {greet}, {firstName}
      </h1>
      <p className="lead">Опишите задачу — агент напишет код, проверит его, откроет pull request и задеплоит.</p>

      {noRepos ? (
        <div className="card card-pad" style={{ textAlign: "center" }}>
          <div style={{ display: "grid", placeItems: "center", marginBottom: 12 }}>
            <GitHubMark size={32} />
          </div>
          <h3 style={{ margin: "0 0 6px" }}>Подключите репозитории</h3>
          <p className="muted" style={{ margin: "0 0 16px" }}>
            Установите GitHub App и выберите репозитории, с которыми будет работать агент.
          </p>
          <a className="btn primary" href={installUrl} target="_blank" rel="noreferrer">
            <GitHubMark size={16} /> Выбрать репозитории
          </a>
          <div className="faint small" style={{ marginTop: 10 }}>
            После установки обновите эту страницу
          </div>
        </div>
      ) : (
        <div className="composer">
          <AutoTextarea
            ref={ta}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onSubmit={start}
            placeholder="Например: добавь страницу регистрации с проверкой email и напиши тесты"
          />
          <div className="composer-bar">
            <Picker
              icon={<GitHubMark size={14} />}
              value={repo}
              items={repoItems}
              onChange={setRepo}
              placeholder="Репозиторий"
              title="Репозиторий"
              searchable
              loading={repos === null}
              direction="down"
              footer={
                <a className="menu-item" href={installUrl} target="_blank" rel="noreferrer">
                  <Plus size={15} /> Добавить репозитории
                </a>
              }
            />
            <Picker
              icon={<GitBranch size={14} />}
              value={branch}
              items={branchItems}
              onChange={setBranch}
              placeholder="Ветка"
              title="Базовая ветка"
              searchable={branchItems.length > 8}
              direction="down"
            />
            <Picker
              icon={<Sparkles size={14} />}
              value={model}
              items={modelItems}
              onChange={setModel}
              title="Модель"
              direction="down"
            />
            <div className="spacer" />
            <span className="faint small nowrap hide-sm" style={{ marginRight: 4 }}>
              <kbd>Enter</kbd> — запустить
            </span>
            <button className="send-btn" disabled={!canSend && !lowBalance} onClick={start} aria-label="Запустить агента">
              {busy ? <Loader2 size={17} className="spin" /> : <ArrowUp size={18} />}
            </button>
          </div>
        </div>
      )}

      {!noRepos && (
        <div className="suggestions">
          {SUGGESTIONS.map((s) => (
            <button
              key={s.text}
              className="suggestion"
              onClick={() => {
                setText(s.prompt);
                ta.current?.focus();
              }}
            >
              <s.icon size={14} /> {s.text}
            </button>
          ))}
        </div>
      )}

      {(lowBalance || !me.has_timeweb) && (
        <div className="onboarding">
          <div className="group-label" style={{ paddingLeft: 2 }}>
            Начало работы
          </div>
          <div className="steps">
            <div className="step done">
              <span className="num">
                <Check size={13} />
              </span>
              <div>
                <div className="t">GitHub подключён</div>
                <div className="d">Агент видит выбранные репозитории</div>
              </div>
            </div>
            <a
              className={"step" + (lowBalance ? "" : " done")}
              href="#/billing"
              onClick={(e) => {
                e.preventDefault();
                openTopUp();
              }}
            >
              <span className="num">{lowBalance ? 2 : <Check size={13} />}</span>
              <div>
                <div className="t">Пополните баланс</div>
                <div className="d">Оплата только за использованные токены</div>
              </div>
            </a>
            <a className={"step" + (me.has_timeweb ? " done" : "")} href="#/settings">
              <span className="num">{me.has_timeweb ? <Check size={13} /> : 3}</span>
              <div>
                <div className="t">Подключите Timeweb</div>
                <div className="d">Чтобы агент деплоил ваши проекты</div>
              </div>
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
