import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError, Me, SessionInfo, SessionPage } from "./api";
import Sidebar from "./components/Sidebar";
import TopUpModal from "./components/TopUpModal";
import { Logo, ToastProvider, useStoredFlag } from "./components/ui";
import { Menu } from "lucide-react";
import Admin from "./pages/Admin";
import Billing from "./pages/Billing";
import Login from "./pages/Login";
import NewTask from "./pages/NewTask";
import SessionView from "./pages/Session";
import Settings from "./pages/Settings";

type Ctx = {
  me: Me;
  refreshMe: () => Promise<void>;
  sessions: SessionInfo[] | null;
  refreshSessions: () => Promise<void>;
  /** есть ли чаты старше загруженных */
  hasMoreSessions: boolean;
  loadMoreSessions: () => void;
  openTopUp: () => void;
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
};
const AppCtx = createContext<Ctx>(null as any);
export const useApp = () => useContext(AppCtx);

const PAGE = 50;

function useHashRoute(): [string, URLSearchParams] {
  const read = () => {
    const raw = window.location.hash.slice(1) || "/";
    const [path, query] = raw.split("?");
    return [path, new URLSearchParams(query || "")] as [string, URLSearchParams];
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [state, setState] = useState<"loading" | "anon" | "ok">("loading");

  const refreshMe = useCallback(async () => {
    try {
      setMe(await api<Me>("/api/auth/me"));
      setState("ok");
    } catch (e) {
      setState(e instanceof ApiError && e.status === 401 ? "anon" : "anon");
    }
  }, []);

  useEffect(() => {
    refreshMe();
  }, [refreshMe]);

  if (state === "loading")
    return (
      <div style={{ height: "100%", display: "grid", placeItems: "center" }}>
        <Logo size={36} />
      </div>
    );
  return <ToastProvider>{state === "anon" || !me ? <Login /> : <Shell me={me} refreshMe={refreshMe} />}</ToastProvider>;
}

function Shell({ me, refreshMe }: { me: Me; refreshMe: () => Promise<void> }) {
  const [path, query] = useHashRoute();
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [sessionLimit, setSessionLimit] = useState(PAGE);
  const [hasMoreSessions, setHasMoreSessions] = useState(false);
  const [topUp, setTopUp] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [collapsed, setCollapsed] = useStoredFlag("sidebarCollapsed", false);
  const toggleSidebar = useCallback(() => setCollapsed((c) => !c), [setCollapsed]);

  // Ctrl/⌘ + B — свернуть/развернуть левую панель
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "b") {
        e.preventDefault();
        toggleSidebar();
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [toggleSidebar]);

  const refreshSessions = useCallback(async () => {
    try {
      const page = await api<SessionPage>(`/api/sessions?limit=${sessionLimit}`);
      setSessions(page.sessions);
      setHasMoreSessions(page.has_more);
    } catch {
      /* ignore */
    }
  }, [sessionLimit]);
  const loadMoreSessions = useCallback(() => setSessionLimit((n) => n + PAGE), []);

  useEffect(() => {
    refreshSessions();
    const t = setInterval(refreshSessions, 15000);
    return () => clearInterval(t);
  }, [refreshSessions]);

  useEffect(() => setDrawer(false), [path]);

  let page;
  if (path.startsWith("/s/")) page = <SessionView key={path} id={path.slice(3)} />;
  else if (path === "/billing") page = <Billing query={query} />;
  else if (path === "/settings") page = <Settings />;
  else if (path === "/admin" && me.is_admin) page = <Admin />;
  else page = <NewTask />;

  return (
    <AppCtx.Provider value={{
        me,
        refreshMe,
        sessions,
        refreshSessions,
        hasMoreSessions,
        loadMoreSessions,
        openTopUp: () => setTopUp(true),
        sidebarCollapsed: collapsed,
        toggleSidebar,
      }}>
      <div className={"shell" + (drawer ? " drawer-open" : "") + (collapsed ? " collapsed" : "")} onClick={(e) => drawer && e.target === e.currentTarget && setDrawer(false)}>
        <Sidebar path={path} onClose={() => setDrawer(false)} />
        <main className="main">
          <div className="mobile-bar">
            <button className="btn ghost icon" onClick={() => setDrawer(true)} aria-label="Меню">
              <Menu size={18} />
            </button>
            <a href="#/" className="brand">
              <Logo size={22} /> Coding Agent
            </a>
          </div>
          {page}
        </main>
      </div>
      {topUp && <TopUpModal onClose={() => setTopUp(false)} />}
    </AppCtx.Provider>
  );
}
