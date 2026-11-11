import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { api, groupLabel, post } from "@/lib/api";
import { WEEKDAY_NAME } from "@/pages/tasks";

type FeedSub = {
  id: number; group_id: number; source: string; time: string; repeat: string;
  weekday: number | null; params: string; ai_summary: boolean; enabled: boolean;
};

export function FeedsTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [subs, setSubs] = useState<FeedSub[]>([]);
  const [groups, setGroups] = useState<number[]>([]);
  const [sources, setSources] = useState<Record<string, string>>({});
  const [form, setForm] = useState({
    group_id: 0, source: "zaobao", time: "08:30", repeat: "daily", weekday: 0,
    since: "daily", language: "", ai_summary: false,
  });
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    api<{ ok: boolean; data: { subs: FeedSub[]; groups: number[]; sources: Record<string, string> } }>(
      "/panel/api/feeds",
    ).then((r) => {
      if (r.ok) {
        setSubs(r.data.subs);
        setGroups(r.data.groups);
        setSources(r.data.sources);
        setForm((f) => ({ ...f, group_id: f.group_id || r.data.groups[0] || 0 }));
      }
    });
  }, []);
  useEffect(() => load(), [load]);

  const create = async () => {
    setBusy(true);
    const params = form.source === "github" ? { since: form.since, language: form.language } : {};
    const r = await post("/panel/api/feeds", {
      action: "create", group_id: form.group_id, source: form.source, time: form.time,
      repeat: form.repeat, weekday: form.weekday, params, ai_summary: form.ai_summary,
    });
    setBusy(false);
    if (r.ok) { toast(r.data.message); load(); } else toast(r.error || "创建失败", false);
  };
  const toggle = async (s: FeedSub) => {
    const r = await post("/panel/api/feeds", { action: "update", id: s.id, fields: { enabled: !s.enabled } });
    if (r.ok) load(); else toast(r.error, false);
  };
  const del = async (id: number) => {
    const r = await post("/panel/api/feeds", { action: "delete", id });
    if (r.ok) { toast("已删除"); load(); } else toast(r.error, false);
  };
  const pushNow = async (id: number) => {
    setBusy(true);
    const r = await post("/panel/api/feeds", { action: "push_now", id });
    setBusy(false);
    toast(r.ok ? r.data.message : r.error || "推送失败", !!r.ok);
  };

  const sel = "border-input bg-background flex h-8 items-center rounded-lg border px-2 text-sm";
  const repeatLabel = (s: { repeat: string; weekday: number | null }) =>
    ({ daily: "每天", weekdays: "工作日", weekend: "周末" } as Record<string, string>)[s.repeat] ??
    "每周" + WEEKDAY_NAME[s.weekday ?? 0];
  const paramLabel = (s: FeedSub) => {
    if (s.source !== "github") return "";
    try {
      const p = JSON.parse(s.params);
      return `（${{ daily: "日榜", weekly: "周榜", monthly: "月榜" }[p.since as string] ?? p.since}${p.language ? " · " + p.language : ""}）`;
    } catch { return ""; }
  };

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">新建内容推送</CardTitle>
          <CardDescription>
            到点后抓取内容源并推送到群；内容未就绪（如早报尚未发布）不会推旧内容，每 10 分钟重试，
            2 小时内仍失败会私聊超管
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label>内容源</Label>
              <select value={form.source} className={sel}
                onChange={(e) => setForm({ ...form, source: e.target.value })}>
                {Object.entries(sources).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>目标群</Label>
              <select value={form.group_id} className={sel}
                onChange={(e) => setForm({ ...form, group_id: +e.target.value })}>
                {groups.map((g) => <option key={g} value={g}>{groupLabel(g)}</option>)}
              </select>
            </div>
          </div>
          <div className="grid gap-2 sm:grid-cols-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="f-time">推送时间</Label>
              <Input id="f-time" type="time" value={form.time}
                onChange={(e) => setForm({ ...form, time: e.target.value })} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>重复规则</Label>
              <select value={form.repeat} className={sel}
                onChange={(e) => setForm({ ...form, repeat: e.target.value })}>
                <option value="daily">每天</option>
                <option value="weekdays">工作日</option>
                <option value="weekend">周末</option>
                <option value="weekly">每周指定日</option>
              </select>
            </div>
            {form.repeat === "weekly" && (
              <div className="flex flex-col gap-1.5">
                <Label>星期</Label>
                <select value={form.weekday} className={sel}
                  onChange={(e) => setForm({ ...form, weekday: +e.target.value })}>
                  {WEEKDAY_NAME.map((n, i) => <option key={i} value={i}>{n}</option>)}
                </select>
              </div>
            )}
          </div>
          {form.source === "github" && (
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label>榜单周期</Label>
                <select value={form.since} className={sel}
                  onChange={(e) => setForm({ ...form, since: e.target.value })}>
                  <option value="daily">日榜</option>
                  <option value="weekly">周榜</option>
                  <option value="monthly">月榜</option>
                </select>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="f-lang">编程语言（可空）</Label>
                <Input id="f-lang" value={form.language} placeholder="如 python、typescript"
                  onChange={(e) => setForm({ ...form, language: e.target.value })} />
              </div>
            </div>
          )}
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">AI 压缩 / 翻译</p>
              <p className="text-muted-foreground text-xs">
                早报压缩成要点，GitHub 简介译成中文；AI 失败时自动回退原文
              </p>
            </div>
            <Switch checked={form.ai_summary} onCheckedChange={(v) => setForm({ ...form, ai_summary: v })} />
          </div>
          <Button disabled={busy || !form.group_id} onClick={create}>
            {busy && <Spinner />}创建订阅
          </Button>
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">订阅列表（{subs.length}）</CardTitle>
        </CardHeader>
        <CardPanel>
          {subs.length === 0 ? (
            <p className="text-muted-foreground text-sm">还没有订阅，先在上方创建一个</p>
          ) : (
            <div className="grid gap-2">
              {subs.map((s) => (
                <div key={s.id}
                  className="border-input flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3">
                  <div className="text-sm">
                    <b>#{s.id}</b> [{s.time}] {repeatLabel(s)} · {sources[s.source] ?? s.source}
                    {paramLabel(s)} → {groupLabel(s.group_id)}
                    {s.ai_summary && <Badge className="ml-1">AI</Badge>}
                    {!s.enabled && <Badge variant="destructive" className="ml-1">已停用</Badge>}
                  </div>
                  <div className="flex items-center gap-2">
                    <Switch checked={s.enabled} onCheckedChange={() => toggle(s)} aria-label="启用" />
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => pushNow(s.id)}>
                      立即推送
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => del(s.id)}>删除</Button>
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
