type Line = { kind: "add" | "del" | "ctx" | "hunk"; text: string; oldNo?: number; newNo?: number };

/** Построчный diff (LCS) двух фрагментов текста. */
export function lineDiff(a: string, b: string): Line[] {
  const A = a.split("\n");
  const B = b.split("\n");
  if (A.length * B.length > 250_000) {
    return [...A.map((t) => ({ kind: "del" as const, text: t })), ...B.map((t) => ({ kind: "add" as const, text: t }))];
  }
  const n = A.length;
  const m = B.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: Line[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) {
      out.push({ kind: "ctx", text: A[i], oldNo: i + 1, newNo: j + 1 });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ kind: "del", text: A[i], oldNo: i + 1 });
      i++;
    } else {
      out.push({ kind: "add", text: B[j], newNo: j + 1 });
      j++;
    }
  }
  while (i < n) out.push({ kind: "del", text: A[i], oldNo: ++i });
  while (j < m) out.push({ kind: "add", text: B[j], newNo: ++j });
  return out;
}

export function diffStat(a: string, b: string) {
  const d = lineDiff(a, b);
  return { add: d.filter((l) => l.kind === "add").length, del: d.filter((l) => l.kind === "del").length };
}

/** Разбор unified diff из git в строки для отображения. */
export function parsePatch(patch: string): Line[] {
  const out: Line[] = [];
  let o = 0;
  let n = 0;
  let inHunk = false;
  for (const raw of patch.split("\n")) {
    if (raw.startsWith("@@")) {
      const m = /@@ -(\d+)(?:,\d+)? \+(\d+)/.exec(raw);
      o = m ? parseInt(m[1], 10) : 0;
      n = m ? parseInt(m[2], 10) : 0;
      inHunk = true;
      out.push({ kind: "hunk", text: raw });
      continue;
    }
    if (!inHunk) continue;
    if (raw.startsWith("+")) out.push({ kind: "add", text: raw.slice(1), newNo: n++ });
    else if (raw.startsWith("-")) out.push({ kind: "del", text: raw.slice(1), oldNo: o++ });
    else if (raw.startsWith("\\")) continue;
    else if (raw.startsWith("diff --git")) inHunk = false;
    else out.push({ kind: "ctx", text: raw.slice(1), oldNo: o++, newNo: n++ });
  }
  return out;
}

/** Сжимает длинные неизменённые участки, оставляя 3 строки контекста. */
function collapse(lines: Line[], context = 3): (Line | { kind: "gap"; count: number })[] {
  const keep = new Array(lines.length).fill(false);
  lines.forEach((l, i) => {
    if (l.kind !== "ctx") for (let k = Math.max(0, i - context); k <= Math.min(lines.length - 1, i + context); k++) keep[k] = true;
  });
  const out: (Line | { kind: "gap"; count: number })[] = [];
  let gap = 0;
  lines.forEach((l, i) => {
    if (keep[i]) {
      if (gap) out.push({ kind: "gap", count: gap });
      gap = 0;
      out.push(l);
    } else gap++;
  });
  if (gap) out.push({ kind: "gap", count: gap });
  return out;
}

export function DiffView({ lines, compact = true }: { lines: Line[]; compact?: boolean }) {
  const view = compact ? collapse(lines) : lines;
  return (
    <div className="diff">
      {view.map((l, i) =>
        l.kind === "gap" ? (
          <div key={i} className="diff-line hunk">
            <span className="ln" />
            <span className="sign" />⋯ {l.count} без изменений
          </div>
        ) : (
          <div key={i} className={"diff-line " + l.kind}>
            <span className="ln">{l.kind === "hunk" ? "" : (l.newNo ?? l.oldNo ?? "")}</span>
            <span className="sign">{l.kind === "add" ? "+" : l.kind === "del" ? "−" : ""}</span>
            <span>{l.text || " "}</span>
          </div>
        ),
      )}
    </div>
  );
}
