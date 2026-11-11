import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, groupLabel, post } from "@/lib/api";

type VisionConfig = {
  enabled: boolean;
  mute_minutes: number;
  notify: boolean;
  cooldown: number;
  daily_limit: number;
  qrcode_enabled: boolean;
  qrcode_types: string;
  qrcode_mute_minutes: number;
  qrcode_notify: boolean;
};

const QR_TYPES: [string, string][] = [
  ["wechat_group", "微信群"],
  ["wechat_personal", "微信个人号"],
  ["wechat_work", "企业微信"],
  ["qq_group", "QQ群"],
  ["qq_personal", "QQ名片"],
  ["url", "普通网址"],
];

export function VisionCard({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [cfg, setCfg] = useState<VisionConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const load = useCallback(() => {
    api<{ ok: boolean; data: VisionConfig }>("/panel/api/visionguard").then(
      (r) => r.ok && setCfg(r.data),
    );
  }, []);
  useEffect(() => load(), [load]);
  if (!cfg) return <Spinner className="m-8" />;
  const save = async (patch: Record<string, unknown>) => {
    setSaving(true);
    const r = await post("/panel/api/visionguard", patch);
    setSaving(false);
    if (r.ok) {
      toast(r.data.message);
      load();
    } else toast(r.error || "保存失败", false);
  };
  const types = new Set(cfg.qrcode_types.split(",").filter(Boolean));
  const toggleType = (t: string) => {
    const next = new Set(types);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    save({ qrcode_types: [...next] });
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">图片与二维码审核</CardTitle>
        <CardDescription>
          二维码为本地解码识别（不耗 AI，AI 不可用时也能拦截）；违规图片走 AI 视觉模型。
          命中后撤回、按下方配置禁言，并私聊通知超管
        </CardDescription>
      </CardHeader>
      <CardPanel className="grid gap-3">
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">二维码引流风控</p>
              <p className="text-muted-foreground text-xs">识别微信群 / QQ 群等引流二维码</p>
            </div>
            <Switch
              checked={cfg.qrcode_enabled}
              onCheckedChange={(v) => save({ qrcode_enabled: v })}
            />
          </div>
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">二维码命中通知超管</p>
              <p className="text-muted-foreground text-xs">私聊推送解码内容与处理结果</p>
            </div>
            <Switch
              checked={cfg.qrcode_notify}
              onCheckedChange={(v) => save({ qrcode_notify: v })}
            />
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>拦截的二维码类型（点击切换，浅色为不拦截）</Label>
          <div className="flex flex-wrap gap-1.5">
            {QR_TYPES.map(([id, label]) => (
              <Button
                key={id}
                size="sm"
                variant={types.has(id) ? "default" : "outline"}
                onClick={() => toggleType(id)}
              >
                {label}
              </Button>
            ))}
          </div>
        </div>

        <div className="grid gap-2 sm:grid-cols-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="qr-mute">二维码禁言（分钟）</Label>
            <Input
              id="qr-mute"
              type="number"
              value={cfg.qrcode_mute_minutes}
              onChange={(e) => setCfg({ ...cfg, qrcode_mute_minutes: +e.target.value || 0 })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="v-mute">图片禁言（分钟）</Label>
            <Input
              id="v-mute"
              type="number"
              value={cfg.mute_minutes}
              onChange={(e) => setCfg({ ...cfg, mute_minutes: +e.target.value || 0 })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="v-cd">每人冷却（秒）</Label>
            <Input
              id="v-cd"
              type="number"
              value={cfg.cooldown}
              onChange={(e) => setCfg({ ...cfg, cooldown: +e.target.value || 0 })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="v-dl">每群日限（次）</Label>
            <Input
              id="v-dl"
              type="number"
              value={cfg.daily_limit}
              onChange={(e) => setCfg({ ...cfg, daily_limit: +e.target.value || 0 })}
            />
          </div>
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">图片命中通知超管</p>
              <p className="text-muted-foreground text-xs">违规图片的私聊告警</p>
            </div>
            <Switch checked={cfg.notify} onCheckedChange={(v) => save({ notify: v })} />
          </div>
          <div className="flex items-end justify-end">
            <Button
              disabled={saving}
              onClick={() =>
                save({
                  mute_minutes: cfg.mute_minutes,
                  cooldown: cfg.cooldown,
                  daily_limit: cfg.daily_limit,
                  qrcode_mute_minutes: cfg.qrcode_mute_minutes,
                })
              }
            >
              {saving && <Spinner />}保存数值设置
            </Button>
          </div>
        </div>
      </CardPanel>
    </Card>
  );
}

type SensitiveConfig = {
  groups: number[];
  override: Record<string, unknown> | null;
  enabled: boolean;
  words: string;
  mute_minutes: number;
  notify: boolean;
  review: boolean;
  fallback: string;
  variant: string;
};

export function SensitiveTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [cfg, setCfg] = useState<SensitiveConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [sel, setSel] = useState<string>("global");
  const load = useCallback((g: string) => {
    const q = g !== "global" ? `?group_id=${g}` : "";
    api<{ ok: boolean; data: SensitiveConfig }>(`/panel/api/sensitive${q}`).then((r) => {
      if (r.ok) setCfg(r.data);
    });
  }, []);
  useEffect(() => load(sel), [sel, load]);
  if (!cfg) return <Spinner className="m-8" />;
  const isGroup = sel !== "global";

  const save = async (patch: Record<string, unknown>) => {
    setSaving(true);
    const r = await post("/panel/api/sensitive", {
      ...patch,
      group_id: isGroup ? parseInt(sel) : undefined,
    });
    setSaving(false);
    if (r.ok) {
      toast(r.data.message);
      load(sel);
    } else toast(r.error || "保存失败", false);
  };

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">配置对象</CardTitle>
          <CardDescription>未单独设置的群自动继承全局默认</CardDescription>
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
                const r = await post("/panel/api/sensitive", {
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
          <CardTitle className="text-base">敏感词监控与撤回</CardTitle>
          <CardDescription>
            白名单群消息命中关键词即自动撤回（可追加禁言），防广告与敏感信息；
            命中后不再触发 AI/关键词回复
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">功能开关</p>
              <p className="text-muted-foreground text-xs">按群独立生效</p>
            </div>
            <Switch checked={cfg.enabled} onCheckedChange={(v) => save({ enabled: v })} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="words">敏感词库（逗号分隔，大小写不敏感）</Label>
            <Textarea
              id="words"
              rows={3}
              value={cfg.words}
              onChange={(e) => setCfg({ ...cfg, words: e.target.value })}
              placeholder="例如：加微信,低价代刷,博彩,代开发票,~机场大巴"
            />
            <p className="text-muted-foreground text-xs">
              纯英文/数字词按词边界匹配（PIA 不会命中 Olympiad）；以 ~ 开头的是豁免短语，
              匹配前会从消息中剔除（如 ~机场大巴，避免「机场」误伤正常聊天）
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mm">命中后禁言（分钟，0 = 不禁言）</Label>
            <Input
              id="mm"
              type="number"
              className="w-48"
              value={cfg.mute_minutes}
              onChange={(e) => setCfg({ ...cfg, mute_minutes: +e.target.value || 0 })}
            />
          </div>
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">命中后通知超管</p>
              <p className="text-muted-foreground text-xs">私聊推送命中详情与处理结果</p>
            </div>
            <Switch checked={cfg.notify} onCheckedChange={(v) => save({ notify: v })} />
          </div>
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">AI 语境复核</p>
              <p className="text-muted-foreground text-xs">
                命中候选后由 AI 判断是推广还是正常讨论；确认违规才撤回，正常讨论直接放行，减少误杀
              </p>
            </div>
            <Switch checked={cfg.review} onCheckedChange={(v) => save({ review: v })} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="sfb">AI 复核不可用时（未配置 / 调用失败）</Label>
            <select
              id="sfb"
              value={cfg.fallback}
              onChange={(e) => save({ fallback: e.target.value })}
              className="border-input bg-background flex h-8 w-full items-center rounded-lg border px-2 text-sm sm:w-72"
            >
              <option value="notify">仅通知超管，不自动撤回</option>
              <option value="recall">照常自动撤回</option>
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="svl">变体匹配强度</Label>
            <select
              id="svl"
              value={cfg.variant}
              onChange={(e) => save({ variant: e.target.value })}
              className="border-input bg-background flex h-8 w-full items-center rounded-lg border px-2 text-sm sm:w-72"
            >
              <option value="basic">原样匹配（最快，可被变体绕过）</option>
              <option value="normal">归一化：全角 / 空格标点 / 繁体</option>
              <option value="pinyin">归一化 + 拼音（拦「威信」「weixin」）</option>
            </select>
          </div>
          <Button disabled={saving} onClick={() => save({
            words: cfg.words, mute_minutes: cfg.mute_minutes,
          })}>
            {saving && <Spinner />}保存词库与禁言设置（{isGroup ? `群 ${sel}` : "全局默认"}）
          </Button>
        </CardPanel>
      </Card>
      <WordStatsCard />
    </div>
  );
}

type WordStat = {
  word: string;
  candidates: number;
  released: number;
  punished: number;
  fallback: number;
  appeal_accepted: number;
  appeals: number;
  suggestion: string;
};

function WordStatsCard() {
  const [rows, setRows] = useState<WordStat[] | null>(null);
  useEffect(() => {
    api<{ ok: boolean; data: { words: WordStat[] } }>("/panel/api/sensitive/stats").then((r) => {
      if (r.ok) setRows(r.data.words);
    });
  }, []);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">词条命中统计（排查误判）</CardTitle>
        <CardDescription>
          放行 = AI 复核判定为正常讨论；处罚 = 确认违规；申诉通过次数来自违规记录。
          放行占比高或有申诉通过的词，建议改具体或加豁免短语（~前缀）
        </CardDescription>
      </CardHeader>
      <CardPanel>
        {!rows ? (
          <Spinner />
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">暂无命中数据（从本版本部署后开始统计）</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>词</TableHead>
                <TableHead>命中</TableHead>
                <TableHead>放行</TableHead>
                <TableHead>处罚</TableHead>
                <TableHead>未复核</TableHead>
                <TableHead>申诉通过</TableHead>
                <TableHead>建议</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.word}>
                  <TableCell className="font-mono">{r.word}</TableCell>
                  <TableCell>{r.candidates}</TableCell>
                  <TableCell>{r.released}</TableCell>
                  <TableCell>{r.punished}</TableCell>
                  <TableCell>{r.fallback}</TableCell>
                  <TableCell>{r.appeal_accepted}/{r.appeals}</TableCell>
                  <TableCell className="text-xs">
                    {r.suggestion ? <Badge variant="secondary">{r.suggestion}</Badge> : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardPanel>
    </Card>
  );
}
