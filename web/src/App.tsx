import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ClipboardList,
  LogOut,
  MessageSquareText,
  Monitor,
  Moon,
  Sun,
  ShieldAlert,
  Clock3,
  Sparkles,
  UserCog,
  UserPlus,
  Users,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardDescription,
  CardHeader,
  CardPanel,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
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
import LandingPage from "@/LandingPage";

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
    body: JSON.stringify(body ?? {}),
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

/* ------------------------------------------------------------------ 主题 */

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

function ThemeToggle() {
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
          <CardTitle className="flex items-center gap-2 text-xl">
            <MessageSquareText className="size-5 text-primary" />
            星潮 · 管理面板
          </CardTitle>
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

/* ------------------------------------------------------------------ 仪表盘 */

type Status = {
  uptime_seconds: number;
  plugins: string[];
  whitelist: number[];
  disabled_groups: number[];
  replies: number;
  reply_enabled: boolean;
  welcome_enabled: boolean;
  log_files: string[];
  today: string;
};

function Dashboard({
  status,
  toast,
}: {
  status: Status | null;
  toast: (t: string, ok?: boolean) => void;
}) {
  const setModule = async (key: string, enabled: boolean) => {
    const r = await post("/panel/api/modules", { key, enabled });
    if (r.ok) toast(r.data.message);
    else toast(r.error, false);
  };
  if (!status) return <Spinner className="m-8" />;
  return (
    <div className="grid gap-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ["运行时长", fmtDur(status.uptime_seconds), Activity],
          ["白名单群", `${status.whitelist.length} 个`, Users],
          ["关键词词条", `${status.replies} 条`, ClipboardList],
          ["日志文件", `${status.log_files.length} 个`, MessageSquareText],
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

      <Card>
        <CardHeader>
          <CardTitle className="text-base">模块开关</CardTitle>
          <CardDescription>关闭后立即生效，重启保留（持久化 SQLite）</CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-4 sm:grid-cols-2">
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">关键词回复</p>
              <p className="text-muted-foreground text-xs">
                词库共 {status.replies} 条
              </p>
            </div>
            <Switch
              checked={status.reply_enabled}
              onCheckedChange={(v) => setModule("reply", v)}
            />
          </div>
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">进群欢迎</p>
              <p className="text-muted-foreground text-xs">
                新成员进群时自动发送
              </p>
            </div>
            <Switch
              checked={status.welcome_enabled}
              onCheckedChange={(v) => setModule("welcome", v)}
            />
          </div>
        </CardPanel>
      </Card>

      <WelcomeCard toast={toast} />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">插件</CardTitle>
        </CardHeader>
        <CardPanel className="flex flex-wrap gap-1.5">
          {status.plugins.map((p) => (
            <Badge key={p} variant="secondary">
              {p}
            </Badge>
          ))}
        </CardPanel>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ 欢迎配置 */

function WelcomeCard({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [enabled, setEnabled] = useState(false);
  const [text, setText] = useState("");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    api<{ ok: boolean; data: { enabled: boolean; text: string } }>(
      "/panel/api/welcome",
    ).then((r) => {
      if (r.ok) {
        setEnabled(r.data.enabled);
        setText(r.data.text);
        setLoaded(true);
      }
    });
  }, []);
  const save = async () => {
    const r = await post("/panel/api/welcome", { enabled, text });
    if (r.ok) toast(r.data.message);
    else toast(r.error || "保存失败", false);
  };
  if (!loaded) return <Spinner className="m-8" />;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">进群欢迎</CardTitle>
        <CardDescription>
          新成员加入白名单群时自动发送。占位符：<code>{"{at}"}</code> = @新人、
          <code>{"{qq}"}</code> = 新人 QQ、<code>{"{group}"}</code> = 群号；保存立即生效
        </CardDescription>
      </CardHeader>
      <CardPanel className="grid gap-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Switch
              checked={enabled}
              onCheckedChange={(v) => setEnabled(v)}
              aria-label="欢迎开关"
            />
            <span className="text-sm">{enabled ? "已开启" : "已关闭"}</span>
          </div>
          <Button onClick={save}>保存配置</Button>
        </div>
        <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
      </CardPanel>
    </Card>
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
  const [loading, setLoading] = useState(true);
  const load = useCallback((d: string) => {
    setLoading(true);
    api<{ ok: boolean; data: StatsData }>(`/panel/api/stats?day=${d}`)
      .then((r) => r.ok && setData(r.data))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => load(day), []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="grid gap-4">
      <div className="flex items-end gap-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="day">日期</Label>
          <Input
            id="day"
            type="date"
            className="w-44"
            value={day}
            onChange={(e) => setDay(e.target.value)}
          />
        </div>
        <Button onClick={() => load(day)}>查询</Button>
      </div>
      {loading && <Spinner className="m-8" />}
      {!loading && data?.groups.length === 0 && (
        <Card>
          <CardPanel className="text-muted-foreground p-10 text-center text-sm">
            当日暂无消息记录
          </CardPanel>
        </Card>
      )}
      {!loading &&
        data?.groups.map((g) => (
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
                    <TableHead className="w-14">#</TableHead>
                    <TableHead>用户</TableHead>
                    <TableHead>发言</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {g.top.map(([uid, cnt], i) => (
                    <TableRow key={uid}>
                      <TableCell>
                        <Badge variant={i === 0 ? "default" : "secondary"}>{i + 1}</Badge>
                      </TableCell>
                      <TableCell className="font-mono">{uid}</TableCell>
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
  const [filter, setFilter] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    api<{ ok: boolean; data: { name: string }[] }>("/panel/api/logfiles").then((r) => {
      if (r.ok) {
        setFiles(r.data.map((f) => f.name));
        if (r.data.length) setName(r.data[r.data.length - 1].name);
      }
    });
  }, []);
  const show = async () => {
    setLoading(true);
    const r = await api<{ ok: boolean; data: { records: any[] } }>(
      `/panel/api/logs?name=${encodeURIComponent(name)}&tail=${tail}`,
    );
    if (r.ok)
      setLines(
        r.data.records.map(
          (x) =>
            `[${(x.time || "").slice(11, 19)}] ${x.user_id}: ${x.raw_plain || ""}`,
        ),
      );
    setLoading(false);
  };
  const shown = filter
    ? lines.filter((l) => l.toLowerCase().includes(filter.toLowerCase()))
    : lines;
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">群消息日志</CardTitle>
          <CardDescription>白名单群全量文本日志（jsonl），支持关键字过滤</CardDescription>
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
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="kw">过滤关键字</Label>
              <Input
                id="kw"
                className="w-44"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="如：用户QQ号"
              />
            </div>
            <Button onClick={show} disabled={!name}>
              {loading && <Spinner />}查看
            </Button>
          </div>
          <pre className="bg-muted/50 max-h-[30rem] overflow-auto rounded-lg border p-3 font-mono text-xs">
            {shown.length
              ? shown.join("\n")
              : loading
                ? "加载中…"
                : "选择日志文件后查看"}
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
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">关键词词库</CardTitle>
          <CardDescription>
            多条命中只回复顺序第一条；保存后立即热重载生效
          </CardDescription>
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
                  onChange={(e) =>
                    update(i, { match: e.target.value as "exact" | "contains" })
                  }
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
                  { id: "", enabled: true, match: "exact", pattern: "", reply: "", cooldown: 8 },
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
  const [groups, setGroups] = useState<
    { group_id: number; source: string; disabled: boolean }[]
  >([]);
  const [gid, setGid] = useState("");
  const load = useCallback(() => {
    api<{ ok: boolean; data: { groups: typeof groups } }>("/panel/api/groups").then((r) => {
      if (r.ok) setGroups(r.data.groups);
    });
  }, []);
  useEffect(() => load(), [load]);
  const act = async (action: "add" | "del" | "on" | "off", group_id?: number) => {
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
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">群白名单</CardTitle>
          <CardDescription>
            环境变量群需修改 XINGCHAO_GROUP_WHITELIST 后重启；每个群可临时开关业务（重启保留）
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
                <TableHead>业务开关</TableHead>
                <TableHead className="w-24"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.map((g) => (
                <TableRow key={g.group_id} className={g.disabled ? "opacity-60" : ""}>
                  <TableCell className="font-mono">{g.group_id}</TableCell>
                  <TableCell>
                    <Badge variant={g.source === "env" ? "secondary" : "info"}>
                      {g.source === "env" ? "环境变量" : "运行时"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Switch
                      checked={!g.disabled}
                      onCheckedChange={(v) => act(v ? "on" : "off", g.group_id)}
                    />
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

/* ------------------------------------------------------------------ 超管 */

function SuperusersTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [superusers, setSuperusers] = useState<
    { qq: number; source: string }[]
  >([]);
  const [qq, setQq] = useState("");
  const load = useCallback(() => {
    api<{ ok: boolean; data: { superusers: typeof superusers } }>(
      "/panel/api/superusers",
    ).then((r) => {
      if (r.ok) setSuperusers(r.data.superusers);
    });
  }, []);
  useEffect(() => load(), [load]);
  const act = async (action: "add" | "del", user_id?: number) => {
    const id = user_id ?? parseInt(qq);
    if (!id) return toast("请输入 QQ 号", false);
    const r = await post("/panel/api/superusers", { action, qq: id });
    if (r.ok) {
      toast(r.data.message);
      setQq("");
      load();
    } else toast(r.error, false);
  };
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">超管列表</CardTitle>
          <CardDescription>
            超管可使用全部管理指令（禁言 / 踢人 / 撤回 / 词库 / 白名单 / 群开关等）。
            环境变量超管需修改 XINGCHAO_SUPERUSERS 后重启；运行时超管立即生效、重启保留
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="flex items-end gap-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="sqq">新超管 QQ</Label>
              <Input
                id="sqq"
                type="number"
                value={qq}
                onChange={(e) => setQq(e.target.value)}
                placeholder="输入 QQ 号"
              />
            </div>
            <Button onClick={() => act("add")}>添加</Button>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>QQ 号</TableHead>
                <TableHead>来源</TableHead>
                <TableHead className="w-24"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {superusers.map((u) => (
                <TableRow key={u.qq}>
                  <TableCell className="font-mono">{u.qq}</TableCell>
                  <TableCell>
                    <Badge variant={u.source === "env" ? "secondary" : "info"}>
                      {u.source === "env" ? "环境变量" : "运行时"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {u.source === "env" ? (
                      <span className="text-muted-foreground text-xs">需改 env</span>
                    ) : (
                      <Button variant="outline" size="sm" onClick={() => act("del", u.qq)}>
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

/* ------------------------------------------------------------------ AI */

type AiConfig = {
  configured: boolean;
  base_url: string;
  api_key_masked: string;
  enabled: boolean;
  model: string;
  system_prompt: string;
  ctx_rounds: number;
  limit_group: number;
  limit_user: number;
  usage: { day: string; groups: Record<string, number>; users: Record<string, number> };
};

function AiTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [cfg, setCfg] = useState<AiConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [connBase, setConnBase] = useState("");
  const [connKey, setConnKey] = useState("");
  const load = useCallback(() => {
    api<{ ok: boolean; data: AiConfig }>("/panel/api/ai").then((r) => {
      if (r.ok) setCfg(r.data);
    });
  }, []);
  useEffect(() => load(), [load]);
  useEffect(() => {
    if (cfg) setConnBase(cfg.base_url);
  }, [cfg?.base_url]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!cfg) return <Spinner className="m-8" />;

  const save = async (patch: Record<string, unknown>) => {
    setSaving(true);
    const r = await post("/panel/api/ai", patch);
    setSaving(false);
    if (r.ok) {
      toast(r.data.message);
      load();
    } else toast(r.error || "保存失败", false);
  };

  const totalToday = Object.values(cfg.usage.groups).reduce((a, b) => a + b, 0);

  return (
    <div className="grid gap-4">
      {!cfg.configured && (
        <Card>
          <CardPanel className="border-amber-500/40 rounded-lg border bg-amber-50 p-4 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">
            ⚠️ AI 未配置：请在下方「API 连接」填写 API 地址与密钥（B.AI 平台创建的
            API Key），保存后即可在「功能开关」中开启。
          </CardPanel>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">API 连接</CardTitle>
          <CardDescription>
            OpenAI 兼容接口。B.AI 地址：<code>https://api.b.ai/v1</code>；
            Key 留空表示保持不变。保存后立即生效（无需重启）
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="aibase">API 地址</Label>
              <Input
                id="aibase"
                value={connBase}
                onChange={(e) => setConnBase(e.target.value)}
                placeholder="https://api.b.ai/v1"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="aikey">API Key</Label>
              <Input
                id="aikey"
                type="password"
                value={connKey}
                onChange={(e) => setConnKey(e.target.value)}
                placeholder={cfg.api_key_masked || "尚未设置"}
              />
            </div>
            <Button
              className="self-end"
              disabled={saving}
              onClick={async () => {
                const patch: Record<string, unknown> = {};
                if (connBase !== cfg.base_url) patch.base_url = connBase;
                if (connKey) patch.api_key = connKey;
                if (!Object.keys(patch).length) return toast("没有需要保存的修改");
                await save(patch);
                setConnKey("");
              }}
            >
              {saving && <Spinner />}保存连接
            </Button>
          </div>
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">AI 问答</CardTitle>
          <CardDescription>
            群内 @机器人（或昵称唤起）即由 AI 回答；带多轮会话记忆与每日限额护栏
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">功能开关</p>
              <p className="text-muted-foreground text-xs">
                关闭后 @ 机器人回复固定问候语
              </p>
            </div>
            <Switch checked={cfg.enabled} onCheckedChange={(v) => save({ enabled: v })} />
          </div>
          <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="model">模型名称</Label>
              <Input
                id="model"
                value={cfg.model}
                onChange={(e) => setCfg({ ...cfg, model: e.target.value })}
              />
            </div>
            <Button
              className="self-end"
              disabled={saving}
              onClick={() => save({ model: cfg.model })}
            >
              {saving && <Spinner />}保存模型
            </Button>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="prompt">系统提示词（人设）</Label>
            <Textarea
              rows={4}
              value={cfg.system_prompt}
              onChange={(e) => setCfg({ ...cfg, system_prompt: e.target.value })}
            />
          </div>
          <div className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_auto]">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="rounds">会话记忆（轮）</Label>
              <Input
                id="rounds"
                type="number"
                value={cfg.ctx_rounds}
                onChange={(e) => setCfg({ ...cfg, ctx_rounds: +e.target.value || 5 })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="lg">每群日限（次）</Label>
              <Input
                id="lg"
                type="number"
                value={cfg.limit_group}
                onChange={(e) => setCfg({ ...cfg, limit_group: +e.target.value || 100 })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="lu">每人日限（次）</Label>
              <Input
                id="lu"
                type="number"
                value={cfg.limit_user}
                onChange={(e) => setCfg({ ...cfg, limit_user: +e.target.value || 20 })}
              />
            </div>
            <Button
              className="self-end"
              disabled={saving}
              onClick={() =>
                save({
                  system_prompt: cfg.system_prompt,
                  ctx_rounds: cfg.ctx_rounds,
                  limit_group: cfg.limit_group,
                  limit_user: cfg.limit_user,
                })
              }
            >
              {saving && <Spinner />}保存
            </Button>
          </div>
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">今日用量</CardTitle>
          <CardDescription>{cfg.usage.day} · 共 {totalToday} 次</CardDescription>
        </CardHeader>
        <CardPanel>
          {totalToday === 0 ? (
            <p className="text-muted-foreground text-sm">今日还没有 AI 调用</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>维度</TableHead>
                  <TableHead>标识</TableHead>
                  <TableHead>次数</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(cfg.usage.groups).map(([gid, n]) => (
                  <TableRow key={"g" + gid}>
                    <TableCell>群</TableCell>
                    <TableCell className="font-mono">{gid}</TableCell>
                    <TableCell>{n}</TableCell>
                  </TableRow>
                ))}
                {Object.entries(cfg.usage.users).map(([uid, n]) => (
                  <TableRow key={"u" + uid}>
                    <TableCell>用户</TableCell>
                    <TableCell className="font-mono">{uid}</TableCell>
                    <TableCell>{n}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardPanel>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ 加群审批 */

type JoinConfig = {
  groups: number[];
  override: Record<string, unknown> | null;
  mode: string;
  question: string;
  fallback: string;
  keywords: string;
  leave_report: boolean;
  pending: { seq: number; group_id: number; user_id: number; comment: string }[];
};

const MODE_LABEL: Record<string, string> = {
  ai: "AI 智能审批",
  manual: "全部转人工",
  auto_approve: "全部自动通过",
  auto_reject: "全部自动拒绝",
};

function JoinTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [cfg, setCfg] = useState<JoinConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [sel, setSel] = useState<string>("global"); // "global" 或群号
  const load = useCallback((g: string) => {
    const q = g !== "global" ? `?group_id=${g}` : "";
    api<{ ok: boolean; data: JoinConfig }>(`/panel/api/join${q}`).then((r) => {
      if (r.ok) setCfg(r.data);
    });
  }, []);
  useEffect(() => load(sel), [sel, load]);
  if (!cfg) return <Spinner className="m-8" />;
  const isGroup = sel !== "global";

  const save = async (patch: Record<string, unknown>) => {
    setSaving(true);
    const r = await post("/panel/api/join", {
      ...patch,
      group_id: isGroup ? parseInt(sel) : undefined,
    });
    setSaving(false);
    if (r.ok) {
      toast(r.data.message);
      load(sel);
    } else toast(r.error || "保存失败", false);
  };
  const resolve = async (seq: number, approve: boolean) => {
    const r = await post("/panel/api/join/resolve", { seq, approve });
    if (r.ok) {
      toast(r.data.message);
      load(sel);
    } else toast(r.error, false);
  };

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">配置对象</CardTitle>
          <CardDescription>
            先选群再改配置；未单独设置的群自动继承全局默认
          </CardDescription>
        </CardHeader>
        <CardPanel className="flex flex-wrap items-center gap-2">
          <select
            value={sel}
            onChange={(e) => setSel(e.target.value)}
            className="border-input bg-background flex h-8 min-w-56 items-center rounded-lg border px-2 text-sm"
          >
            <option value="global">🌐 全局默认</option>
            {cfg.groups.map((g) => (
              <option key={g} value={g}>群 {g}</option>
            ))}
          </select>
          {isGroup &&
            (cfg.override ? (
              <Badge>该群使用独立配置</Badge>
            ) : (
              <Badge variant="secondary">继承全局（保存后成为独立配置）</Badge>
            ))}
          {isGroup && cfg.override && (
            <Button
              size="sm"
              variant="outline"
              onClick={async () => {
                const r = await post("/panel/api/join", {
                  clear_group: true, group_id: parseInt(sel),
                });
                if (r.ok) { toast(r.data.message); load(sel); }
                else toast(r.error, false);
              }}
            >
              恢复继承全局
            </Button>
          )}
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">待审批申请</CardTitle>
          <CardDescription>
            转人工的申请会通知超管私聊（/通过 序号、/拒绝 序号 [理由]），也可在此操作
          </CardDescription>
        </CardHeader>
        <CardPanel>
          {cfg.pending.length === 0 ? (
            <p className="text-muted-foreground text-sm">当前没有待审批的入群申请</p>
          ) : (
            <div className="grid gap-2">
              {cfg.pending.map((p) => (
                <div
                  key={p.seq}
                  className="border-input flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3"
                >
                  <div className="text-sm">
                    <b>#{p.seq}</b> 群 {p.group_id} · QQ {p.user_id}
                    <p className="text-muted-foreground text-xs">
                      回答：{p.comment || "（无）"}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => resolve(p.seq, true)}>通过</Button>
                    <Button size="sm" variant="outline" onClick={() => resolve(p.seq, false)}>拒绝</Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">审批策略</CardTitle>
          <CardDescription>
            AI 模式：AI 判断回答是否合理，失败时按下方兜底规则处理；转人工会通知超管
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label>审批模式</Label>
              <select
                value={cfg.mode}
                onChange={(e) => setCfg({ ...cfg, mode: e.target.value })}
                className="border-input bg-background flex h-8 items-center rounded-lg border px-2 text-sm"
              >
                {Object.entries(MODE_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>AI 不可用兜底</Label>
              <select
                value={cfg.fallback}
                onChange={(e) => setCfg({ ...cfg, fallback: e.target.value })}
                className="border-input bg-background flex h-8 items-center rounded-lg border px-2 text-sm"
              >
                <option value="manual">转人工审批</option>
                <option value="approve">自动通过</option>
                <option value="reject">自动拒绝</option>
              </select>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="q">加群验证问题</Label>
            <Input
              id="q"
              value={cfg.question}
              onChange={(e) => setCfg({ ...cfg, question: e.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="kw">规则兜底关键词（逗号分隔，回答命中即通过）</Label>
            <Input
              id="kw"
              value={cfg.keywords}
              onChange={(e) => setCfg({ ...cfg, keywords: e.target.value })}
            />
          </div>
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">退群群内播报</p>
              <p className="text-muted-foreground text-xs">
                成员退群/被移出时在群里播报；机器人被踢会通知超管
              </p>
            </div>
            <Switch
              checked={cfg.leave_report}
              onCheckedChange={(v) => save({ leave_report: v })}
            />
          </div>
          <Button disabled={saving} onClick={() => save({
            mode: cfg.mode, fallback: cfg.fallback,
            question: cfg.question, keywords: cfg.keywords,
          })}>
            {saving && <Spinner />}保存策略（{isGroup ? `群 ${sel}` : "全局默认"}）
          </Button>
        </CardPanel>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ 敏感词 */

type SensitiveConfig = {
  groups: number[];
  override: Record<string, unknown> | null;
  enabled: boolean;
  words: string;
  mute_minutes: number;
  notify: boolean;
};

function SensitiveTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [cfg, setCfg] = useState<SensitiveConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [sel, setSel] = useState<string>("global");
  const load = useCallback((g: string) => {
    const q = g !== "global" ? `?group_id=${g}` : "";
    api<{ ok: boolean; data: SensitiveConfig }>(`/panel/api/sensitive${q}`).then((r) => {
      if (r.ok) setCfg(r.data);
    });
  }, []);
  useEffect(() => load(sel), [sel, load]);
  if (!cfg) return <Spinner className="m-8" />;
  const isGroup = sel !== "global";

  const save = async (patch: Record<string, unknown>) => {
    setSaving(true);
    const r = await post("/panel/api/sensitive", {
      ...patch,
      group_id: isGroup ? parseInt(sel) : undefined,
    });
    setSaving(false);
    if (r.ok) {
      toast(r.data.message);
      load(sel);
    } else toast(r.error || "保存失败", false);
  };

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">配置对象</CardTitle>
          <CardDescription>未单独设置的群自动继承全局默认</CardDescription>
        </CardHeader>
        <CardPanel className="flex flex-wrap items-center gap-2">
          <select
            value={sel}
            onChange={(e) => setSel(e.target.value)}
            className="border-input bg-background flex h-8 min-w-56 items-center rounded-lg border px-2 text-sm"
          >
            <option value="global">🌐 全局默认</option>
            {cfg.groups.map((g) => (
              <option key={g} value={g}>群 {g}</option>
            ))}
          </select>
          {isGroup &&
            (cfg.override ? (
              <Badge>该群使用独立配置</Badge>
            ) : (
              <Badge variant="secondary">继承全局（保存后成为独立配置）</Badge>
            ))}
          {isGroup && cfg.override && (
            <Button
              size="sm"
              variant="outline"
              onClick={async () => {
                const r = await post("/panel/api/sensitive", {
                  clear_group: true, group_id: parseInt(sel),
                });
                if (r.ok) { toast(r.data.message); load(sel); }
                else toast(r.error, false);
              }}
            >
              恢复继承全局
            </Button>
          )}
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">敏感词监控与撤回</CardTitle>
          <CardDescription>
            白名单群消息命中关键词即自动撤回（可追加禁言），防广告与敏感信息；
            命中后不再触发 AI/关键词回复
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">功能开关</p>
              <p className="text-muted-foreground text-xs">按群独立生效</p>
            </div>
            <Switch checked={cfg.enabled} onCheckedChange={(v) => save({ enabled: v })} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="words">敏感词库（逗号分隔，大小写不敏感）</Label>
            <Textarea
              id="words"
              rows={3}
              value={cfg.words}
              onChange={(e) => setCfg({ ...cfg, words: e.target.value })}
              placeholder="例如：加微信,低价代刷,博彩,代开发票"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mm">命中后禁言（分钟，0 = 不禁言）</Label>
            <Input
              id="mm"
              type="number"
              className="w-48"
              value={cfg.mute_minutes}
              onChange={(e) => setCfg({ ...cfg, mute_minutes: +e.target.value || 0 })}
            />
          </div>
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">命中后通知超管</p>
              <p className="text-muted-foreground text-xs">私聊推送命中详情与处理结果</p>
            </div>
            <Switch checked={cfg.notify} onCheckedChange={(v) => save({ notify: v })} />
          </div>
          <Button disabled={saving} onClick={() => save({
            words: cfg.words, mute_minutes: cfg.mute_minutes,
          })}>
            {saving && <Spinner />}保存词库与禁言设置（{isGroup ? `群 ${sel}` : "全局默认"}）
          </Button>
        </CardPanel>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ 定时任务 */

type Task = {
  id: number; group_id: number; time: string; message: string;
  at_all: boolean; repeat: string; weekday: number | null; date: string | null; enabled: boolean;
};

function TasksTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
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
                {groups.map((g) => <option key={g} value={g}>群 {g}</option>)}
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
                    <b>#{t.id}</b> [{t.time}] {repeatLabel[t.repeat] ?? t.repeat} → 群 {t.group_id}
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

const WEEKDAY_NAME = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

/* ------------------------------------------------------------------ 布局 */

const NAV = [
  { id: "dashboard", label: "仪表盘", icon: Activity },
  { id: "stats", label: "统计", icon: ClipboardList },
  { id: "logs", label: "日志", icon: MessageSquareText },
  { id: "replies", label: "词库", icon: ClipboardList },
  { id: "groups", label: "白名单", icon: Users },
  { id: "superusers", label: "超管", icon: UserCog },
  { id: "ai", label: "AI", icon: Sparkles },
  { id: "join", label: "加群审批", icon: UserPlus },
  { id: "sensitive", label: "敏感词", icon: ShieldAlert },
  { id: "tasks", label: "定时任务", icon: Clock3 },
] as const;

function PanelApp() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [tab, setTab] = useState<(typeof NAV)[number]["id"]>("dashboard");
  const [status, setStatus] = useState<Status | null>(null);
  const [toastState, setToastState] = useState<{ t: string; ok: boolean } | null>(null);
  const toast = useCallback((t: string, ok = true) => {
    setToastState({ t, ok });
    setTimeout(() => setToastState(null), 2500);
  }, []);
  const loadStatus = useCallback(() => {
    api<{ ok: boolean; data: Status }>("/panel/api/status").then((r) => r.ok && setStatus(r.data));
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

  if (authed === null) return <Spinner className="mx-auto mt-32" />;
  if (!authed) return <Login onOk={() => setAuthed(true)} />;

  return (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader>
          <div className="text-foreground flex items-center gap-2 px-2 py-1.5">
            <MessageSquareText className="text-primary size-5" />
            <div className="grid leading-tight">
              <span className="text-sm font-semibold">星潮 Xingchao</span>
              <span className="text-muted-foreground text-[11px]">QQ 群助手控制台</span>
            </div>
          </div>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>管理</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {NAV.map((n) => (
                  <SidebarMenuItem key={n.id}>
                    <SidebarMenuButton
                      isActive={tab === n.id}
                      tooltip={n.label}
                      onClick={() => {
                        setTab(n.id);
                        if (n.id === "dashboard") loadStatus();
                      }}
                    >
                      <n.icon className="size-4" />
                      <span>{n.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton tooltip="退出登录" onClick={() => post("/panel/api/logout").then(() => setAuthed(false))}>
                <LogOut className="size-4" />
                <span>退出登录</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
      </Sidebar>
      <SidebarInset>
        <header className="bg-background/80 sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b px-4 backdrop-blur">
          <SidebarTrigger />
          <span className="text-sm font-medium">
            {NAV.find((n) => n.id === tab)?.label}
          </span>
          <div className="ml-auto flex items-center gap-1">
            <ThemeToggle />
          </div>
        </header>
        <div className="p-4 sm:p-6">
          {tab === "dashboard" && <Dashboard status={status} toast={toast} />}
          {tab === "stats" && <StatsTab />}
          {tab === "logs" && <LogsTab />}
          {tab === "replies" && <RepliesTab toast={toast} />}
          {tab === "groups" && <GroupsTab toast={toast} />}
          {tab === "superusers" && <SuperusersTab toast={toast} />}
          {tab === "ai" && <AiTab toast={toast} />}
          {tab === "join" && <JoinTab toast={toast} />}
          {tab === "sensitive" && <SensitiveTab toast={toast} />}
          {tab === "tasks" && <TasksTab toast={toast} />}
        </div>
      </SidebarInset>
      {toastState && <Toast text={toastState.t} ok={toastState.ok} />}
    </SidebarProvider>
  );
}

export default function App() {
  const panel = window.location.pathname.startsWith("/panel");

  useEffect(() => {
    document.title = panel ? "星潮 · 管理面板" : "星潮 Xingchao · 懂分寸的 QQ 群助手";
  }, [panel]);

  return panel ? <PanelApp /> : <LandingPage />;
}
