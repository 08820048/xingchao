import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api, groupLabel, post } from "@/lib/api";

type Task = {
  id: number; group_id: number; time: string; message: string;
  at_all: boolean; repeat: string; weekday: number | null; date: string | null; enabled: boolean;
};

type ReportConfig = {
  enabled: boolean;
  weekday: number;
  time: string;
  scope: string;
  groups: number[];
};

function ReportCard({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [cfg, setCfg] = useState<ReportConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const load = useCallback(() => {
    api<{ ok: boolean; data: ReportConfig }>("/panel/api/report").then(
      (r) => r.ok && setCfg(r.data),
    );
  }, []);
  useEffect(() => load(), [load]);
  if (!cfg) return <Spinner className="m-8" />;
  const save = async (patch: Record<string, unknown>) => {
    setSaving(true);
    const r = await post("/panel/api/report", patch);
    setSaving(false);
    if (r.ok) {
      toast(r.data.message);
      load();
    } else toast(r.error || "保存失败", false);
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">群活跃周报</CardTitle>
        <CardDescription>
          按设定时间向群内播报前一周活跃数据（消息量环比、参与人数、最活跃日、发言 Top3）
        </CardDescription>
      </CardHeader>
      <CardPanel className="grid gap-3">
        <div className="border-input flex items-center justify-between rounded-lg border p-3">
          <div>
            <p className="text-sm font-medium">功能开关</p>
            <p className="text-muted-foreground text-xs">关闭后不再自动播报</p>
          </div>
          <Switch checked={cfg.enabled} onCheckedChange={(v) => save({ enabled: v })} />
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          <div className="flex flex-col gap-1.5">
            <Label>播报星期</Label>
            <select
              value={cfg.weekday}
              onChange={(e) => save({ weekday: +e.target.value })}
              className="border-input bg-background flex h-8 items-center rounded-lg border px-2 text-sm"
            >
              {WEEKDAY_NAME.map((n, i) => (
                <option key={i} value={i}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rpt-time">时间</Label>
            <Input
              id="rpt-time"
              type="time"
              value={cfg.time}
              onChange={(e) => setCfg({ ...cfg, time: e.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>目标范围</Label>
            <select
              value={cfg.scope}
              onChange={(e) => save({ scope: e.target.value })}
              className="border-input bg-background flex h-8 items-center rounded-lg border px-2 text-sm"
            >
              <option value="all">全部白名单群</option>
              {cfg.groups.map((g) => (
                <option key={g} value={g}>
                  {groupLabel(g)}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="flex justify-end">
          <Button disabled={saving} onClick={() => save({ time: cfg.time })}>
            {saving && <Spinner />}保存时间
          </Button>
        </div>
      </CardPanel>
    </Card>
  );
}

export function TasksTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [groups, setGroups] = useState<number[]>([]);
  const [form, setForm] = useState({
    group_id: 0, time: "09:00", message: "", at_all: false,
    repeat: "daily", weekday: 1, date: new Date().toISOString().slice(0, 10),
  });
  const [saving, setSaving] = useState(false);
  const load = useCallback(() => {
    api<{ ok: boolean; data: { tasks: Task[]; groups: number[] } }>("/panel/api/tasks").then((r) => {
      if (r.ok) {
        setTasks(r.data.tasks);
        setGroups(r.data.groups);
        setForm((f) => ({ ...f, group_id: f.group_id || r.data.groups[0] || 0 }));
      }
    });
  }, []);
  useEffect(() => load(), [load]);
  if (!cfgGuard(tasks)) return null;
  function cfgGuard(t: Task[]): t is Task[] { return Array.isArray(t); }

  const create = async () => {
    if (!form.message.trim()) return toast("消息内容不能为空", false);
    setSaving(true);
    const r = await post("/panel/api/tasks", { action: "create", ...form });
    setSaving(false);
    if (r.ok) { toast(r.data.message); setForm({ ...form, message: "" }); load(); }
    else toast(r.error || "创建失败", false);
  };
  const toggle = async (t: Task) => {
    const r = await post("/panel/api/tasks", { action: "update", id: t.id, fields: { enabled: !t.enabled } });
    if (r.ok) load(); else toast(r.error, false);
  };
  const del = async (id: number) => {
    const r = await post("/panel/api/tasks", { action: "delete", id });
    if (r.ok) { toast("已删除"); load(); } else toast(r.error, false);
  };

  const repeatLabel: Record<string, string> = {
    daily: "每天", weekdays: "工作日", weekend: "周末",
    weekly: "每周" + WEEKDAY_NAME[form.weekday], once: "仅 " + form.date,
  };

  return (
    <div className="grid gap-4">
      <ReportCard toast={toast} />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">新建定时任务</CardTitle>
          <CardDescription>
            按北京时间（Asia/Shanghai）触发；目标群须在白名单内；@全体需要机器人是群管理员
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="grid gap-2 sm:grid-cols-[1fr_120px_1fr]">
            <div className="flex flex-col gap-1.5">
              <Label>目标群</Label>
              <select
                value={form.group_id}
                onChange={(e) => setForm({ ...form, group_id: +e.target.value })}
                className="border-input bg-background flex h-8 items-center rounded-lg border px-2 text-sm"
              >
                {groups.map((g) => <option key={g} value={g}>{groupLabel(g)}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="t">时间</Label>
              <Input id="t" type="time" value={form.time}
                onChange={(e) => setForm({ ...form, time: e.target.value })} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="r">重复规则</Label>
              <select id="r" value={form.repeat}
                onChange={(e) => setForm({ ...form, repeat: e.target.value })}
                className="border-input bg-background flex h-8 items-center rounded-lg border px-2 text-sm">
                <option value="daily">每天</option>
                <option value="weekdays">工作日</option>
                <option value="weekend">周末</option>
                <option value="weekly">每周指定日</option>
                <option value="once">仅一次</option>
              </select>
            </div>
          </div>
          {form.repeat === "weekly" && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wd">星期</Label>
              <select id="wd" value={form.weekday}
                onChange={(e) => setForm({ ...form, weekday: +e.target.value })}
                className="border-input bg-background flex h-8 w-40 items-center rounded-lg border px-2 text-sm">
                {WEEKDAY_NAME.map((n, i) => <option key={i} value={i}>{n}</option>)}
              </select>
            </div>
          )}
          {form.repeat === "once" && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="d">日期</Label>
              <Input id="d" type="date" className="w-48" value={form.date}
                onChange={(e) => setForm({ ...form, date: e.target.value })} />
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="msg">消息内容</Label>
            <Textarea id="msg" rows={3} value={form.message}
              onChange={(e) => setForm({ ...form, message: e.target.value })}
              placeholder="定时发送的消息文本" />
          </div>
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">@全体成员</p>
              <p className="text-muted-foreground text-xs">需要机器人是群管理员</p>
            </div>
            <Switch checked={form.at_all} onCheckedChange={(v) => setForm({ ...form, at_all: v })} />
          </div>
          <Button disabled={saving} onClick={create}>
            {saving && <Spinner />}创建任务
          </Button>
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">任务列表（{tasks.length}）</CardTitle>
        </CardHeader>
        <CardPanel>
          {tasks.length === 0 ? (
            <p className="text-muted-foreground text-sm">还没有定时任务，先在上方创建一个</p>
          ) : (
            <div className="grid gap-2">
              {tasks.map((t) => (
                <div key={t.id}
                  className="border-input flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3">
                  <div className="text-sm">
                    <b>#{t.id}</b> [{t.time}] {repeatLabel[t.repeat] ?? t.repeat} → {groupLabel(t.group_id)}
                    {t.at_all && <Badge className="ml-1">@全体</Badge>}
                    {!t.enabled && <Badge variant="destructive" className="ml-1">已停用</Badge>}
                    <p className="text-muted-foreground text-xs">{t.message.slice(0, 60)}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Switch checked={t.enabled} onCheckedChange={() => toggle(t)} aria-label="启用" />
                    <Button size="sm" variant="outline" onClick={() => del(t.id)}>删除</Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardPanel>
      </Card>
    </div>
  );
}

export const WEEKDAY_NAME = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
