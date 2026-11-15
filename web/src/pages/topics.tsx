import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api, post } from "@/lib/api";

type Category = { key: string; label: string; enabled: boolean; words: string; reply: string };
type TopicsData = { enabled: boolean; categories: Category[]; baseline: string };

export function TopicsTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [d, setD] = useState<TopicsData | null>(null);
  const [form, setForm] = useState<Record<string, { words: string; reply: string }>>({});
  const [test, setTest] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<{ ok: boolean; data: TopicsData }>("/panel/api/topics").then((r) => {
      if (!r.ok) return;
      setD(r.data);
      const f: Record<string, { words: string; reply: string }> = {};
      for (const c of r.data.categories) f[c.key] = { words: c.words, reply: c.reply };
      setForm(f);
    });
  }, []);
  useEffect(load, [load]);
  if (!d) return <Spinner className="m-8" />;

  const save = async (body: Record<string, unknown>) => {
    setBusy(true);
    const r = await post("/panel/api/topics", body);
    setBusy(false);
    if (r.ok) { toast(r.data?.message || "已保存"); load(); }
    else toast(r.error || "保存失败", false);
  };

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">话题规约</CardTitle>
          <CardDescription>
            管的是<b>机器人自己</b>：碰到这几类话题就不参与、并说明原因。不撤回、不禁言、不处罚任何人
            —— 群友之间怎么聊是他们的自由。命中后不会调用模型，所以既不会越界，也不花 API 费用
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="flex items-center justify-between gap-2">
            <div className="grid gap-0.5">
              <Label>总开关</Label>
              <span className="text-muted-foreground text-xs">关掉后所有分类都不生效</span>
            </div>
            <Switch checked={d.enabled} onCheckedChange={(v) => save({ enabled: v })} />
          </div>
        </CardPanel>
      </Card>

      {d.categories.map((c) => {
        const f = form[c.key] ?? { words: "", reply: "" };
        return (
          <Card key={c.key}>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                {c.label}
                <Badge variant={c.enabled ? "default" : "secondary"}>
                  {c.enabled ? "开启" : "关闭"}
                </Badge>
              </CardTitle>
              <CardDescription>
                {c.key === "politics"
                  ? "词表默认留空：具体哪些表述算敏感因群而异，也容易误伤正常讨论，请按自己群的情况填"
                  : "「机场/节点/梯子/加速器」这类词在正常语境很常见，靠下面 ~ 开头的豁免短语兜住误伤"}
              </CardDescription>
            </CardHeader>
            <CardPanel className="grid gap-3">
              <div className="flex items-center justify-between gap-2">
                <Label>启用这一类</Label>
                <Switch checked={c.enabled}
                  onCheckedChange={(v) => save({ categories: [{ key: c.key, enabled: v }] })} />
              </div>

              <div className="grid gap-1.5">
                <Label htmlFor={`w-${c.key}`}>词表（逗号分隔）</Label>
                <Textarea id={`w-${c.key}`} rows={4} value={f.words}
                  placeholder={c.key === "politics" ? "按需填写，用逗号分隔" : ""}
                  onChange={(e) => setForm({ ...form, [c.key]: { ...f, words: e.target.value } })} />
                <span className="text-muted-foreground text-xs">
                  以 ~ 开头的项是豁免短语：命中它就不算，例如 ~机场大巴
                </span>
              </div>

              <div className="grid gap-1.5">
                <Label htmlFor={`r-${c.key}`}>拒答话术</Label>
                <Textarea id={`r-${c.key}`} rows={3} value={f.reply}
                  onChange={(e) => setForm({ ...form, [c.key]: { ...f, reply: e.target.value } })} />
              </div>

              <div className="flex justify-end">
                <Button disabled={busy}
                  onClick={() => save({ categories: [{ key: c.key, ...f }] })}>
                  保存 {c.label}
                </Button>
              </div>
            </CardPanel>
          </Card>
        );
      })}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">测试</CardTitle>
          <CardDescription>输入一句话，看看会不会被拦、会被怎么回复</CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-2">
          <div className="flex gap-2">
            <Input placeholder="例如：有没有好用的翻墙工具" value={test}
              onChange={(e) => setTest(e.target.value)} />
            <Button variant="outline" disabled={busy || !test.trim()} onClick={async () => {
              const r = await post("/panel/api/topics", { test: test.trim() });
              if (r.ok) toast(r.data.message, Boolean(r.data.hit));
              else toast(r.error || "测试失败", false);
            }}>测试</Button>
          </div>
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">提示词底线</CardTitle>
          <CardDescription>
            这段会随每次对话注入系统提示词，兜住规则拦不住的情况（隐晦说法、图片里的文字）
          </CardDescription>
        </CardHeader>
        <CardPanel>
          <pre className="bg-muted/50 rounded-lg p-3 text-xs whitespace-pre-wrap">{d.baseline}</pre>
        </CardPanel>
      </Card>
    </div>
  );
}
