import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { api, groupLabel, post } from "@/lib/api";

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

export function JoinTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
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
              <option key={g} value={g}>{groupLabel(g)}</option>
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
                    <b>#{p.seq}</b> {groupLabel(p.group_id)} · QQ {p.user_id}
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
              <p className="text-sm font-medium">退群通知管理员</p>
              <p className="text-muted-foreground text-xs">
                成员退群/被移出时私聊通知超管，群内不播报
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
