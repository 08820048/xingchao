import { cn } from "@/lib/utils";

export function Toast({ text, ok }: { text: string; ok: boolean }) {
  return (
    <div
      className={cn(
        "fixed top-4 right-4 z-50 rounded-lg border px-4 py-2 text-sm shadow-lg",
        ok
          ? "border-emerald-500/40 bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
          : "border-red-500/40 bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200",
      )}
    >
      {text}
    </div>
  );
}
