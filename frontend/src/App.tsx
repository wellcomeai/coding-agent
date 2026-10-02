import { useCallback, useEffect, useState } from "react";
import { api, ApiError, Me, rub } from "./api";
import Home from "./pages/Home";
import Login from "./pages/Login";
import SessionView from "./pages/Session";
import Settings from "./pages/Settings";

function useHashRoute(): string {
  const [hash, setHash] = useState(window.location.hash.slice(1) || "/");
  useEffect(() => {
    const on = () => setHash(window.location.hash.slice(1) || "/");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

export default function App() {
  const route = useHashRoute();
  const [me, setMe] = useState<Me | null>(null);
  const [state, setState] = useState<"loading" | "anon" | "ok">("loading");

  const refreshMe = useCallback(async () => {
    try {
      setMe(await api<Me>("/api/auth/me"));
      setState("ok");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setState("anon");
      else setState("anon");
    }
  }, []);

  useEffect(() => {
    refreshMe();
  }, [refreshMe]);

  if (state === "loading") return <div className="center muted">Загрузка…</div>;
  if (state === "anon" || !me) return <Login />;

  let page;
  if (route.startsWith("/s/")) page = <SessionView id={route.slice(3)} me={me} onBalance={refreshMe} />;
  else if (route === "/settings") page = <Settings me={me} onChange={refreshMe} />;
  else page = <Home me={me} />;

  return (
    <div className="app">
      <header className="topbar">
        <a href="#/" className="brand">
          ⌘ Coding Agent
        </a>
        <nav>
          <a href="#/">Сессии</a>
          <a href="#/settings">Настройки</a>
        </nav>
        <div className="spacer" />
        <a href="#/settings" className={"balance" + (me.balance_rub <= 0 ? " low" : "")} title="Баланс">
          {rub(me.balance_rub)}
        </a>
        {me.avatar_url && <img className="avatar" src={me.avatar_url} alt="" />}
        <span className="muted">{me.login}</span>
        <form method="post" action="/api/auth/logout">
          <button className="link">Выйти</button>
        </form>
      </header>
      <main>{page}</main>
    </div>
  );
}
