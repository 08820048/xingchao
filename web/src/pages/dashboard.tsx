import { useEffect, useState } from "react";
import { Activity, ClipboardList, Clock3, MessageSquareText, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { api, post, fmtDur } from "@/lib/api";
import { VisionCard } from "@/pages/sensitive";
import { WelcomeCard } from "@/pages/replies";

export type Status = {
  uptime_seconds: number;
  plugins: string[];
  whitelist: number[];
  disabled_groups: number[];
  replies: number;
  reply_enabled: boolean;
  welcome_enabled: boolean;
  link_preview_enabled: boolean;
  vision_guard_enabled: boolean;
  proactive_enabled: boolean;
  blog_enabled: boolean;
  ad_enabled: boolean;
  log_files: string[];
  today: string;
};

type UptimeEvent = {
  id: number;
  ts: string;
  event: string;
  detail: string;
  duration_seconds: number;
};

type UptimeData = {
  events: UptimeEvent[];
  summary: {
    connected: boolean;
    qq_online: boolean | null;
    currently_offline: boolean;
    offline_count_7d: number;
    avg_online_seconds: number;
    online_seconds: number;
    last_online: UptimeEvent | null;
    last_offline: UptimeEvent | null;
  };
};

const fmtTime = (ts?: string | null) => {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString("zh-CN", { hour12: false });
};

function UptimeCard() {
  const [data, setData] = useState<UptimeData | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => {
      api<{ ok: boolean; data: UptimeData }>("/panel/api/uptime?limit=12")
        .then((r) => {
          if (alive && r.ok) setData(r.data);
        })
        .catch(() => {});
    };
    load();
    const timer = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const summary = data?.summary;
  const events = data?.events ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Clock3 className="size-4" /> QQ 连接记录
        </CardTitle>
        <CardDescription>掉线时间、每次在线时长与近 7 天统计（每 30 秒刷新）</CardDescription>
      </CardHeader>
      <CardPanel className="grid gap-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="border-input rounded-lg border p-3">
            <p className="text-muted-foreground text-xs">当前状态</p>
            <p className="font-semibold">
              {summary
                ? summary.currently_offline
                  ? "离线"
                  : summary.qq_online === true
                    ? "在线"
                    : summary.qq_online === null && summary.connected
                      ? "未知"
                      : "离线"
                : "—"}
            </p>
          </div>
          <div className="border-input rounded-lg border p-3">
            <p className="text-muted-foreground text-xs">最近掉线</p>
            <p className="font-semibold">{fmtTime(summary?.last_offline?.ts)}</p>
          </div>
          <div className="border-input rounded-lg border p-3">
            <p className="text-muted-foreground text-xs">近 7 天掉线</p>
            <p className="font-semibold">{summary?.offline_count_7d ?? "—"} 次</p>
          </div>
          <div className="border-input rounded-lg border p-3">
            <p className="text-muted-foreground text-xs">平均在线</p>
            <p className="font-semibold">
              {summary?.avg_online_seconds ? fmtDur(summary.avg_online_seconds) : "—"}
            </p>
          </div>
        </div>

        {events.length === 0 ? (
          <p className="text-muted-foreground text-sm">还没有连接记录，等下一次上线/掉线后出现。</p>
        ) : (
          <div className="divide-input divide-y rounded-lg border">
            {events.map((e) => (
              <div key={e.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
                <Badge variant={e.event === "offline" ? "error" : "success"}>
                  {e.event === "offline" ? "掉线" : e.event === "online" ? "上线" : e.event}
                </Badge>
                <span className="text-muted-foreground text-xs">{fmtTime(e.ts)}</span>
                <span className="min-w-0 flex-1 truncate text-xs">{e.detail || "—"}</span>
                {e.event === "offline" && e.duration_seconds > 0 && (
                  <span className="text-muted-foreground text-xs">在线 {fmtDur(e.duration_seconds)}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </CardPanel>
    </Card>
  );
}

export function Dashboard({
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

      <UptimeCard />

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
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">链接自动解读</p>
              <p className="text-muted-foreground text-xs">
                群里发链接时 AI 一句话概括
              </p>
            </div>
            <Switch
              checked={status.link_preview_enabled}
              onCheckedChange={(v) => setModule("link", v)}
            />
          </div>
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">图片识别与违规处理</p>
              <p className="text-muted-foreground text-xs">
                违规图片自动撤回并禁言
              </p>
            </div>
            <Switch
              checked={status.vision_guard_enabled}
              onCheckedChange={(v) => setModule("vision", v)}
            />
          </div>
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">群聊主动性</p>
              <p className="text-muted-foreground text-xs">
                话题明确或有人求助时 AI 自动参与
              </p>
            </div>
            <Switch
              checked={status.proactive_enabled}
              onCheckedChange={(v) => setModule("proactive", v)}
            />
          </div>
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">博客知识关联推荐</p>
              <p className="text-muted-foreground text-xs">
                话题与 xuyi.dev 的文章/作品相关时优先推荐
              </p>
            </div>
            <Switch
              checked={status.blog_enabled}
              onCheckedChange={(v) => setModule("blog", v)}
            />
          </div>
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">广告招商</p>
              <p className="text-muted-foreground text-xs">
                群内自发广告自动撤回并引导商务合作，合作方放行
              </p>
            </div>
            <Switch
              checked={status.ad_enabled}
              onCheckedChange={(v) => setModule("ads", v)}
            />
          </div>
        </CardPanel>
      </Card>

      <VisionCard toast={toast} />

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
