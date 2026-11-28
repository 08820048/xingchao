import { useCallback, useEffect, useState } from "react";
import type { ChartData, ChartOptions } from "chart.js";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatChart, axisOptions, tooltipOptions, useChartTheme } from "@/components/stat-chart";
import { api, groupLabel, post } from "@/lib/api";

type Account = { user_id: number; balance: number; updated_at: string | null };
type ShopItem = {
  id: number;
  group_id: number;
  name: string;
  description: string;
  price: number;
  stock: number;
  per_user_limit: number;
  enabled: boolean;
  delivery: "manual" | "title" | "announcement";
  delivery_data: string;
  created_at: string;
  updated_at: string;
};
type Redemption = {
  id: number;
  user_id: number;
  item_id: number;
  item_name: string;
  price: number;
  status: "pending" | "fulfilled" | "rejected";
  user_note: string;
  note: string;
  created_at: string;
  resolved_by: string;
  resolved_at: string | null;
  resolve_note: string;
};
type Ledger = {
  user_id: number;
  delta: number;
  balance_after: number;
  reason: string;
  ref: string | null;
  note: string;
  created_at: string;
};
type PointsData = {
  group_id: number;
  groups: number[];
  overview: { accounts: number; total: number; sign_today: number; ledger: number };
  accounts: Account[];
  ledger: Ledger[];
  settings: Record<string, number>;
  limits: Record<string, [number, number]>;
  display: Record<string, string>;
  shop_items: ShopItem[];
  pending_redemptions: Redemption[];
  shop_stats: {
    days: number;
    since: string;
    orders: number;
    pending: number;
    fulfilled: number;
    rejected: number;
    buyers: number;
    gross: number;
    refunded: number;
    top: { item_id: number; item_name: string; count: number; points: number }[];
    daily: { day: string; count: number; points: number }[];
  };
};

const REASON_LABEL: Record<string, string> = {
  initial: "开户",
  daily: "每日",
  roulette: "轮盘",
  admin: "超管调整",
  migrate: "迁移",
  transfer: "转账",
};

const fmtTime = (s: string | null) => (s ? String(s).replace("T", " ").slice(5, 16) : "—");

export function PointsTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [d, setD] = useState<PointsData | null>(null);
  const [busy, setBusy] = useState(false);
  const [qq, setQq] = useState("");
  const [delta, setDelta] = useState("");
  const [reason, setReason] = useState("");
  const [newItem, setNewItem] = useState({
    name: "", price: "", description: "", stock: "-1",
    per_user_limit: "0", delivery: "manual", delivery_data: "",
  });
  const [resolving, setResolving] = useState<number | null>(null);
  const palette = useChartTheme();
  const sel = "border-input bg-background flex h-8 items-center rounded-lg border px-2 text-sm";

  const load = useCallback((groupId?: number) => {
    const qs = groupId ? `?group_id=${groupId}` : "";
    api<{ ok: boolean; data: PointsData }>(`/panel/api/points${qs}`).then((r) => {
      if (r.ok) setD(r.data);
    });
  }, []);
  useEffect(() => { load(); }, [load]);
  if (!d) return <Spinner className="m-8" />;

  const dailyMap = new Map(d.shop_stats.daily.map((p) => [p.day, p]));
  const today = new Date();
  const shopDaily = Array.from({ length: d.shop_stats.days }, (_, i) => {
    const dt = new Date(today);
    dt.setDate(today.getDate() - (d.shop_stats.days - 1 - i));
    const key = dt.toLocaleDateString("sv-SE");
    const row = dailyMap.get(key);
    return { day: key, count: row?.count ?? 0, points: row?.points ?? 0 };
  });
  const shopTrendEmpty = shopDaily.every((p) => p.count === 0 && p.points === 0);
  const shopTrendData: ChartData = {
    labels: shopDaily.map((p) => p.day.slice(5)),
    datasets: [
      {
        type: "bar",
        label: "兑换单",
        data: shopDaily.map((p) => p.count),
        backgroundColor: palette.bar,
        hoverBackgroundColor: palette.inkSoft,
        borderRadius: 4,
        maxBarThickness: 26,
        yAxisID: "y",
      },
      {
        type: "line",
        label: "消耗积分",
        data: shopDaily.map((p) => p.points),
        borderColor: palette.ink,
        backgroundColor: palette.ink,
        pointBackgroundColor: palette.ink,
        pointRadius: 2,
        pointHoverRadius: 4,
        borderWidth: 2,
        tension: 0.35,
        yAxisID: "y1",
      },
    ],
  };
  const shopTrendOptions: ChartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        ...tooltipOptions(palette),
        callbacks: {
          label: (ctx: any) =>
            `${ctx.dataset.label}: ${ctx.parsed.y} ${ctx.dataset.label === "兑换单" ? "张" : "积分"}`,
        },
      },
    },
    scales: {
      x: {
        grid: { display: false },
        border: { display: false },
        ticks: { color: palette.tick, maxRotation: 0, autoSkipPadding: 12 },
      },
      y: {
        beginAtZero: true,
        ...axisOptions(palette),
        ticks: { color: palette.tick, padding: 6, precision: 0 },
      },
      y1: {
        beginAtZero: true,
        position: "right",
        grid: { display: false },
        border: { display: false },
        ticks: { color: palette.tick, padding: 6, precision: 0 },
      },
    },
  };

  const saveConfig = async (key: string, value: number) => {
    if (!Number.isFinite(value) || value === d.settings[key]) return;
    setBusy(true);
    const r = await post("/panel/api/points", { action: "config", updates: { [key]: value } });
    setBusy(false);
    if (r.ok) { toast(r.data?.message || "已保存"); load(d.group_id); }
    else toast(r.error || "保存失败", false);
  };

  const adjust = async () => {
    const userId = Number(qq);
    const amount = Number(delta);
    if (!Number.isInteger(userId) || userId <= 0) { toast("QQ 号必须是正整数", false); return; }
    if (!Number.isInteger(amount) || amount === 0) { toast("调整值必须是非零整数", false); return; }
    if (!reason.trim()) { toast("请填写调整原因，会写入流水", false); return; }
    setBusy(true);
    const r = await post("/panel/api/points", {
      action: "adjust",
      group_id: d.group_id,
      user_id: userId,
      delta: amount,
      reason: reason.trim(),
    });
    setBusy(false);
    if (r.ok) {
      toast(r.data?.message || "已调整");
      setDelta("");
      setReason("");
      load(d.group_id);
    } else toast(r.error || "调整失败", false);
  };

  const addShopItem = async () => {
    const price = Number(newItem.price);
    if (!newItem.name.trim()) { toast("商品名称不能为空", false); return; }
    if (!Number.isInteger(price) || price <= 0) { toast("价格必须是正整数", false); return; }
    setBusy(true);
    const r = await post("/panel/api/points", {
      action: "shop_add", group_id: d.group_id,
      name: newItem.name.trim(), price,
      description: newItem.description.trim(),
      stock: Number(newItem.stock), per_user_limit: Number(newItem.per_user_limit),
      delivery: newItem.delivery, delivery_data: newItem.delivery_data.trim(),
    });
    setBusy(false);
    if (r.ok) {
      toast(r.data?.message || "已上架");
      setNewItem({ name: "", price: "", description: "", stock: "-1", per_user_limit: "0", delivery: "manual", delivery_data: "" });
      load(d.group_id);
    } else toast(r.error || "上架失败", false);
  };

  const updateShopItem = async (item: ShopItem, fields: Record<string, unknown>) => {
    setBusy(true);
    const r = await post("/panel/api/points", {
      action: "shop_update", group_id: d.group_id, item_id: item.id, fields,
    });
    setBusy(false);
    if (r.ok) { toast("已更新"); load(d.group_id); }
    else toast(r.error || "更新失败", false);
  };

  const resolveRedemption = async (id: number, accept: boolean) => {
    setResolving(id);
    const r = await post("/panel/api/points", {
      action: "shop_resolve", group_id: d.group_id, redemption_id: id, accept,
    });
    setResolving(null);
    if (r.ok) { toast(r.data?.message || "已处理"); load(d.group_id); }
    else toast(r.error || "处理失败", false);
  };

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">本地按群积分</CardTitle>
          <CardDescription>
            账户、流水和签到都按群隔离；余额是缓存，任何加减分都会写一条只追加的流水。
            /积分、/商店、/轮盘 共用同一份本地积分。
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <Label>群</Label>
            <select className={sel} value={d.group_id} onChange={(e) => load(Number(e.target.value))}>
              {d.groups.length ? d.groups.map((g) => (
                <option key={g} value={g}>{groupLabel(g)}</option>
              )) : <option value={0}>没有白名单群</option>}
            </select>
            <Badge variant="secondary">账户 {d.overview.accounts}</Badge>
            <Badge variant="secondary">总积分 {d.overview.total}</Badge>
            <Badge variant="secondary">今日签到 {d.overview.sign_today}</Badge>
            <Badge variant="secondary">流水 {d.overview.ledger}</Badge>
            <Button variant="outline" size="sm" onClick={() => load(d.group_id)} disabled={busy}>
              刷新
            </Button>
            <Button variant="outline" size="sm" disabled={!d.group_id}
              onClick={() => window.open(`/panel/api/points/export?group_id=${d.group_id}&kind=accounts`)}>
              导出账户
            </Button>
            <Button variant="outline" size="sm" disabled={!d.group_id}
              onClick={() => window.open(`/panel/api/points/export?group_id=${d.group_id}&kind=ledger`)}>
              导出流水
            </Button>
            <Button variant="outline" size="sm" disabled={!d.group_id}
              onClick={() => window.open(`/panel/api/points/export?group_id=${d.group_id}&kind=redemptions`)}>
              导出兑换单
            </Button>
            <Button variant="outline" size="sm" disabled={!d.group_id}
              onClick={() => window.open(`/panel/api/points/export?group_id=${d.group_id}&kind=items`)}>
              导出商品
            </Button>
          </div>
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">积分配置</CardTitle>
          <CardDescription>修改后立即生效；配置是全局默认值，余额仍按群隔离</CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(d.settings).map(([key, value]) => {
            const [low, high] = d.limits[key] || [0, 0];
            return (
              <div key={key} className="flex flex-col gap-1.5">
                <Label htmlFor={`pt-${key}`}>{d.display[key] || key}（{key}）</Label>
                <Input
                  id={`pt-${key}`}
                  type="number"
                  min={low}
                  max={high}
                  defaultValue={value}
                  key={`${key}-${value}`}
                  disabled={busy}
                  onBlur={(e) => {
                    const raw = e.target.value.trim();
                    if (!raw) { e.target.value = String(value); return; }
                    saveConfig(key, Math.trunc(Number(raw)));
                  }}
                />
                <span className="text-muted-foreground text-[11px]">范围 {low}–{high}</span>
              </div>
            );
          })}
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">手动调分</CardTitle>
          <CardDescription>用于活动奖励、误扣补偿等；必须填原因，会写一条 admin 流水</CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3 sm:grid-cols-[160px_160px_1fr_auto] sm:items-end">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="pt-qq">QQ 号</Label>
            <Input id="pt-qq" value={qq} onChange={(e) => setQq(e.target.value)} placeholder="123456" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="pt-delta">调整值</Label>
            <Input id="pt-delta" value={delta} onChange={(e) => setDelta(e.target.value)} placeholder="+50 或 -20" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="pt-reason">原因</Label>
            <Input id="pt-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="活动奖励 / 误扣补偿" />
          </div>
          <Button onClick={adjust} disabled={busy}>调整</Button>
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">积分商店</CardTitle>
          <CardDescription>
            用户用 /购买 下单后立即扣分，订单进入待处理；完成或拒绝（退款并恢复库存）。
            自动头衔需要机器人是群主，自动公告需要机器人是管理员。
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="flex flex-col gap-1.5">
              <Label>名称</Label>
              <Input value={newItem.name} onChange={(e) => setNewItem({ ...newItem, name: e.target.value })} placeholder="自定义头衔" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>价格</Label>
              <Input type="number" value={newItem.price} onChange={(e) => setNewItem({ ...newItem, price: e.target.value })} placeholder="100" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>库存（-1 不限）</Label>
              <Input type="number" value={newItem.stock} onChange={(e) => setNewItem({ ...newItem, stock: e.target.value })} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>限购（0 不限）</Label>
              <Input type="number" value={newItem.per_user_limit} onChange={(e) => setNewItem({ ...newItem, per_user_limit: e.target.value })} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>交付方式</Label>
              <select className={sel} value={newItem.delivery}
                onChange={(e) => setNewItem({ ...newItem, delivery: e.target.value })}>
                <option value="manual">人工交付</option>
                <option value="title">自动头衔</option>
                <option value="announcement">自动公告</option>
              </select>
            </div>
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <Label>交付内容（头衔文字/公告内容）</Label>
              <Input value={newItem.delivery_data} onChange={(e) => setNewItem({ ...newItem, delivery_data: e.target.value })} placeholder="留空则用商品名/购买备注" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>描述</Label>
              <Input value={newItem.description} onChange={(e) => setNewItem({ ...newItem, description: e.target.value })} />
            </div>
          </div>
          <div><Button onClick={addShopItem} disabled={busy}>上架商品</Button></div>

          {d.shop_items.length === 0 ? (
            <p className="text-muted-foreground text-sm">这个群还没有商品。</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-14">#</TableHead>
                  <TableHead>名称</TableHead>
                  <TableHead className="w-24">价格</TableHead>
                  <TableHead className="w-24">库存</TableHead>
                  <TableHead className="w-24">限购</TableHead>
                  <TableHead className="w-32">交付</TableHead>
                  <TableHead className="w-48">交付内容</TableHead>
                  <TableHead className="text-right">状态</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {d.shop_items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="text-muted-foreground">{item.id}</TableCell>
                    <TableCell>
                      <Input key={`name-${item.id}-${item.updated_at}`} defaultValue={item.name}
                        className="h-7 w-48 font-medium" disabled={busy}
                        onBlur={(e) => {
                          const value = e.target.value.trim();
                          if (!value) { e.target.value = item.name; return; }
                          if (value !== item.name) updateShopItem(item, { name: value });
                        }} />
                      <Input key={`desc-${item.id}-${item.updated_at}`} defaultValue={item.description}
                        className="mt-1 h-6 w-48 text-xs" placeholder="描述" disabled={busy}
                        onBlur={(e) => {
                          const value = e.target.value.trim();
                          if (value !== item.description) updateShopItem(item, { description: value });
                        }} />
                    </TableCell>
                    <TableCell>
                      <Input key={`price-${item.id}-${item.updated_at}`} type="number" defaultValue={item.price}
                        className="h-7 w-20 text-right" disabled={busy}
                        onBlur={(e) => {
                          const raw = e.target.value.trim();
                          const value = Math.trunc(Number(raw));
                          if (!raw) { e.target.value = String(item.price); return; }
                          if (value > 0 && value !== item.price) updateShopItem(item, { price: value });
                        }} />
                    </TableCell>
                    <TableCell>
                      <Input key={`stock-${item.id}-${item.updated_at}`} type="number" defaultValue={item.stock}
                        className="h-7 w-20 text-right" disabled={busy}
                        onBlur={(e) => {
                          const raw = e.target.value.trim();
                          const value = Math.trunc(Number(raw));
                          if (!raw) { e.target.value = String(item.stock); return; }
                          if (value >= -1 && value !== item.stock) updateShopItem(item, { stock: value });
                        }} />
                    </TableCell>
                    <TableCell>
                      <Input key={`limit-${item.id}-${item.updated_at}`} type="number" defaultValue={item.per_user_limit}
                        className="h-7 w-20 text-right" disabled={busy}
                        onBlur={(e) => {
                          const raw = e.target.value.trim();
                          const value = Math.trunc(Number(raw));
                          if (!raw) { e.target.value = String(item.per_user_limit); return; }
                          if (value >= 0 && value !== item.per_user_limit) updateShopItem(item, { per_user_limit: value });
                        }} />
                    </TableCell>
                    <TableCell>
                      <select className={sel} value={item.delivery} disabled={busy}
                        onChange={(e) => updateShopItem(item, { delivery: e.target.value })}>
                        <option value="manual">人工</option>
                        <option value="title">自动头衔</option>
                        <option value="announcement">自动公告</option>
                      </select>
                    </TableCell>
                    <TableCell>
                      <Input key={`data-${item.id}-${item.updated_at}`} defaultValue={item.delivery_data}
                        className="h-7 w-44" placeholder="头衔文字/公告内容" disabled={busy}
                        onBlur={(e) => {
                          const value = e.target.value.trim();
                          if (value !== item.delivery_data) updateShopItem(item, { delivery_data: value });
                        }} />
                    </TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant={item.enabled ? "outline" : "secondary"}
                        disabled={busy}
                        onClick={() => updateShopItem(item, { enabled: !item.enabled })}>
                        {item.enabled ? "下架" : "上架"}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          <div className="grid gap-2">
            <div className="text-sm font-medium">待处理兑换单</div>
            {d.pending_redemptions.length === 0 ? (
              <p className="text-muted-foreground text-sm">没有待处理订单。</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-14">#</TableHead>
                    <TableHead>用户</TableHead>
                    <TableHead>商品</TableHead>
                    <TableHead className="text-right">积分</TableHead>
                    <TableHead>备注</TableHead>
                    <TableHead className="text-right">处理</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {d.pending_redemptions.map((row) => (
                    <TableRow key={row.id}>
                      <TableCell className="text-muted-foreground">{row.id}</TableCell>
                      <TableCell className="font-mono">{row.user_id}</TableCell>
                      <TableCell>{row.item_name}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.price}</TableCell>
                      <TableCell className="text-xs">{row.user_note || "—"}</TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button size="sm" disabled={resolving === row.id}
                            onClick={() => resolveRedemption(row.id, true)}>完成</Button>
                          <Button size="sm" variant="outline" disabled={resolving === row.id}
                            onClick={() => resolveRedemption(row.id, false)}>拒绝退款</Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">商店统计（近 {d.shop_stats.days} 天）</CardTitle>
          <CardDescription>只统计兑换单；拒绝的订单计入退款，不计入净消耗</CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">订单 {d.shop_stats.orders}</Badge>
            <Badge variant="secondary">待处理 {d.shop_stats.pending}</Badge>
            <Badge variant="secondary">已完成 {d.shop_stats.fulfilled}</Badge>
            <Badge variant="secondary">已拒绝 {d.shop_stats.rejected}</Badge>
            <Badge variant="secondary">买家 {d.shop_stats.buyers}</Badge>
            <Badge variant="secondary">净消耗 {d.shop_stats.gross} 积分</Badge>
            <Badge variant="secondary">已退款 {d.shop_stats.refunded} 积分</Badge>
          </div>
          {d.shop_stats.top.length === 0 ? (
            <p className="text-muted-foreground text-sm">近 {d.shop_stats.days} 天还没有成功兑换。</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-14">#</TableHead>
                  <TableHead>商品</TableHead>
                  <TableHead className="text-right">兑换次数</TableHead>
                  <TableHead className="text-right">消耗积分</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {d.shop_stats.top.map((item, i) => (
                  <TableRow key={`${item.item_id}-${i}`}>
                    <TableCell className="text-muted-foreground">{i + 1}</TableCell>
                    <TableCell>{item.item_name}</TableCell>
                    <TableCell className="text-right tabular-nums">{item.count}</TableCell>
                    <TableCell className="text-right tabular-nums">{item.points}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">商店趋势（近 {d.shop_stats.days} 天）</CardTitle>
          <CardDescription>柱状为兑换单数，折线为消耗积分；仅统计非拒绝订单</CardDescription>
        </CardHeader>
        <CardPanel>
          <StatChart
            type="bar"
            data={shopTrendData}
            options={shopTrendOptions}
            height={240}
            label={`近 ${d.shop_stats.days} 天商店兑换与消耗`}
            empty={shopTrendEmpty}
            emptyText="该区间还没有成功兑换"
          />
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">账户（前 200）</CardTitle>
          <CardDescription>按余额降序；余额为负表示欠分，通常是手动调分造成</CardDescription>
        </CardHeader>
        <CardPanel>
          {d.accounts.length === 0 ? (
            <p className="text-muted-foreground text-sm">这个群还没有积分账户。</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-14">#</TableHead>
                  <TableHead>QQ</TableHead>
                  <TableHead className="text-right">积分</TableHead>
                  <TableHead className="text-right">最后变动</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {d.accounts.map((a, i) => (
                  <TableRow key={a.user_id}>
                    <TableCell className="text-muted-foreground">{i + 1}</TableCell>
                    <TableCell className="font-mono">{a.user_id}</TableCell>
                    <TableCell className="text-right tabular-nums">{a.balance}</TableCell>
                    <TableCell className="text-muted-foreground text-right text-xs">{fmtTime(a.updated_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">最近流水</CardTitle>
          <CardDescription>最近 50 条，含签到、轮盘、手动调整；点数是变动后的余额</CardDescription>
        </CardHeader>
        <CardPanel>
          {d.ledger.length === 0 ? (
            <p className="text-muted-foreground text-sm">还没有积分流水。</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-28">时间</TableHead>
                  <TableHead>QQ</TableHead>
                  <TableHead className="text-right">变动</TableHead>
                  <TableHead className="text-right">余额</TableHead>
                  <TableHead>原因</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {d.ledger.map((row, i) => (
                  <TableRow key={`${row.created_at}-${row.user_id}-${i}`}>
                    <TableCell className="text-muted-foreground text-xs">{fmtTime(row.created_at)}</TableCell>
                    <TableCell className="font-mono">{row.user_id}</TableCell>
                    <TableCell className={`text-right tabular-nums ${row.delta > 0 ? "text-emerald-600" : "text-rose-600"}`}>
                      {row.delta > 0 ? "+" : ""}{row.delta}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{row.balance_after}</TableCell>
                    <TableCell className="text-xs">
                      <span>{REASON_LABEL[row.reason] || row.reason}</span>
                      {row.note && <span className="text-muted-foreground"> · {row.note}</span>}
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
