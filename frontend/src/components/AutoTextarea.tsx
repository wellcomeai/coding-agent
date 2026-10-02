import { forwardRef, TextareaHTMLAttributes, useEffect, useImperativeHandle, useRef } from "react";

/** Textarea, растущая по содержимому. Enter — отправить, Shift+Enter — перенос строки. */
const AutoTextarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement> & { onSubmit?: () => void }
>(function AutoTextarea({ onSubmit, value, ...props }, outer) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(outer, () => ref.current!);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 320) + "px";
  }, [value]);
  return (
    <textarea
      ref={ref}
      rows={1}
      value={value}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
          e.preventDefault();
          onSubmit?.();
        }
      }}
      {...props}
    />
  );
});

export default AutoTextarea;
