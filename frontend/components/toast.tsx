"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";

interface Toast {
  id: number;
  kind: "success" | "error" | "info";
  message: string;
}

const ToastContext = createContext<(kind: Toast["kind"], message: string) => void>(() => {});

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counter = useRef(0);

  const push = useCallback((kind: Toast["kind"], message: string) => {
    const id = ++counter.current;
    setToasts((current) => [...current, { id, kind, message }]);
    setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), 4200);
  }, []);

  const glyph: Record<Toast["kind"], string> = { success: "✓", error: "✕", info: "•" };
  const glyphColor: Record<Toast["kind"], string> = {
    success: "text-legal",
    error: "text-illegal",
    info: "text-signal",
  };

  const styles: Record<Toast["kind"], string> = {
    success: "border-l-legal",
    error: "border-l-illegal",
    info: "border-l-signal",
  };

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div
        aria-live="polite"
        className="scope-terminal pointer-events-none fixed bottom-24 right-4 z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2 lg:bottom-4"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`pointer-events-auto flex items-start gap-2 rounded-lg border border-l-[3px] border-line bg-panel2 px-3 py-2.5 text-sm text-foreground shadow-[var(--shadow-pop)] ${styles[toast.kind]}`}
          >
            <span aria-hidden className={`text-[12px] leading-5 ${glyphColor[toast.kind]}`}>
              {glyph[toast.kind]}
            </span>
            <span>{toast.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
