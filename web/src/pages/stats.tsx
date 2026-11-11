import { useCallback, useEffect, useState } from "react";
import { Activity, MessageSquareText, Clock3, Users } from "lucide-react";
import type { ChartData, ChartOptions } from "chart.js";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { StatChart, axisOptions, inkRamp, tooltipOptions, useChartTheme } from "@/components/stat-chart";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { api, groupLabel } from "@/lib/api";

type StatsData = {
  day: string;
  groups: { group_id: number; total: number; users: number; top: [number, number][] }[];
};

type TrendPoint = { day: string; total: number; users: number };

const TREND_RANGES = [7, 14, 30] as const;

const todayLocal = () => new Date().toLocaleDateString("sv-SE");

export function StatsTab() {
  const [day, setDay] = useState(todayLocal);
  const [scope, setScope] = useState<"all" | number>("all");
  const [range, setRange] = useState<number>(14);
  const [stats, setStats] = useState<StatsData | null>(null);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [hours, setHours] = useState<number[]>(() => Array(24).fill(0));
  const [top, setTop] = useState<[number, number][]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const palette = useChartTheme();

  const load = useCallback(async (d: string, s: "all" | number, r: number) => {
    setLoading(true);
    setError("");
    const gid = s === "all" ? "" : `&group_id=${s}`;
    try {
      const [statsRes, trendRes, hourlyRes, topRes] = await Promise.all([
        api<{ ok: boolean; data: StatsData }>(`/panel/api/stats?day=${d}`),
        api<{ ok: boolean; data: { series: TrendPoint[] } }>(
          `/panel/api/stats/trend?end=${d}&days=${r}${gid}`,
        ),
        api<{ ok: boolean; data: { hours: number[] } }>(
          `/panel/api/stats/hourly?day=${d}${gid}`,
        ),
        api<{ ok: boolean; data: { top: [number, number][] } }>(
          `/panel/api/stats/top?day=${d}&limit=10${gid}`,
        ),
      ]);
      if (statsRes.ok) {
        setStats(statsRes.data);
        // 选中的群在当天没有数据时，回退到全部群
        if (s !== "all" && !statsRes.data.groups.some((g) => g.group_id === s)) {
          setScope("all");
        }
      }
      if (trendRes.ok) setTrend(trendRes.data.series);
      if (hourlyRes.ok) setHours(hourlyRes.data.hours);
      if (topRes.ok) setTop(topRes.data.top);
    } catch {
      setError("加载失败，请检查网络后重试");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(day, scope, range);
  }, [day, scope, range, load]);

  const last = trend.length ? trend[trend.length - 1] : null;
  const total = last?.total ?? 0;
  const users = last?.users ?? 0;
  const peakHour = hours.some((v) => v > 0) ? hours.indexOf(Math.max(...hours)) : -1;
  const peak = peakHour >= 0 ? `${peakHour} 时` : "—";
  const groups = stats?.groups ?? [];

  const trendEmpty = trend.every((p) => p.total === 0 && p.users === 0);
  const hourlyEmpty = hours.every((v) => v === 0);

  const labelWithUnit = (ctx: any) =>
    `${ctx.dataset.label}: ${ctx.parsed.y} ${ctx.dataset.label === "消息数" ? "条" : "人"}`;

  const trendData: ChartData = {
    labels: trend.map((p) => p.day.slice(5)),
    datasets: [
      {
        type: "bar",
        label: "消息数",
        data: trend.map((p) => p.total),
        backgroundColor: palette.bar,
        hoverBackgroundColor: palette.inkSoft,
        borderRadius: 4,
        maxBarThickness: 26,
        yAxisID: "y",
      },
      {
        type: "line",
        label: "参与人数",
        data: trend.map((p) => p.users),
        borderColor: palette.ink,
        backgroundColor: palette.ink,
        pointBackgroundColor: palette.ink,
        pointRadius: 2,
        pointHoverRadius: 4,
        borderWidth: 2,
        tension: 0.35,
        yAxisID: "y1",
      },
    ],
  };
  const trendOptions: ChartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...tooltipOptions(palette), callbacks: { label: labelWithUnit } },
    },
    scales: {
      x: {
        grid: { display: false },
        border: { display: false },
        ticks: { color: palette.tick, maxRotation: 0, autoSkipPadding: 12 },
      },
      y: {
        beginAtZero: true,
        ...axisOptions(palette),
        ticks: { color: palette.tick, padding: 6, precision: 0 },
      },
      y1: {
        beginAtZero: true,
        position: "right",
        grid: { display: false },
        border: { display: false },
        ticks: { color: palette.tick, padding: 6, precision: 0 },
      },
    },
  };

  const barTooltip = {
    ...tooltipOptions(palette),
    callbacks: { label: (ctx: any) => `${ctx.parsed.y ?? 0} 条` },
  };
  const hbarTooltip = {
    ...tooltipOptions(palette),
    callbacks: { label: (ctx: any) => `${ctx.parsed.x ?? 0} 条` },
  };

  const hourlyData: ChartData = {
    labels: Array.from({ length: 24 }, (_, h) => `${h}`),
    datasets: [
      {
        label: "消息数",
        data: hours,
        backgroundColor: hours.map((v, h) =>
          h === peakHour && v > 0 ? palette.ink : palette.bar,
        ),
        hoverBackgroundColor: palette.inkSoft,
        borderRadius: 4,
        maxBarThickness: 18,
      },
    ],
  };
  const hourlyOptions: ChartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: barTooltip },
    scales: {
      x: {
        grid: { display: false },
        border: { display: false },
        ticks: { color: palette.tick, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
      },
      y: {
        beginAtZero: true,
        ...axisOptions(palette),
        ticks: { color: palette.tick, padding: 6, precision: 0 },
      },
    },
  };

  const groupData: ChartData = {
    labels: groups.map((g) => groupLabel(g.group_id)),
    datasets: [
      {
        label: "消息数",
        data: groups.map((g) => g.total),
        backgroundColor: groups.map((g) =>
          scope === g.group_id ? palette.ink : palette.bar,
        ),
        hoverBackgroundColor: palette.inkSoft,
        borderRadius: 4,
        maxBarThickness: 22,
      },
    ],
  };
  const hbarOptions: ChartOptions = {
    indexAxis: "y",
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: hbarTooltip },
    scales: {
      x: {
        beginAtZero: true,
        ...axisOptions(palette),
        ticks: { color: palette.tick, padding: 6, precision: 0 },
      },
      y: { grid: { display: false }, border: { display: false }, ticks: { color: palette.tick } },
    },
  };

  const topData: ChartData = {
    labels: top.map(([uid]) => String(uid)),
    datasets: [
      {
        label: "发言",
        data: top.map(([, cnt]) => cnt),
        backgroundColor: top.map((_, i) => `${palette.ink}${inkRamp(i, top.length)}`),
        hoverBackgroundColor: palette.ink,
        borderRadius: 4,
        maxBarThickness: 20,
      },
    ],
  };
  const topOptions: ChartOptions = {
    ...hbarOptions,
    scales: {
      x: {
        beginAtZero: true,
        ...axisOptions(palette),
        ticks: { color: palette.tick, padding: 6, precision: 0 },
      },
      y: {
        grid: { display: false },
        border: { display: false },
        ticks: {
          color: palette.tick,
          font: { family: "'Geist Mono', monospace" },
        },
      },
    },
  };

  return (
    <div className="grid gap-4">
      <Card>
        <CardPanel className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="stats-day">日期</Label>
            <Input
              id="stats-day"
              type="date"
              className="w-44"
              value={day}
              onChange={(e) => e.target.value && setDay(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="stats-scope">统计范围</Label>
            <select
              id="stats-scope"
              value={scope === "all" ? "all" : String(scope)}
              onChange={(e) =>
                setScope(e.target.value === "all" ? "all" : Number(e.target.value))
              }
              className="border-input bg-background flex h-8 min-w-40 items-center rounded-lg border px-2 text-sm"
            >
              <option value="all">全部群</option>
              {groups.map((g) => (
                <option key={g.group_id} value={g.group_id}>
                  {groupLabel(g.group_id)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>趋势区间</Label>
            <div className="flex gap-1">
              {TREND_RANGES.map((r) => (
                <Button
                  key={r}
                  size="sm"
                  variant={range === r ? "default" : "outline"}
                  onClick={() => setRange(r)}
                >
                  {r} 天
                </Button>
              ))}
            </div>
          </div>
          {loading && stats && (
            <span className="text-muted-foreground pb-2 text-xs">更新中…</span>
          )}
          {error && (
            <span className="pb-2 text-xs text-red-600 dark:text-red-400">{error}</span>
          )}
        </CardPanel>
      </Card>

      {!stats && loading && <Spinner className="m-8" />}
      {!stats && !loading && (
        <Card>
          <CardPanel className="flex flex-col items-center gap-3 p-10">
            <p className="text-muted-foreground text-sm">{error || "暂无统计数据"}</p>
            <Button variant="outline" size="sm" onClick={() => load(day, scope, range)}>
              重试
            </Button>
          </CardPanel>
        </Card>
      )}

      {stats && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ["当日消息", `${total} 条`, MessageSquareText],
              ["参与人数", `${users} 人`, Users],
              ["人均发言", users ? `${(total / users).toFixed(1)} 条` : "—", Activity],
              ["高峰时段", peak, Clock3],
            ].map(([k, v, Icon]) => {
              const I = Icon as typeof Activity;
              return (
                <Card key={k as string}>
                  <CardPanel className="flex items-center gap-3">
                    <I className="text-muted-foreground size-5 shrink-0" />
                    <div>
                      <p className="text-muted-foreground text-xs">{k as string}</p>
                      <p className="font-semibold">{v as string}</p>
                    </div>
                  </CardPanel>
                </Card>
              );
            })}
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader>
                <CardTitle className="text-base">消息趋势</CardTitle>
                <CardDescription>
                  近 {range} 天消息量与参与人数
                  {scope !== "all" ? `（群 ${scope}）` : "（全部群）"}
                </CardDescription>
                <CardAction>
                  <div className="text-muted-foreground flex items-center gap-3 text-xs">
                    <span className="flex items-center gap-1.5">
                      <span
                        className="size-2.5 rounded-[3px]"
                        style={{ background: palette.bar }}
                      />
                      消息数
                    </span>
                    <span className="flex items-center gap-1.5">
                      <span
                        className="h-0.5 w-3.5 rounded-full"
                        style={{ background: palette.ink }}
                      />
                      参与人数
                    </span>
                  </div>
                </CardAction>
              </CardHeader>
              <CardPanel>
                <StatChart
                  type="bar"
                  data={trendData}
                  options={trendOptions}
                  height={260}
                  label={`近 ${range} 天消息量与参与人数趋势`}
                  empty={trendEmpty}
                  emptyText="该区间暂无消息记录"
                />
              </CardPanel>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">24 小时分布</CardTitle>
                <CardDescription>{day} 各时段消息量（深色为高峰）</CardDescription>
              </CardHeader>
              <CardPanel>
                <StatChart
                  type="bar"
                  data={hourlyData}
                  options={hourlyOptions}
                  height={260}
                  label={`${day} 消息量小时分布`}
                  empty={hourlyEmpty}
                  emptyText="当日暂无消息记录"
                />
              </CardPanel>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">群消息对比</CardTitle>
                <CardDescription>
                  当日各群消息量{scope !== "all" ? "（深色为当前选中群）" : ""}
                </CardDescription>
              </CardHeader>
              <CardPanel>
                <StatChart
                  type="bar"
                  data={groupData}
                  options={hbarOptions}
                  height={Math.max(180, groups.length * 44)}
                  label="当日各群消息量对比"
                  empty={groups.length === 0}
                  emptyText="当日暂无群消息记录"
                />
              </CardPanel>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">发言 Top 10</CardTitle>
                <CardDescription>
                  {day} {scope === "all" ? "跨群" : `群 ${scope} 内`}发言排行
                </CardDescription>
              </CardHeader>
              <CardPanel>
                <StatChart
                  type="bar"
                  data={topData}
                  options={topOptions}
                  height={Math.max(180, top.length * 44)}
                  label="当日发言排行 Top 10"
                  empty={top.length === 0}
                  emptyText="当日暂无发言记录"
                />
              </CardPanel>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
