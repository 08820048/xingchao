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

type AiConfig = {
  configured: boolean;
  base_url: string;
  api_key_masked: string;
  provider: string;
  active_provider: string;
  deepseek: { api_key_masked: string; model: string; base_url: string };
  enabled: boolean;
  model: string;
  vision_model: string;
  system_prompt: string;
  ctx_rounds: number;
  limit_group: number;
  limit_user: number;
  usage: { day: string; groups: Record<string, number>; users: Record<string, number> };
};

type ActivityData = {
  default_mode: string;
  groups: Record<string, string>;
  overrides: Record<string, boolean>;
  quiet_start: string;
  quiet_end: string;
  modes: Record<string, string>;
};

function ActivityCard({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [d, setD] = useState<ActivityData | null>(null);
  const [quiet, setQuiet] = useState({ start: "23:30", end: "07:30" });
  const load = useCallback(() => {
    api<{ ok: boolean; data: ActivityData }>("/panel/api/proactive").then((r) => {
      if (r.ok) {
        setD(r.data);
        setQuiet({ start: r.data.quiet_start, end: r.data.quiet_end });
      }
    });
  }, []);
  useEffect(() => load(), [load]);
  if (!d) return <Spinner className="m-8" />;
  const save = async (patch: Record<string, unknown>) => {
    const r = await post("/panel/api/proactive", patch);
    if (r.ok) { toast(r.data.message); load(); } else toast(r.error || "保存失败", false);
  };
  const sel = "border-input bg-background flex h-8 items-center rounded-lg border px-2 text-sm";
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">主动程度</CardTitle>
        <CardDescription>
          安静：很少插话；正常：有话题或求助时参与，偶尔看图、复读；话痨：更爱接话、评论图片、贴表情。
          机器人回复后，对方接着说不用再 @ 也会继续聊。需先在仪表盘开启「群聊主动性」
        </CardDescription>
      </CardHeader>
      <CardPanel className="grid gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Label>全局默认</Label>
          <select value={d.default_mode} className={sel}
            onChange={(e) => save({ mode: e.target.value, group_id: "default" })}>
            {Object.entries(d.modes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div className="grid gap-2">
          {Object.entries(d.groups).map(([gid, mode]) => (
            <div key={gid} className="border-input flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3">
              <span className="text-sm">
                {groupLabel(gid)}
                {!d.overrides[gid] && <Badge variant="secondary" className="ml-1">跟随默认</Badge>}
              </span>
              <div className="flex items-center gap-2">
                <select value={mode} className={sel}
                  onChange={(e) => save({ mode: e.target.value, group_id: +gid })}>
                  {Object.entries(d.modes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                {d.overrides[gid] && (
                  <Button size="sm" variant="outline" onClick={() => save({ clear_group: +gid })}>恢复默认</Button>
                )}
              </div>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="q-s">夜间静默开始</Label>
            <Input id="q-s" type="time" value={quiet.start} onChange={(e) => setQuiet({ ...quiet, start: e.target.value })} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="q-e">结束</Label>
            <Input id="q-e" type="time" value={quiet.end} onChange={(e) => setQuiet({ ...quiet, end: e.target.value })} />
          </div>
          <Button onClick={() => save({ quiet_start: quiet.start, quiet_end: quiet.end })}>保存静默时段</Button>
        </div>
        <p className="text-muted-foreground text-xs">静默时段内机器人不会主动插话，只会继续回应正在和它聊天的人</p>
      </CardPanel>
    </Card>
  );
}

type PersonaData = {
  card: Record<string, string>;
  labels: Record<string, string>;
  defaults: Record<string, string>;
  fields: string[];
};

function PersonaCard({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [d, setD] = useState<PersonaData | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const load = useCallback(() => {
    api<{ ok: boolean; data: PersonaData }>("/panel/api/persona").then((r) => {
      if (r.ok) { setD(r.data); setForm(r.data.card); }
    });
  }, []);
  useEffect(() => load(), [load]);
  if (!d) return <Spinner className="m-8" />;
  const save = async () => {
    const r = await post("/panel/api/persona", { card: form });
    if (r.ok) { toast(r.data.message); load(); } else toast(r.error || "保存失败", false);
  };
  const reset = async () => {
    const r = await post("/panel/api/persona", { action: "reset" });
    if (r.ok) { toast(r.data.message); load(); } else toast(r.error || "操作失败", false);
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">人设卡</CardTitle>
        <CardDescription>
          机器人的性格与说话方式，@ 对话和主动发言共用，保存后下一条消息起生效。
          「底线」里的“不冒充真人”会被自动保留，无法删除
        </CardDescription>
      </CardHeader>
      <CardPanel className="grid gap-3">
        {d.fields.map((k) => (
          <div key={k} className="flex flex-col gap-1.5">
            <Label htmlFor={`persona-${k}`}>{d.labels[k]}</Label>
            <Textarea id={`persona-${k}`} rows={k === "name" ? 1 : 2} maxLength={300}
              value={form[k] ?? ""} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
          </div>
        ))}
        <div className="flex gap-2">
          <Button onClick={save}>保存人设</Button>
          <Button variant="outline" onClick={reset}>恢复默认</Button>
        </div>
      </CardPanel>
    </Card>
  );
}

type MemoryData = {
  enabled: boolean;
  groups: number[];
  group_id: number | null;
  group_facts: string[];
  members: { user_id: number; facts: string[]; interactions: number; updated_at: string | null }[];
  optouts: number[];
};

function MemoryCard({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [d, setD] = useState<MemoryData | null>(null);
  const [gid, setGid] = useState<number | null>(null);
  const [groupText, setGroupText] = useState("");
  const load = useCallback((g: number | null) => {
    const q = g ? `?group_id=${g}` : "";
    api<{ ok: boolean; data: MemoryData }>(`/panel/api/memory${q}`).then((r) => {
      if (r.ok) {
        setD(r.data);
        setGid(r.data.group_id);
        setGroupText(r.data.group_facts.join("\n"));
      }
    });
  }, []);
  useEffect(() => load(null), [load]);
  if (!d) return <Spinner className="m-8" />;
  const act = async (body: Record<string, unknown>) => {
    const r = await post("/panel/api/memory", { group_id: gid, ...body });
    if (r.ok) { toast(r.data.message); load(gid); } else toast(r.error || "操作失败", false);
  };
  const sel = "border-input bg-background flex h-8 items-center rounded-lg border px-2 text-sm";
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">长期记忆</CardTitle>
        <CardDescription>
          机器人对群友的印象与群记忆（模型从聊天里提炼的短事实，不存原话，按群隔离）。
          成员可发送 /忘记我 自行清除并停止被记录；这里可以查看、改写、删除
        </CardDescription>
      </CardHeader>
      <CardPanel className="grid gap-3">
        <div className="border-input flex items-center justify-between rounded-lg border p-3">
          <div>
            <p className="text-sm font-medium">记忆功能</p>
            <p className="text-muted-foreground text-xs">关闭后不再提炼新记忆，也不再注入已有记忆</p>
          </div>
          <Switch checked={d.enabled} onCheckedChange={(v) => act({ action: "set_enabled", enabled: v })} />
        </div>
        <div className="flex items-center gap-2">
          <Label>群</Label>
          <select value={gid ?? ""} className={sel} onChange={(e) => load(+e.target.value)}>
            {d.groups.map((g) => <option key={g} value={g}>{groupLabel(g)}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="mem-group">群记忆（每行一条）</Label>
          <Textarea id="mem-group" rows={3} value={groupText} onChange={(e) => setGroupText(e.target.value)} />
          <div className="flex gap-2">
            <Button size="sm" onClick={() => act({ action: "set_group_facts", facts: groupText.split("\n") })}>保存</Button>
            <Button size="sm" variant="outline" onClick={() => act({ action: "delete_group" })}>清空</Button>
          </div>
        </div>
        <div className="grid gap-2">
          {d.members.length === 0 && <p className="text-muted-foreground text-sm">这个群还没有成员印象</p>}
          {d.members.map((m) => (
            <div key={m.user_id}
              className="border-input flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3">
              <div className="text-sm">
                <b>QQ {m.user_id}</b>
                <Badge variant="secondary" className="ml-1">互动 {m.interactions} 次</Badge>
                <p className="text-muted-foreground text-xs">{m.facts.length ? m.facts.join("；") : "（暂无特别印象）"}</p>
              </div>
              <Button size="sm" variant="outline" onClick={() => act({ action: "delete_user", user_id: m.user_id })}>
                删除
              </Button>
            </div>
          ))}
        </div>
        {d.optouts.length > 0 && (
          <p className="text-muted-foreground text-xs">已选择不被记录的成员：{d.optouts.join("、")}</p>
        )}
      </CardPanel>
    </Card>
  );
}

export function AiTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [cfg, setCfg] = useState<AiConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [connBase, setConnBase] = useState("");
  const [connKey, setConnKey] = useState("");
  const [dsKey, setDsKey] = useState("");
  const [dsModel, setDsModel] = useState("");
  const [models, setModels] = useState<string[] | null>(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const load = useCallback(() => {
    api<{ ok: boolean; data: AiConfig }>("/panel/api/ai").then((r) => {
      if (r.ok) setCfg(r.data);
    });
  }, []);
  useEffect(() => load(), [load]);
  useEffect(() => {
    if (cfg) setConnBase(cfg.base_url);
  }, [cfg?.base_url]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (cfg) setDsModel(cfg.deepseek.model);
  }, [cfg?.deepseek.model]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!cfg) return <Spinner className="m-8" />;

  const save = async (patch: Record<string, unknown>) => {
    setSaving(true);
    const r = await post("/panel/api/ai", patch);
    setSaving(false);
    if (r.ok) {
      toast(r.data.message);
      load();
    } else toast(r.error || "保存失败", false);
  };

  const totalToday = Object.values(cfg.usage.groups).reduce((a, b) => a + b, 0);

  return (
    <div className="grid gap-4">
      <ActivityCard toast={toast} />
      <PersonaCard toast={toast} />
      <MemoryCard toast={toast} />

      {!cfg.configured && (
        <Card>
          <CardPanel className="border-amber-500/40 rounded-lg border bg-amber-50 p-4 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">
            ⚠️ AI 未配置：请在下方「API 连接」填写 API 地址与密钥，保存后即可在「功能开关」中开启。
          </CardPanel>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">API 连接</CardTitle>
          <CardDescription>
            OpenAI 兼容接口（智谱 GLM 地址：<code>https://open.bigmodel.cn/api/paas/v4</code>）。
            Key 留空表示保持不变。保存后立即生效（无需重启）
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="aibase">API 地址</Label>
              <Input
                id="aibase"
                value={connBase}
                onChange={(e) => setConnBase(e.target.value)}
                placeholder="https://api.b.ai/v1"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="aikey">API Key</Label>
              <Input
                id="aikey"
                type="password"
                value={connKey}
                onChange={(e) => setConnKey(e.target.value)}
                placeholder={cfg.api_key_masked || "尚未设置"}
              />
            </div>
            <Button
              className="self-end"
              disabled={saving}
              onClick={async () => {
                const patch: Record<string, unknown> = {};
                if (connBase !== cfg.base_url) patch.base_url = connBase;
                if (connKey) patch.api_key = connKey;
                if (!Object.keys(patch).length) return toast("没有需要保存的修改");
                await save(patch);
                setConnKey("");
              }}
            >
              {saving && <Spinner />}保存连接
            </Button>
          </div>
          <div className="flex justify-end">
            <Button
              variant="outline"
              size="sm"
              disabled={loadingModels}
              onClick={async () => {
                setModels(null);
                setLoadingModels(true);
                const r = await post("/panel/api/ai/models", {
                  base_url: connBase,
                  ...(connKey ? { api_key: connKey } : {}),
                });
                setLoadingModels(false);
                if (r.ok) {
                  setModels(r.data.models);
                  toast(`获取到 ${r.data.models.length} 个模型`);
                } else toast(r.error || "获取模型列表失败", false);
              }}
            >
              {loadingModels && <Spinner />}获取模型列表
            </Button>
          </div>
          {models && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="modelpick">从列表选择模型</Label>
              <select
                id="modelpick"
                className="border-input bg-background rounded-lg border px-3 py-2 text-sm"
                value=""
                onChange={(e) => {
                  if (e.target.value) save({ model: e.target.value });
                  setModels(null);
                }}
              >
                <option value="">
                  {models.length ? `共 ${models.length} 个模型，点击选择并保存` : "服务商未返回任何模型"}
                </option>
                {models.map((m) => (
                  <option key={m} value={m} className="font-mono text-xs">
                    {m}
                  </option>
                ))}
              </select>
            </div>
          )}
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">DeepSeek 提供商</CardTitle>
          <CardDescription>
            启用后问答、链接解读、加群审批等文本任务改用 DeepSeek（官方地址{" "}
            <code>{cfg.deepseek.base_url}</code>）；图片识别与语音识别仍使用上方默认连接。
            Key 留空表示保持不变
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">使用 DeepSeek</p>
              <p className="text-muted-foreground text-xs">
                当前生效：{cfg.active_provider === "deepseek" ? "DeepSeek" : "默认连接"}
                （需先保存 DeepSeek Key）
              </p>
            </div>
            <Switch
              checked={cfg.provider === "deepseek"}
              onCheckedChange={(v) =>
                save({
                  provider: v ? "deepseek" : "default",
                  ...(dsKey ? { deepseek_api_key: dsKey } : {}),
                })
              }
            />
          </div>
          <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="dskey">DeepSeek API Key</Label>
              <Input
                id="dskey"
                type="password"
                value={dsKey}
                onChange={(e) => setDsKey(e.target.value)}
                placeholder={cfg.deepseek.api_key_masked || "尚未设置"}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="dsmodel">DeepSeek 模型</Label>
              <Input
                id="dsmodel"
                value={dsModel}
                onChange={(e) => setDsModel(e.target.value)}
                placeholder="deepseek-chat"
              />
            </div>
            <Button
              className="self-end"
              disabled={saving}
              onClick={async () => {
                const patch: Record<string, unknown> = {};
                if (dsKey) patch.deepseek_api_key = dsKey;
                if (dsModel !== cfg.deepseek.model) patch.deepseek_model = dsModel;
                if (!Object.keys(patch).length) return toast("没有需要保存的修改");
                await save(patch);
                setDsKey("");
              }}
            >
              {saving && <Spinner />}保存 DeepSeek
            </Button>
          </div>
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">AI 问答</CardTitle>
          <CardDescription>
            群内 @机器人（或昵称唤起）即由 AI 回答；带多轮会话记忆与每日限额护栏
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="border-input flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">功能开关</p>
              <p className="text-muted-foreground text-xs">
                关闭后 @ 机器人回复固定问候语
              </p>
            </div>
            <Switch checked={cfg.enabled} onCheckedChange={(v) => save({ enabled: v })} />
          </div>
          <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="model">模型名称</Label>
              <Input
                id="model"
                value={cfg.model}
                onChange={(e) => setCfg({ ...cfg, model: e.target.value })}
              />
            </div>
            <Button
              className="self-end"
              disabled={saving}
              onClick={() => save({ model: cfg.model })}
            >
              {saving && <Spinner />}保存模型
            </Button>
          </div>
          <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="visionModel">视觉模型（图片识别用）</Label>
              <Input
                id="visionModel"
                value={cfg.vision_model}
                onChange={(e) => setCfg({ ...cfg, vision_model: e.target.value })}
                placeholder="如 glm-4v-flash（需支持图片输入）"
              />
            </div>
            <Button
              className="self-end"
              disabled={saving}
              onClick={() => save({ vision_model: cfg.vision_model })}
            >
              {saving && <Spinner />}保存视觉模型
            </Button>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="prompt">系统提示词（人设）</Label>
            <Textarea
              rows={4}
              value={cfg.system_prompt}
              onChange={(e) => setCfg({ ...cfg, system_prompt: e.target.value })}
            />
          </div>
          <div className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_auto]">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="rounds">会话记忆（轮）</Label>
              <Input
                id="rounds"
                type="number"
                value={cfg.ctx_rounds}
                onChange={(e) => setCfg({ ...cfg, ctx_rounds: +e.target.value || 5 })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="lg">每群日限（次）</Label>
              <Input
                id="lg"
                type="number"
                value={cfg.limit_group}
                onChange={(e) => setCfg({ ...cfg, limit_group: +e.target.value || 100 })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="lu">每人日限（次）</Label>
              <Input
                id="lu"
                type="number"
                value={cfg.limit_user}
                onChange={(e) => setCfg({ ...cfg, limit_user: +e.target.value || 20 })}
              />
            </div>
            <Button
              className="self-end"
              disabled={saving}
              onClick={() =>
                save({
                  system_prompt: cfg.system_prompt,
                  ctx_rounds: cfg.ctx_rounds,
                  limit_group: cfg.limit_group,
                  limit_user: cfg.limit_user,
                })
              }
            >
              {saving && <Spinner />}保存
            </Button>
          </div>
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">今日用量</CardTitle>
          <CardDescription>{cfg.usage.day} · 共 {totalToday} 次</CardDescription>
        </CardHeader>
        <CardPanel>
          {totalToday === 0 ? (
            <p className="text-muted-foreground text-sm">今日还没有 AI 调用</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>维度</TableHead>
                  <TableHead>标识</TableHead>
                  <TableHead>次数</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(cfg.usage.groups).map(([gid, n]) => (
                  <TableRow key={"g" + gid}>
                    <TableCell>群</TableCell>
                    <TableCell>{groupLabel(gid)}</TableCell>
                    <TableCell>{n}</TableCell>
                  </TableRow>
                ))}
                {Object.entries(cfg.usage.users).map(([uid, n]) => (
                  <TableRow key={"u" + uid}>
                    <TableCell>用户</TableCell>
                    <TableCell className="font-mono">{uid}</TableCell>
                    <TableCell>{n}</TableCell>
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
