import { useCallback, useEffect, useState } from "react";
import { Activity, BookOpen, ClipboardList, Gavel, LogOut, MessageSquareText, Newspaper, ShieldAlert, Clock3, Sparkles, UserCog, UserPlus, Users } from "lucide-react";
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { Spinner } from "@/components/ui/spinner";
import { RepliesTab } from "@/pages/replies";
import { Login } from "@/pages/login";
import { type Status, Dashboard } from "@/pages/dashboard";
import { JoinTab } from "@/pages/join";
import { LogsTab } from "@/pages/logs";
import { TasksTab } from "@/pages/tasks";
import { BlogTab } from "@/pages/blog";
import { SensitiveTab } from "@/pages/sensitive";
import { StatsTab } from "@/pages/stats";
import { PunishmentsTab } from "@/pages/punishments";
import { AiTab } from "@/pages/ai";
import { FeedsTab } from "@/pages/feeds";
import { GroupsTab, SuperusersTab } from "@/pages/groups";
import { ThemeToggle } from "@/components/theme";
import { Toast } from "@/components/toast";
import { api, post, setGroupNames } from "@/lib/api";

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
