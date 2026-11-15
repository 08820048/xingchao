import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { api, groupLabel, post } from "@/lib/api";

type WorklogData = {
  enabled: boolean;
  weekly: boolean;
  weekday: number;
  time: string;
  scope: string;
  groups: number[];
  actions: Record<string, number>;
  by_group: Record<string, Record<string, number>>;
  range: string[];
  weekdays: string[];
  preview: string;
};

const ACTION_LABEL: Record<string, string> = {
  ai_reply: "回答提问",
  kb_answer: "群知识命中",
  recall_query: "翻出旧消息",
  link_preview: "解读链接",
  essence_set: "设为精华",
};

export function WorklogTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [d, setD] = useState<WorklogData | null>(null);
  const [busy, setBusy] = useState(false);
  const sel = "border-input bg-background flex h-8 items-center rounded-lg border px-2 text-sm";

  const load = useCallback(() => {
    api<{ ok: boolean; data: WorklogData }>("/panel/api/worklog").then((r) => {
      if (r.ok) setD(r.data);
    });
  }, []);
  useEffect(load, [load]);
  if (!d) return <Spinner className="m-8" />;

  const save = async (body: Record<string, unknown>) => {
    setBusy(true);
    const r = await post("/panel/api/worklog", body);
    setBusy(false);
    if (r.ok) { toast(r.data?.message || "已保存"); load(); }
    else toast(r.error || "保存失败", false);
  };

  const total = Object.values(d.actions).reduce((a, b) => a + b, 0);

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">工作台账</CardTitle>
          <CardDescription>
            把机器人实际做过的事摊开：拦了多少广告、答了多少问题、翻出多少旧消息。
            这些活干得越多越看不见，台账就是为了让它显性化。只统计真实发生的事，没做的不会编
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2">
              <Switch checked={d.enabled} onCheckedChange={(v) => save({ enabled: v })} />
              <Label>允许 /台账 查询</Label>
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={d.weekly} onCheckedChange={(v) => save({ weekly: v })} />
              <Label>每周自动播报到群</Label>
            </div>
            <Badge variant="secondary">
              {d.range[0]} ~ {d.range[1]} 共 {total} 次
            </Badge>
          </div>

          {d.weekly && (
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex flex-col gap-1.5">
                <Label>星期</Label>
                <select className={sel} value={d.weekday}
                  onChange={(e) => save({ weekday: +e.target.value })}>
                  {d.weekdays.map((w, i) => <option key={w} value={i}>{w}</option>)}
                </select>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="wl-time">时间</Label>
                <Input id="wl-time" type="time" defaultValue={d.time}
                  onBlur={(e) => save({ time: e.target.value })} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label>范围</Label>
                <select className={sel} value={d.scope}
                  onChange={(e) => save({ scope: e.target.value })}>
                  <option value="all">全部白名单群</option>
                  {d.groups.map((g) => <option key={g} value={g}>{groupLabel(g)}</option>)}
                </select>
              </div>
            </div>
          )}
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">近 7 天动作分布</CardTitle>
          <CardDescription>按动作类型统计，可用于判断哪些能力真被用上了</CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          {total === 0 ? (
            <p className="text-muted-foreground text-sm">这周还没有记录到任何动作</p>
          ) : (
            <div className="grid gap-2">
              {Object.entries(d.actions)
                .sort((a, b) => b[1] - a[1])
                .map(([key, n]) => (
                  <div key={key} className="flex items-center gap-3">
                    <span className="w-28 shrink-0 text-sm">{ACTION_LABEL[key] || key}</span>
                    <div className="bg-muted h-2 flex-1 overflow-hidden rounded-full">
                      <div className="bg-primary h-full rounded-full"
                        style={{ width: `${Math.max(4, (n / total) * 100)}%` }} />
                    </div>
                    <span className="w-12 shrink-0 text-right text-sm tabular-nums">{n}</span>
                  </div>
                ))}
            </div>
          )}

          {Object.keys(d.by_group).length > 0 && (
            <div className="flex flex-wrap gap-2">
              {Object.entries(d.by_group).map(([gid, acts]) => (
                <span key={gid} className="border-input rounded-md border px-2 py-1 text-xs">
                  {groupLabel(gid)}：{Object.values(acts).reduce((a, b) => a + b, 0)} 次
                </span>
              ))}
            </div>
          )}
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">台账预览</CardTitle>
          <CardDescription>这就是群友会看到的内容（全部群合计）</CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-2">
          <pre className="bg-muted/50 max-h-96 overflow-auto rounded-lg p-3 text-xs whitespace-pre-wrap">
            {d.preview}
          </pre>
          <div>
            <Button variant="outline" onClick={load} disabled={busy}>刷新</Button>
          </div>
        </CardPanel>
      </Card>
    </div>
  );
}
