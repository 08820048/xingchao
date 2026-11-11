import { Activity, ClipboardList, MessageSquareText, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { post, fmtDur } from "@/lib/api";
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
  log_files: string[];
  today: string;
};

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
