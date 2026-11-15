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

type AdsData = {
  enabled: boolean;
  text: string;
  price: string;
  contact: string;
  partners: number[];
  action: string;
  actions: Record<string, string>;
  notify: boolean;
  cooldown: number;
  welcome_line: string;
  welcome_has_placeholder: boolean;
};

export function AdsTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [d, setD] = useState<AdsData | null>(null);
  const [form, setForm] = useState({ text: "", price: "", contact: "", action: "reply", cooldown: 600, notify: true });
  const [partner, setPartner] = useState("");
  const [busy, setBusy] = useState(false);
  const sel = "border-input bg-background flex h-8 items-center rounded-lg border px-2 text-sm";

  const load = useCallback(() => {
    api<{ ok: boolean; data: AdsData }>("/panel/api/ads").then((r) => {
      if (!r.ok) return;
      setD(r.data);
      setForm({
        text: r.data.text,
        price: r.data.price,
        contact: r.data.contact,
        action: r.data.action,
        cooldown: r.data.cooldown,
        notify: r.data.notify,
      });
    });
  }, []);
  useEffect(load, [load]);
  if (!d) return <Spinner className="m-8" />;

  const save = async (body: Record<string, unknown>, okMsg = "已保存") => {
    setBusy(true);
    const r = await post("/panel/api/ads", body);
    setBusy(false);
    if (r.ok) {
      toast(r.data?.message || okMsg);
      load();
    } else toast(r.error || "保存失败", false);
  };

  const addPartner = () => {
    const uid = Number(partner.trim());
    if (!uid) {
      toast("请输入 QQ 号", false);
      return;
    }
    if (d.partners.includes(uid)) {
      toast("该 QQ 已在合作方名单里", false);
      return;
    }
    setPartner("");
    save({ partners: [...d.partners, uid] });
  };

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">广告招商</CardTitle>
          <CardDescription>
            群里有人发广告时按「{d.actions[d.action] || d.action}」处理；想投广告的人发咨询会直接收到这份招商信息。
            <Badge variant="secondary" className="ml-1">
              {d.enabled ? "开启" : "关闭"}
            </Badge>
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-4">
          <div className="flex items-center justify-between gap-2">
            <div className="grid gap-0.5">
              <Label>招商功能</Label>
              <span className="text-muted-foreground text-xs">
                关闭后 /广告 只提示"已关闭"，群内也不再自动处理广告
              </span>
            </div>
            <Switch checked={d.enabled} onCheckedChange={(v) => save({ enabled: v })} />
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="ad-text">招商说明</Label>
            <Textarea
              id="ad-text"
              rows={7}
              value={form.text}
              onChange={(e) => setForm({ ...form, text: e.target.value })}
            />
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="ad-price">报价单</Label>
            <Textarea
              id="ad-price"
              rows={4}
              value={form.price}
              onChange={(e) => setForm({ ...form, price: e.target.value })}
            />
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="ad-contact">广告对接人 QQ</Label>
              <Input
                id="ad-contact"
                className="w-40"
                value={form.contact}
                onChange={(e) => setForm({ ...form, contact: e.target.value })}
              />
            </div>
            <div className="grid gap-1.5">
              <Label>命中处理</Label>
              <select
                className={sel}
                value={form.action}
                onChange={(e) => setForm({ ...form, action: e.target.value })}
              >
                {Object.entries(d.actions).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="ad-cooldown">提示冷却（秒）</Label>
              <Input
                id="ad-cooldown"
                type="number"
                min={0}
                max={86400}
                className="w-28"
                value={form.cooldown}
                onChange={(e) => setForm({ ...form, cooldown: Number(e.target.value) })}
              />
            </div>
            <div className="flex items-center gap-2 pb-1">
              <Switch
                checked={form.notify}
                onCheckedChange={(v) => setForm({ ...form, notify: v })}
              />
              <Label>命中后私聊通知超管</Label>
            </div>
            <Button
              className="ml-auto"
              disabled={busy}
              onClick={() =>
                save({
                  text: form.text,
                  price: form.price,
                  contact: form.contact,
                  action: form.action,
                  cooldown: form.cooldown,
                  notify: form.notify,
                })
              }
            >
              保存招商配置
            </Button>
          </div>
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">进群欢迎里的广告提示</CardTitle>
          <CardDescription>
            欢迎语里写 <code>{"{ad}"}</code> 就会展开成下面这句话
            {d.welcome_has_placeholder ? "" : "（当前欢迎语里没有这个占位符，加上才会显示）"}
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-2">
          <p className="bg-muted/50 rounded-lg p-3 text-sm">{d.welcome_line}</p>
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">合作方名单</CardTitle>
          <CardDescription>
            名单里的 QQ 发言不会被当成广告撤回，适合已付费或长期合作的推广方
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="w-48"
              placeholder="QQ 号"
              value={partner}
              onChange={(e) => setPartner(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addPartner()}
            />
            <Button variant="outline" onClick={addPartner} disabled={busy}>
              加入合作方
            </Button>
          </div>
          {d.partners.length === 0 ? (
            <p className="text-muted-foreground text-sm">还没有合作方</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {d.partners.map((uid) => (
                <span
                  key={uid}
                  className="border-input flex items-center gap-2 rounded-lg border px-2.5 py-1 text-sm"
                >
                  {uid}
                  <button
                    className="text-muted-foreground hover:text-destructive text-xs"
                    onClick={() => save({ partners: d.partners.filter((p) => p !== uid) })}
                  >
                    移除
                  </button>
                </span>
              ))}
            </div>
          )}
        </CardPanel>
      </Card>
    </div>
  );
}
