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

type Entry = {
  id: number;
  group_id: number;
  question: string;
  answer: string;
  keywords: string;
  hits: number;
  source: string;
};

type KbData = {
  entries: Entry[];
  groups: number[];
  enabled: boolean;
  auto_answer: boolean;
  require_question: boolean;
  cooldown: number;
  min_score: number;
  suggest_min: number;
  suggestions?: { question: string; count: number }[];
};

export function KnowledgeTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [data, setData] = useState<KbData | null>(null);
  const [gid, setGid] = useState<number | null>(null);
  const [form, setForm] = useState({ question: "", answer: "", keywords: "" });
  const [test, setTest] = useState("");
  const [busy, setBusy] = useState(false);
  const sel = "border-input bg-background flex h-8 items-center rounded-lg border px-2 text-sm";

  const load = useCallback((g: number | null) => {
    const q = g ? `?group_id=${g}` : "";
    api<{ ok: boolean; data: KbData }>(`/panel/api/kb${q}`).then((r) => {
      if (!r.ok) return;
      setData(r.data);
      setGid(r.data.entries[0]?.group_id ?? r.data.groups[0] ?? null);
    });
  }, []);
  useEffect(() => load(null), [load]);
  if (!data) return <Spinner className="m-8" />;

  const save = async (body: Record<string, unknown>, okMsg = "已保存") => {
    setBusy(true);
    const r = await post("/panel/api/kb", body);
    setBusy(false);
    if (r.ok) { toast(r.data?.message || okMsg); load(gid); }
    else toast(r.error || "操作失败", false);
    return r;
  };

  const add = async () => {
    if (!gid) { toast("先选择一个群", false); return; }
    if (!form.question.trim() || !form.answer.trim()) {
      toast("问题和答案都要填", false);
      return;
    }
    const r = await save({ action: "add", group_id: gid, ...form });
    if (r?.ok) setForm({ question: "", answer: "", keywords: "" });
  };

  const adopt = async (question: string) => {
    setForm({ question, answer: "", keywords: "" });
    toast("已填进左侧表单，补上答案后点添加");
  };

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">群知识库</CardTitle>
          <CardDescription>
            把群里反复被问的事（群规、内测申请、报名入口）存成条目。有人问到就自动回答，
            不用每次重打一遍。按群隔离，命中次数可帮你判断哪些才是真问题
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <Label>当前群</Label>
              <select
                className={sel}
                value={gid ?? ""}
                onChange={(e) => { const g = +e.target.value; setGid(g); load(g); }}
              >
                {data.groups.map((g) => (
                  <option key={g} value={g}>{groupLabel(g)}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={data.enabled} onCheckedChange={(v) => save({ enabled: v })} />
              <Label>启用</Label>
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={data.auto_answer} onCheckedChange={(v) => save({ auto_answer: v })} />
              <Label>命中自动回答</Label>
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={data.require_question} onCheckedChange={(v) => save({ require_question: v })} />
              <Label>只回答像提问的消息</Label>
            </div>
          </div>

          <div className="grid gap-2 sm:grid-cols-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="kb-cd">同群冷却（秒）</Label>
              <Input id="kb-cd" type="number" defaultValue={data.cooldown}
                onBlur={(e) => save({ cooldown: +e.target.value || 0 })} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="kb-ms">命中阈值</Label>
              <Input id="kb-ms" type="number" step="0.1" defaultValue={data.min_score}
                onBlur={(e) => save({ min_score: +e.target.value || 1.8 })} />
              <span className="text-muted-foreground text-xs">调高更严格，误答多就往上调</span>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="kb-sm">候选出现次数</Label>
              <Input id="kb-sm" type="number" defaultValue={data.suggest_min}
                onBlur={(e) => save({ suggest_min: +e.target.value || 2 })} />
            </div>
          </div>
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">新增条目</CardTitle>
          <CardDescription>
            关键词是同义说法，空格分隔：有人问「开通资格在哪」也能命中「怎么申请内测」
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="kb-q">问题</Label>
            <Input id="kb-q" placeholder="怎么申请内测" value={form.question}
              onChange={(e) => setForm({ ...form, question: e.target.value })} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="kb-a">答案</Label>
            <Textarea id="kb-a" rows={3} placeholder="私聊管理员报 QQ 和用途"
              value={form.answer} onChange={(e) => setForm({ ...form, answer: e.target.value })} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="kb-k">关键词（可选）</Label>
            <Input id="kb-k" placeholder="内测 申请 开通资格" value={form.keywords}
              onChange={(e) => setForm({ ...form, keywords: e.target.value })} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={busy} onClick={add}>添加</Button>
            <div className="ml-auto flex items-center gap-2">
              <Input className="w-56" placeholder="试一句话看会不会命中" value={test}
                onChange={(e) => setTest(e.target.value)} />
              <Button variant="outline" disabled={busy || !test.trim()} onClick={async () => {
                const r = await save({ action: "test", group_id: gid, query: test.trim() }, "已测试");
                if (r?.ok) {
                  const e = r.data?.entry as Entry | null;
                  toast(e ? `命中 #${e.id}：${e.question}` : "没有命中");
                }
              }}>测试</Button>
            </div>
          </div>
        </CardPanel>
      </Card>

      {data.suggestions && data.suggestions.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">候选问题</CardTitle>
            <CardDescription>
              最近几天被反复问、但知识库里还没有的问题（点一下就填进上面的表单）
            </CardDescription>
          </CardHeader>
          <CardPanel className="grid gap-2">
            {data.suggestions.map((s) => (
              <div key={s.question} className="border-input flex items-center gap-2 rounded-lg border p-2.5">
                <span className="text-sm">{s.question}</span>
                <Badge variant="secondary">被问 {s.count} 次</Badge>
                <Button size="sm" variant="outline" className="ml-auto" onClick={() => adopt(s.question)}>
                  采纳
                </Button>
              </div>
            ))}
          </CardPanel>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">已收录 {data.entries.length} 条</CardTitle>
        </CardHeader>
        <CardPanel className="grid gap-2">
          {data.entries.length === 0 && (
            <p className="text-muted-foreground text-sm">这个群还没有知识条目</p>
          )}
          {data.entries.map((e) => (
            <div key={e.id} className="border-input grid gap-2 rounded-lg border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge>#{e.id}</Badge>
                <span className="text-sm font-medium">{e.question}</span>
                <Badge variant="secondary">命中 {e.hits}</Badge>
                {e.keywords && (
                  <span className="text-muted-foreground text-xs">关键词：{e.keywords}</span>
                )}
                <div className="ml-auto flex gap-2">
                  <Button size="sm" variant="outline"
                    onClick={() => setForm({ question: e.question, answer: e.answer, keywords: e.keywords })}>
                    复制到表单
                  </Button>
                  <Button size="sm" variant="outline"
                    onClick={() => save({ action: "delete", id: e.id })}>
                    删除
                  </Button>
                </div>
              </div>
              <p className="text-muted-foreground text-sm whitespace-pre-wrap">{e.answer}</p>
            </div>
          ))}
        </CardPanel>
      </Card>
    </div>
  );
}
