export const rub = (v: number, digits = 2) =>
  v.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits }) + " ₽";

export const rubShort = (v: number) =>
  v.toLocaleString("ru-RU", { maximumFractionDigits: v < 10 ? 2 : 0 }) + " ₽";

export function plural(n: number, one: string, few: string, many: string) {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

export function relTime(iso: string): string {
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "только что";
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
  if (diff < 86400 && new Date().getDate() === d.getDate()) return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

export function dateGroup(iso: string): string {
  const d = new Date(iso);
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const day = 86400000;
  if (d >= start) return "Сегодня";
  if (d.getTime() >= start.getTime() - day) return "Вчера";
  if (d.getTime() >= start.getTime() - 7 * day) return "Последние 7 дней";
  return "Ранее";
}

export function modelLabel(id: string): string {
  const name = id.split("/").pop() || id;
  const map: Record<string, string> = {
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-opus-5": "Claude Opus 5",
    "gpt-5.3-codex": "GPT-5.3 Codex",
    "kimi-k2.7-code": "Kimi K2.7 Code",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
  };
  return map[name] || name;
}

export function modelHint(id: string): string {
  if (id.includes("opus")) return "Самая умная, для сложных задач";
  if (id.includes("sonnet")) return "Лучший баланс качества и цены";
  if (id.includes("codex")) return "Сильна в коде и рефакторинге";
  if (id.includes("kimi")) return "Быстрая и недорогая";
  if (id.includes("deepseek")) return "Самая экономичная";
  return "";
}

export function repoName(full: string) {
  return full.split("/")[1] || full;
}
