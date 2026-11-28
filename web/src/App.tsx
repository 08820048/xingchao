import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { Activity, BookOpen, BookMarked, ClipboardList, Coins, Gavel, LogOut, Megaphone, MessageSquareText, Newspaper, ShieldAlert, Clock3, Sparkles, UserCog, UserPlus, Users, Wrench, ShieldCheck } from "lucide-react";
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { Spinner } from "@/components/ui/spinner";
import { Login } from "@/pages/login";
import { type Status, Dashboard } from "@/pages/dashboard";
import { ThemeToggle } from "@/components/theme";
import { Toast } from "@/components/toast";
import { PageBoundary } from "@/components/page-boundary";
import { api, post, setGroupNames } from "@/lib/api";

// 除登录页和仪表盘（首屏）外，各页面按需加载，打开面板时不必下载全部页面的代码
const RepliesTab = lazy(() => import("@/pages/replies").then((m) => ({ default: m.RepliesTab })));
const JoinTab = lazy(() => import("@/pages/join").then((m) => ({ default: m.JoinTab })));
const LogsTab = lazy(() => import("@/pages/logs").then((m) => ({ default: m.LogsTab })));
const TasksTab = lazy(() => import("@/pages/tasks").then((m) => ({ default: m.TasksTab })));
const BlogTab = lazy(() => import("@/pages/blog").then((m) => ({ default: m.BlogTab })));
const SensitiveTab = lazy(() => import("@/pages/sensitive").then((m) => ({ default: m.SensitiveTab })));
const StatsTab = lazy(() => import("@/pages/stats").then((m) => ({ default: m.StatsTab })));
const PunishmentsTab = lazy(() => import("@/pages/punishments").then((m) => ({ default: m.PunishmentsTab })));
const AiTab = lazy(() => import("@/pages/ai").then((m) => ({ default: m.AiTab })));
const FeedsTab = lazy(() => import("@/pages/feeds").then((m) => ({ default: m.FeedsTab })));
const AdsTab = lazy(() => import("@/pages/ads").then((m) => ({ default: m.AdsTab })));
const KnowledgeTab = lazy(() => import("@/pages/knowledge").then((m) => ({ default: m.KnowledgeTab })));
const WorklogTab = lazy(() => import("@/pages/worklog").then((m) => ({ default: m.WorklogTab })));
const PointsTab = lazy(() => import("@/pages/points").then((m) => ({ default: m.PointsTab })));
const TopicsTab = lazy(() => import("@/pages/topics").then((m) => ({ default: m.TopicsTab })));
const GroupsTab = lazy(() => import("@/pages/groups").then((m) => ({ default: m.GroupsTab })));
const SuperusersTab = lazy(() => import("@/pages/groups").then((m) => ({ default: m.SuperusersTab })));

const NAV = [
  { id: "dashboard", label: "仪表盘", icon: Activity },
  { id: "stats", label: "统计", icon: ClipboardList },
  { id: "logs", label: "日志", icon: MessageSquareText },
  { id: "replies", label: "词库", icon: ClipboardList },
  { id: "groups", label: "白名单", icon: Users },
  { id: "superusers", label: "超管", icon: UserCog },
  { id: "ai", label: "AI", icon: Sparkles },
  { id: "blog", label: "博客", icon: BookOpen },
  { id: "join", label: "加群审批", icon: UserPlus },
  { id: "sensitive", label: "敏感词", icon: ShieldAlert },
  { id: "punishments", label: "违规记录", icon: Gavel },
  { id: "tasks", label: "定时任务", icon: Clock3 },
  { id: "feeds", label: "内容推送", icon: Newspaper },
  { id: "ads", label: "广告", icon: Megaphone },
  { id: "knowledge", label: "知识库", icon: BookMarked },
  { id: "worklog", label: "工作台账", icon: Wrench },
  { id: "points", label: "积分", icon: Coins },
  { id: "topics", label: "话题规约", icon: ShieldCheck },
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
  const [, setNamesVer] = useState(0);
  useEffect(() => {
    if (!authed) return;
    api<{ ok: boolean; data: { names: Record<string, string> } }>("/panel/api/group-names").then((r) => {
      if (r.ok) {
        setGroupNames(r.data.names);
        setNamesVer((v) => v + 1);
      }
    });
  }, [authed]);
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
          <PageBoundary key={tab}>
          <Suspense fallback={<Spinner className="mx-auto mt-16" />}>
          {tab === "dashboard" && <Dashboard status={status} toast={toast} />}
          {tab === "stats" && <StatsTab />}
          {tab === "logs" && <LogsTab />}
          {tab === "replies" && <RepliesTab toast={toast} />}
          {tab === "groups" && <GroupsTab toast={toast} />}
          {tab === "superusers" && <SuperusersTab toast={toast} />}
          {tab === "ai" && <AiTab toast={toast} />}
          {tab === "blog" && <BlogTab toast={toast} />}
          {tab === "join" && <JoinTab toast={toast} />}
          {tab === "sensitive" && <SensitiveTab toast={toast} />}
          {tab === "punishments" && <PunishmentsTab toast={toast} />}
          {tab === "tasks" && <TasksTab toast={toast} />}
          {tab === "feeds" && <FeedsTab toast={toast} />}
          {tab === "ads" && <AdsTab toast={toast} />}
          {tab === "knowledge" && <KnowledgeTab toast={toast} />}
          {tab === "worklog" && <WorklogTab toast={toast} />}
          {tab === "points" && <PointsTab toast={toast} />}
          {tab === "topics" && <TopicsTab toast={toast} />}
          </Suspense>
          </PageBoundary>
        </div>
      </SidebarInset>
      {toastState && <Toast text={toastState.t} ok={toastState.ok} />}
    </SidebarProvider>
  );
}

export default function App() {
  useEffect(() => {
    document.title = "星潮 · 管理面板";
  }, []);

  return <PanelApp />;
}
