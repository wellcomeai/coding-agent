import { Check, ChevronDown, Search } from "lucide-react";
import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useOutside } from "./ui";

export type PickerItem = { value: string; label: string; hint?: string; icon?: ReactNode };

export default function Picker({
  icon,
  value,
  items,
  onChange,
  placeholder,
  searchable,
  footer,
  title,
  direction = "up",
  loading,
}: {
  icon?: ReactNode;
  value: string;
  items: PickerItem[];
  onChange: (v: string) => void;
  placeholder?: string;
  searchable?: boolean;
  footer?: ReactNode;
  title?: string;
  direction?: "up" | "down";
  loading?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hl, setHl] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  useOutside(ref, () => setOpen(false), open);

  const filtered = useMemo(
    () => items.filter((i) => !q || i.label.toLowerCase().includes(q.toLowerCase())),
    [items, q],
  );
  const current = items.find((i) => i.value === value);

  useEffect(() => {
    if (open) {
      setQ("");
      setHl(Math.max(0, items.findIndex((i) => i.value === value)));
      setTimeout(() => (searchable ? inputRef.current : listRef.current)?.focus(), 0);
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  function choose(v: string) {
    onChange(v);
    setOpen(false);
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHl((h) => Math.min(filtered.length - 1, h + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHl((h) => Math.max(0, h - 1));
    } else if (e.key === "Enter" && filtered[hl]) {
      e.preventDefault();
      choose(filtered[hl].value);
    }
  }

  return (
    <div className="picker" ref={ref}>
      <button type="button" className="picker-btn" onClick={() => setOpen((o) => !o)} title={title}>
        {current?.icon ?? icon}
        <span className="lbl">{loading ? "Загрузка…" : current?.label || placeholder}</span>
        <ChevronDown size={14} className="faint" />
      </button>
      {open && (
        <div className={"popover" + (direction === "down" ? " down" : "")} onKeyDown={onKey}>
          {title && <div className="menu-label">{title}</div>}
          {searchable && (
            <div className="input-wrap" style={{ margin: "2px 2px 0" }}>
              <Search size={14} />
              <input
                ref={inputRef}
                className="input"
                style={{ height: 32 }}
                placeholder="Поиск…"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setHl(0);
                }}
              />
            </div>
          )}
          <div className="list" tabIndex={-1} ref={listRef}>
            {filtered.length === 0 && <div className="faint small" style={{ padding: "10px" }}>Ничего не найдено</div>}
            {filtered.map((i, idx) => (
              <button
                type="button"
                key={i.value}
                className={"menu-item" + (idx === hl ? " hl" : "") + (i.value === value ? " selected" : "")}
                onMouseEnter={() => setHl(idx)}
                onClick={() => choose(i.value)}
              >
                {i.icon}
                <span style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1 }}>
                  <span className="ellipsis">{i.label}</span>
                  {i.hint && <span className="faint small ellipsis">{i.hint}</span>}
                </span>
                {i.value === value && <Check size={15} />}
              </button>
            ))}
          </div>
          {footer && (
            <>
              <div className="divider" />
              {footer}
            </>
          )}
        </div>
      )}
    </div>
  );
}
