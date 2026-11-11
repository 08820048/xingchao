import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api, post } from "@/lib/api";

type ReplyItem = {
  id: string;
  enabled: boolean;
  match: "exact" | "contains";
  pattern: string;
  reply: string;
  cooldown: number;
};

export function RepliesTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
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

export function WelcomeCard({ toast }: { toast: (t: string, ok?: boolean) => void }) {
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
