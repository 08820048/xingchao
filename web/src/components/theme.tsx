import { useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

type Theme = "light" | "dark" | "system";

function applyTheme(theme: Theme) {
  const dark =
    theme === "dark" ||
    (theme === "system" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem("xingchao-theme") as Theme) || "system",
  );
  useEffect(() => {
    applyTheme(theme);
    localStorage.setItem("xingchao-theme", theme);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => theme === "system" && applyTheme("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);
  return [theme, setTheme];
}

export function ThemeToggle() {
  const [theme, setTheme] = useTheme();
  const next = () => {
    const order: Theme[] = ["light", "dark", "system"];
    const t = order[(order.indexOf(theme) + 1) % order.length];
    setTheme(t);
  };
  const Icon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  const label = theme === "light" ? "浅色" : theme === "dark" ? "深色" : "跟随系统";
  return (
    <Button variant="ghost" size="sm" onClick={next} title="切换主题">
      <Icon className="size-4" />
      {label}
    </Button>
  );
}
