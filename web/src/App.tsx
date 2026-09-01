import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardDescription,
  CardHeader,
  CardPanel,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ API */

async function api<T = any>(path: string, opt: RequestInit = {}): Promise<T> {
  const r = await fetch(path, opt);
  if (r.status === 401) {
    window.dispatchEvent(new Event("panel:unauthorized"));
    throw new Error("unauthorized");
  }
  return r.json();
}
const post = (p: string, body?: unknown) =>
  api(p, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

const fmtDur = (s: number) => {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return (h ? `${h} 小时 ` : "") + `${m} 分钟`;
};

function Toast({ text, ok }: { text: string; ok: boolean }) {
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

/* ------------------------------------------------------------------ 登录 */

function Login({ onOk }: { onOk: () => void }) {
  const [pwd, setPwd] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const submit = async () => {
    setBusy(true);
    setErr("");
    const r = await fetch("/panel/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pwd }),
    });
    setBusy(false);
    if (r.ok) onOk();
    else setErr((await r.json()).error || "登录失败");
  };
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl">星潮 · 管理面板</CardTitle>
          <CardDescription>请输入面板密码登录</CardDescription>
        </CardHeader>
        <CardPanel className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="pwd">密码</Label>
            <Input
              id="pwd"
              type="password"
              value={pwd}
              onChange={(e) => setPwd(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              autoFocus
            />
            {err && <p className="text-xs text-red-600 dark:text-red-400">{err}</p>}
          </div>
          <Button onClick={submit} disabled={busy || !pwd}>
            {busy && <Spinner />}登 录
          </Button>
        </CardPanel>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ 状态 */

type Status = {
  uptime_seconds: number;
  plugins: string[];
  whitelist: number[];
  replies: number;
  reply_enabled: boolean;
  welcome_enabled: boolean;
  log_files: string[];
  today: string;
};

function StatusTab() {
  const [s, setS] = useState<Status | null>(null);
  useEffect(() => {
    api<{ ok: boolean; data: Status }>("/panel/api/status").then((r) =>
      r.ok && setS(r.data),
    );
  }, []);
  if (!s) return <Spinner className="m-8" />;
  return (
    <div className="grid gap-3">
      <Card>
        <CardPanel className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          {[
            ["运行时长", fmtDur(s.uptime_seconds)],
            ["白名单群", `${s.whitelist.length} 个`],
            ["关键词词条", `${s.replies} 条`],
            ["关键词模块", s.reply_enabled],
            ["进群欢迎", s.welcome_enabled],
            ["插件", s.plugins.join(" / ")],
          ].map(([k, v], i) => (
            <div key={k as string}>
              <p className="text-muted-foreground text-xs">{k}</p>
              {typeof v === "boolean" ? (
                <Badge variant={v ? "success" : "destructive"} className="mt-1">
                  {v ? "开启" : "关闭"}
                </Badge>
              ) : i === 5 ? (
                <p className="text-sm">{v}</p>
              ) : (
                <p className="text-lg font-semibold">{v}</p>
              )}
            </div>
          ))}
        </CardPanel>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">白名单群</CardTitle>
        </CardHeader>
        <CardPanel className="flex flex-wrap gap-1.5">
          {s.whitelist.map((g) => (
            <Badge key={g} variant="outline">
              {g}
            </Badge>
          ))}
        </CardPanel>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ 统计 */

type StatsData = {
  day: string;
  groups: { group_id: number; total: number; users: number; top: [number, number][] }[];
};

function StatsTab() {
  const [day, setDay] = useState(new Date().toISOString().slice(0, 10));
  const [data, setData] = useState<StatsData | null>(null);
  const load = useCallback((d: string) => {
    api<{ ok: boolean; data: StatsData }>(`/panel/api/stats?day=${d}`).then((r) =>
      r.ok && setData(r.data),
    );
  }, []);
  useEffect(() => load(day), []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="grid gap-3">
      <div className="flex items-end gap-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="day">日期</Label>
          <Input id="day" type="date" value={day} onChange={(e) => setDay(e.target.value)} />
        </div>
        <Button onClick={() => load(day)}>查询</Button>
      </div>
      {data?.groups.length === 0 && (
        <Card>
          <CardPanel className="text-muted-foreground p-6 text-center text-sm">
            当日暂无消息记录
          </CardPanel>
        </Card>
      )}
      {data?.groups.map((g) => (
        <Card key={g.group_id}>
          <CardHeader>
            <CardTitle className="text-base">群 {g.group_id}</CardTitle>
            <CardDescription>
              消息 {g.total} 条 · 参与 {g.users} 人
            </CardDescription>
          </CardHeader>
          <CardPanel>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>#</TableHead>
                  <TableHead>用户</TableHead>
                  <TableHead>发言</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {g.top.map(([uid, cnt], i) => (
                  <TableRow key={uid}>
                    <TableCell>{i + 1}</TableCell>
                    <TableCell>{uid}</TableCell>
                    <TableCell>{cnt} 条</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardPanel>
        </Card>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ 日志 */

function LogsTab() {
  const [files, setFiles] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [tail, setTail] = useState(200);
  const [lines, setLines] = useState<string[]>([]);
  useEffect(() => {
    api<{ ok: boolean; data: { name: string }[] }>("/panel/api/logfiles").then((r) => {
      if (r.ok) {
        setFiles(r.data.map((f) => f.name));
        if (r.data.length) setName(r.data[r.data.length - 1].name);
      }
    });
  }, []);
  const show = async () => {
    const r = await api<{ ok: boolean; data: { records: any[] } }>(
      `/panel/api/logs?name=${encodeURIComponent(name)}&tail=${tail}`,
    );
    if (r.ok)
      setLines(
        r.data.records.map(
          (x) => `[${(x.time || "").slice(11, 19)}] ${x.user_id}: ${x.raw_plain || ""}`,
        ),
      );
  };
  return (
    <div className="grid gap-3">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">群消息日志</CardTitle>
          <CardDescription>查看白名单群全量文本日志（jsonl）</CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex min-w-56 flex-col gap-1.5">
              <Label htmlFor="lf">日志文件</Label>
              <select
                id="lf"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="border-input bg-background flex h-8 w-full items-center rounded-lg border px-3 text-sm"
              >
                {files.map((f) => (
                  <option key={f}>{f}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="tail">行数</Label>
              <Input
                id="tail"
                type="number"
                className="w-24"
                value={tail}
                onChange={(e) => setTail(+e.target.value || 200)}
              />
            </div>
            <Button onClick={show} disabled={!name}>
              查看
            </Button>
          </div>
          <pre className="bg-muted/50 max-h-[26rem] overflow-auto rounded-lg border p-3 font-mono text-xs">
            {lines.length ? lines.join("\n") : "选择日志文件后查看"}
          </pre>
        </CardPanel>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ 词库 */

type ReplyItem = {
  id: string;
  enabled: boolean;
  match: "exact" | "contains";
  pattern: string;
  reply: string;
  cooldown: number;
};

function RepliesTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [items, setItems] = useState<ReplyItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    api<{ ok: boolean; data: { items: ReplyItem[] } }>("/panel/api/replies").then((r) => {
      if (r.ok) {
        setItems(r.data.items);
        setLoaded(true);
      }
    });
  }, []);
  const update = (i: number, patch: Partial<ReplyItem>) =>
    setItems(items.map((it, j) => (j === i ? { ...it, ...patch } : it)));
  const save = async () => {
    const r = await post("/panel/api/replies", { items });
    if (r.ok) toast(`已保存并热重载，共 ${r.data.count} 条词条`);
    else toast(r.error || "保存失败", false);
  };
  if (!loaded) return <Spinner className="m-8" />;
  return (
    <div className="grid gap-3">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">关键词词库</CardTitle>
          <CardDescription>多条命中只回复顺序第一条；保存后立即热重载生效</CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          {items.map((it, i) => (
            <div
              key={i}
              className="grid gap-2 rounded-lg border p-3 sm:grid-cols-[1fr_1fr_130px_110px_auto]"
            >
              <div className="flex flex-col gap-1">
                <Label className="text-xs">ID</Label>
                <Input value={it.id} onChange={(e) => update(i, { id: e.target.value })} />
              </div>
              <div className="flex flex-col gap-1">
                <Label className="text-xs">触发词</Label>
                <Input
                  value={it.pattern}
                  onChange={(e) => update(i, { pattern: e.target.value })}
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label className="text-xs">匹配方式</Label>
                <select
                  value={it.match}
                  onChange={(e) => update(i, { match: e.target.value as "exact" | "contains" })}
                  className="border-input bg-background flex h-8 w-full items-center rounded-lg border px-2 text-sm"
                >
                  <option value="exact">精确</option>
                  <option value="contains">包含</option>
                </select>
              </div>
              <div className="flex flex-col gap-1">
                <Label className="text-xs">冷却(秒)</Label>
                <Input
                  type="number"
                  value={it.cooldown}
                  onChange={(e) => update(i, { cooldown: +e.target.value || 8 })}
                />
              </div>
              <div className="flex items-end gap-2">
                <Switch
                  checked={it.enabled}
                  onCheckedChange={(v) => update(i, { enabled: v })}
                  aria-label="启用"
                />
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-muted-foreground"
                  onClick={() => setItems(items.filter((_, j) => j !== i))}
                >
                  ✕
                </Button>
              </div>
              <div className="flex flex-col gap-1 sm:col-span-4">
                <Label className="text-xs">回复内容</Label>
                <Textarea
                  rows={2}
                  value={it.reply}
                  onChange={(e) => update(i, { reply: e.target.value })}
                />
              </div>
            </div>
          ))}
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() =>
                setItems([
                  ...items,
                  {
                    id: "",
                    enabled: true,
                    match: "exact",
                    pattern: "",
                    reply: "",
                    cooldown: 8,
                  },
                ])
              }
            >
              + 新增词条
            </Button>
            <Button onClick={save}>保存并热重载</Button>
          </div>
        </CardPanel>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ 白名单 */

function GroupsTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [groups, setGroups] = useState<{ group_id: number; source: string }[]>([]);
  const [gid, setGid] = useState("");
  const load = useCallback(() => {
    api<{ ok: boolean; data: { groups: typeof groups } }>("/panel/api/groups").then((r) => {
      if (r.ok) setGroups(r.data.groups);
    });
  }, []);
  useEffect(() => load(), [load]);
  const act = async (action: "add" | "del", group_id?: number) => {
    const id = group_id ?? parseInt(gid);
    if (!id) return toast("请输入群号", false);
    const r = await post("/panel/api/groups", { action, group_id: id });
    if (r.ok) {
      toast(r.data.message);
      setGid("");
      load();
    } else toast(r.error, false);
  };
  return (
    <div className="grid gap-3">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">群白名单</CardTitle>
          <CardDescription>
            运行时群立即生效且重启保留；环境变量群需修改 XINGCHAO_GROUP_WHITELIST 后重启
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="flex items-end gap-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="gid">新群号</Label>
              <Input
                id="gid"
                type="number"
                value={gid}
                onChange={(e) => setGid(e.target.value)}
                placeholder="输入群号"
              />
            </div>
            <Button onClick={() => act("add")}>添加</Button>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>群号</TableHead>
                <TableHead>来源</TableHead>
                <TableHead className="w-24"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.map((g) => (
                <TableRow key={g.group_id}>
                  <TableCell className="font-mono">{g.group_id}</TableCell>
                  <TableCell>
                    <Badge variant={g.source === "env" ? "secondary" : "info"}>
                      {g.source === "env" ? "环境变量" : "运行时"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {g.source === "env" ? (
                      <span className="text-muted-foreground text-xs">需改 env</span>
                    ) : (
                      <Button variant="outline" size="sm" onClick={() => act("del", g.group_id)}>
                        移除
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardPanel>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ 主界面 */

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [toastState, setToastState] = useState<{ t: string; ok: boolean } | null>(null);
  const toast = useCallback((t: string, ok = true) => {
    setToastState({ t, ok });
    setTimeout(() => setToastState(null), 2500);
  }, []);
  const check = useCallback(() => {
    api("/panel/api/status")
      .then(() => setAuthed(true))
      .catch(() => setAuthed(false));
  }, []);
  useEffect(() => {
    check();
    const h = () => setAuthed(false);
    window.addEventListener("panel:unauthorized", h);
    return () => window.removeEventListener("panel:unauthorized", h);
  }, [check]);

  if (authed === null)
    return <Spinner className="mx-auto mt-32" />;
  if (!authed) return <Login onOk={() => setAuthed(true)} />;

  return (
    <div className="mx-auto max-w-4xl p-6">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-xl font-semibold">星潮 · 管理面板</h1>
        <Button variant="ghost" size="sm" onClick={() => post("/panel/api/logout").then(() => setAuthed(false))}>
          退出
        </Button>
      </div>
      <p className="text-muted-foreground mb-4 text-sm">
        星潮 Xingchao — 开源 QQ 群助手 · Web 控制台
      </p>
      <Tabs defaultValue="status">
        <TabsList>
          <TabsTab value="status">状态</TabsTab>
          <TabsTab value="stats">统计</TabsTab>
          <TabsTab value="logs">日志</TabsTab>
          <TabsTab value="replies">词库</TabsTab>
          <TabsTab value="groups">白名单</TabsTab>
        </TabsList>
        <TabsPanel value="status">
          <StatusTab />
        </TabsPanel>
        <TabsPanel value="stats">
          <StatsTab />
        </TabsPanel>
        <TabsPanel value="logs">
          <LogsTab />
        </TabsPanel>
        <TabsPanel value="replies">
          <RepliesTab toast={toast} />
        </TabsPanel>
        <TabsPanel value="groups">
          <GroupsTab toast={toast} />
        </TabsPanel>
      </Tabs>
      {toastState && <Toast text={toastState.t} ok={toastState.ok} />}
    </div>
  );
}
