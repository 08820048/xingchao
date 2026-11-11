import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, groupLabel, post } from "@/lib/api";

type Punishment = {
  id: number;
  ts: string;
  group_id: number;
  user_id: number;
  word: string;
  action: string;
  mute_minutes: number;
  reason: string;
  appeal_status: string;
  appeal_text: string;
  source: string;
};

type PunishmentsData = {
  records: Punishment[];
  offenders: { user_id: number; count: number; groups: number }[];
  appeals: {
    id: number;
    ts: string;
    group_id: number;
    user_id: number;
    word: string;
    action: string;
    appeal_text: string;
    appeal_ts: string;
    source: string;
  }[];
  groups: number[];
};

const SOURCE_LABEL: Record<string, string> = {
  sensitive: "敏感词",
  vision: "图片",
  qrcode: "二维码",
};

const ACTION_LABEL: Record<string, string> = {
  recall: "撤回",
  mute: "禁言",
  recall_mute: "撤回+禁言",
};

const APPEAL_LABEL: Record<string, string> = {
  none: "—",
  pending: "待处理",
  accepted: "已通过",
  rejected: "已驳回",
};

const shortTime = (ts: string) => ts.slice(5, 16).replace("T", " ");

export function PunishmentsTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [data, setData] = useState<PunishmentsData | null>(null);
  const [gid, setGid] = useState("all");
  const [note, setNote] = useState("");
  const load = useCallback((g: string) => {
    const q = g !== "all" ? `?group_id=${g}` : "";
    api<{ ok: boolean; data: PunishmentsData }>(`/panel/api/punishments${q}`).then(
      (r) => r.ok && setData(r.data),
    );
  }, []);
  useEffect(() => load(gid), [gid, load]);
  if (!data) return <Spinner className="m-8" />;
  const resolve = async (id: number, approve: boolean) => {
    const r = await post("/panel/api/punishments", { action: "resolve", id, approve, note });
    if (r.ok) {
      toast(r.data.message);
      setNote("");
      load(gid);
    } else toast(r.error, false);
  };
  return (
    <div className="grid gap-4">
      {data.appeals.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">待处理申诉（{data.appeals.length}）</CardTitle>
            <CardDescription>
              通过后会自动解除对应禁言，并私聊通知申诉人；也可在 QQ 里用 /申诉通过|驳回 &lt;编号&gt; 处理
            </CardDescription>
          </CardHeader>
          <CardPanel className="grid gap-2">
            <Input
              value={note}
              maxLength={200}
              onChange={(e) => setNote(e.target.value)}
              placeholder="处理备注（可选，会随结果一起发给申诉人）"
            />
            {data.appeals.map((a) => (
              <div
                key={a.id}
                className="border-input flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3"
              >
                <div className="text-sm">
                  <b>#{a.id}</b> QQ {a.user_id} · {groupLabel(a.group_id)} · 命中「{a.word}」 ·{" "}
                  {ACTION_LABEL[a.action] ?? a.action}
                  <Badge variant="secondary" className="ml-1">
                    {SOURCE_LABEL[a.source] ?? a.source}
                  </Badge>
                  <p className="text-muted-foreground text-xs">
                    申诉：{a.appeal_text}（{shortTime(a.appeal_ts)}）
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => resolve(a.id, true)}>
                    通过
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => resolve(a.id, false)}>
                    驳回
                  </Button>
                </div>
              </div>
            ))}
          </CardPanel>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">近 30 天处罚排行</CardTitle>
          <CardDescription>被处罚次数最多的成员（含撤回与禁言）</CardDescription>
        </CardHeader>
        <CardPanel>
          {data.offenders.length === 0 ? (
            <p className="text-muted-foreground text-sm">近 30 天没有处罚记录</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-14">#</TableHead>
                  <TableHead>QQ</TableHead>
                  <TableHead>次数</TableHead>
                  <TableHead>涉及群数</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.offenders.map((o, i) => (
                  <TableRow key={o.user_id}>
                    <TableCell>
                      <Badge variant={i === 0 ? "default" : "secondary"}>{i + 1}</Badge>
                    </TableCell>
                    <TableCell className="font-mono">{o.user_id}</TableCell>
                    <TableCell>{o.count}</TableCell>
                    <TableCell>{o.groups}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">处罚记录</CardTitle>
          <CardDescription>
            敏感词命中的撤回 / 禁言记录（不含聊天内容），最多展示最近 100 条
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="pgid">筛选群</Label>
              <select
                id="pgid"
                value={gid}
                onChange={(e) => setGid(e.target.value)}
                className="border-input bg-background flex h-8 min-w-40 items-center rounded-lg border px-2 text-sm"
              >
                <option value="all">全部群</option>
                {data.groups.map((g) => (
                  <option key={g} value={g}>
                    {groupLabel(g)}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {data.records.length === 0 ? (
            <p className="text-muted-foreground text-sm">还没有处罚记录</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>时间</TableHead>
                  <TableHead>群</TableHead>
                  <TableHead>QQ</TableHead>
                  <TableHead>命中词</TableHead>
                  <TableHead>来源</TableHead>
                  <TableHead>处理</TableHead>
                  <TableHead>AI 理由</TableHead>
                  <TableHead>申诉</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.records.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell className="text-muted-foreground text-xs">
                      {shortTime(p.ts)}
                    </TableCell>
                    <TableCell>{groupLabel(p.group_id)}</TableCell>
                    <TableCell className="font-mono">{p.user_id}</TableCell>
                    <TableCell>{p.word}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">{SOURCE_LABEL[p.source] ?? p.source}</Badge>
                    </TableCell>
                    <TableCell>
                      {ACTION_LABEL[p.action] ?? p.action}
                      {p.mute_minutes ? ` ${p.mute_minutes} 分` : ""}
                    </TableCell>
                    <TableCell className="text-muted-foreground max-w-40 truncate text-xs">
                      {p.reason || "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={p.appeal_status === "pending" ? "info" : "secondary"}>
                        {APPEAL_LABEL[p.appeal_status] ?? p.appeal_status}
                      </Badge>
                    </TableCell>
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
