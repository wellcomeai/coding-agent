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
  build: { version: string; branch: string | null; repo: string; deployed_at: string } | null;
};

export type Repo = {
  full_name: string;
  private: boolean;
  default_branch: string;
  installation_id: number;
  pushed_at?: string;
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

export type Price = { model: string; input_per_m_rub: number; output_per_m_rub: number; typical_task_rub: number };

export type BillingInfo = {
  balance_rub: number;
  prices: Price[];
  payments_enabled: boolean;
  packages: number[];
  topup_min_rub: number;
  topup_max_rub: number;
  ledger: { id: number; amount_rub: number; kind: string; session_id: string | null; meta: any; created_at: string }[];
};

export type FileChange = { path: string; additions: number; deletions: number; patch: string };

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
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
    let msg = res.statusText || "Ошибка запроса";
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
