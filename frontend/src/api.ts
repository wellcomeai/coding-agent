export type Me = {
  id: number;
  login: string;
  name: string | null;
  avatar_url: string | null;
  balance_rub: number;
  has_timeweb: boolean;
  is_admin: boolean;
  install_url: string;
  models: string[];
  default_model: string;
};

export type Repo = {
  full_name: string;
  private: boolean;
  default_branch: string;
  installation_id: number;
};

export type SessionInfo = {
  id: string;
  title: string;
  repo_full_name: string;
  base_branch: string;
  work_branch: string;
  model: string;
  status: "idle" | "running" | "error";
  pr_url: string | null;
  cost_rub: number;
  created_at: string;
  last_activity_at: string;
  branch_url: string;
};

export type AgentEvent = { type: string; data: any; seq: number | null; ts?: string };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = any>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init;
  const res = await fetch(path, {
    credentials: "same-origin",
    ...rest,
    headers: { ...(json !== undefined ? { "Content-Type": "application/json" } : {}), ...(rest.headers || {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json();
}

export const rub = (v: number) =>
  v.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ₽";
