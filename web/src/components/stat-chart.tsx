import {
  Chart,
  type ChartConfiguration,
  type ChartData,
  type ChartOptions,
  type ChartType,
  registerables,
} from "chart.js";
import { useEffect, useRef, useState } from "react";

Chart.register(...registerables);
Chart.defaults.font.family = "'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif";
Chart.defaults.font.size = 11;

export type ChartPalette = {
  ink: string;
  inkSoft: string;
  bar: string;
  grid: string;
  tick: string;
  tooltipBg: string;
  tooltipFg: string;
  tooltipBorder: string;
};

const LIGHT: ChartPalette = {
  ink: "#262626",
  inkSoft: "#737373",
  bar: "#d4d4d4",
  grid: "rgba(0, 0, 0, 0.06)",
  tick: "#737373",
  tooltipBg: "#171717",
  tooltipFg: "#fafafa",
  tooltipBorder: "rgba(255, 255, 255, 0.14)",
};

const DARK: ChartPalette = {
  ink: "#e5e5e5",
  inkSoft: "#a3a3a3",
  bar: "#404040",
  grid: "rgba(255, 255, 255, 0.07)",
  tick: "#a3a3a3",
  tooltipBg: "#fafafa",
  tooltipFg: "#171717",
  tooltipBorder: "rgba(0, 0, 0, 0.14)",
};

function useIsDark(): boolean {
  const [dark, setDark] = useState(
    () => document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    const el = document.documentElement;
    const observer = new MutationObserver(() => setDark(el.classList.contains("dark")));
    observer.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

/** 与面板黑白灰视觉一致的单色图表调色板（跟随明暗主题）。 */
export function useChartTheme(): ChartPalette {
  return useIsDark() ? DARK : LIGHT;
}

export function tooltipOptions(p: ChartPalette) {
  return {
    backgroundColor: p.tooltipBg,
    titleColor: p.tooltipFg,
    bodyColor: p.tooltipFg,
    borderColor: p.tooltipBorder,
    borderWidth: 1,
    padding: 10,
    cornerRadius: 8,
    displayColors: false,
    boxPadding: 4,
  };
}

export function axisOptions(p: ChartPalette, showGrid = true) {
  return {
    grid: showGrid ? { color: p.grid, drawTicks: false } : { display: false },
    border: { display: false },
    ticks: { color: p.tick, padding: 6 },
  };
}

/** 从深到浅的单色梯度（用于排名条形图）。 */
export function inkRamp(index: number, count: number): string {
  const alpha = count <= 1 ? 0.92 : 0.92 - (index / (count - 1)) * 0.5;
  return Math.round(alpha * 255)
    .toString(16)
    .padStart(2, "0");
}

export function StatChart({
  type,
  data,
  options,
  height = 240,
  label,
  empty = false,
  emptyText = "暂无数据",
}: {
  type: ChartType;
  data: ChartData;
  options: ChartOptions;
  height?: number;
  label: string;
  empty?: boolean;
  emptyText?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || empty) return;
    const chart = new Chart(canvas, { type, data, options } as ChartConfiguration);
    return () => chart.destroy();
  }, [type, data, options, empty]);

  if (empty) {
    return (
      <div
        className="text-muted-foreground flex items-center justify-center text-sm"
        style={{ height }}
      >
        {emptyText}
      </div>
    );
  }
  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label={label}
      className="w-full"
      style={{ height }}
    />
  );
}
