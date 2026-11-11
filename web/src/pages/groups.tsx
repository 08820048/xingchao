import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, groupNameMap, post } from "@/lib/api";

export function GroupsTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [groups, setGroups] = useState<
    { group_id: number; source: string; disabled: boolean }[]
  >([]);
  const [gid, setGid] = useState("");
  const load = useCallback(() => {
    api<{ ok: boolean; data: { groups: typeof groups } }>("/panel/api/groups").then((r) => {
      if (r.ok) setGroups(r.data.groups);
    });
  }, []);
  useEffect(() => load(), [load]);
  const act = async (action: "add" | "del" | "on" | "off", group_id?: number) => {
    const id = group_id ?? parseInt(gid);
    if (!id) return toast("请输入群号", false);
    const r = await post("/panel/api/groups", { action, group_id: id });
    if (r.ok) {
      toast(r.data.message);
      setGid("");
      load();
    } else toast(r.error, false);
  };
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">群白名单</CardTitle>
          <CardDescription>
            环境变量群需修改 XINGCHAO_GROUP_WHITELIST 后重启；每个群可临时开关业务（重启保留）
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="flex items-end gap-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="gid">新群号</Label>
              <Input
                id="gid"
                type="number"
                value={gid}
                onChange={(e) => setGid(e.target.value)}
                placeholder="输入群号"
              />
            </div>
            <Button onClick={() => act("add")}>添加</Button>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>群名称</TableHead>
                <TableHead>群号</TableHead>
                <TableHead>来源</TableHead>
                <TableHead>业务开关</TableHead>
                <TableHead className="w-24"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.map((g) => (
                <TableRow key={g.group_id} className={g.disabled ? "opacity-60" : ""}>
                  <TableCell>{groupNameMap[String(g.group_id)] || "—"}</TableCell>
                  <TableCell className="font-mono">{g.group_id}</TableCell>
                  <TableCell>
                    <Badge variant={g.source === "env" ? "secondary" : "info"}>
                      {g.source === "env" ? "环境变量" : "运行时"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Switch
                      checked={!g.disabled}
                      onCheckedChange={(v) => act(v ? "on" : "off", g.group_id)}
                    />
                  </TableCell>
                  <TableCell>
                    {g.source === "env" ? (
                      <span className="text-muted-foreground text-xs">需改 env</span>
                    ) : (
                      <Button variant="outline" size="sm" onClick={() => act("del", g.group_id)}>
                        移除
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardPanel>
      </Card>
    </div>
  );
}

export function SuperusersTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [superusers, setSuperusers] = useState<
    { qq: number; source: string }[]
  >([]);
  const [qq, setQq] = useState("");
  const load = useCallback(() => {
    api<{ ok: boolean; data: { superusers: typeof superusers } }>(
      "/panel/api/superusers",
    ).then((r) => {
      if (r.ok) setSuperusers(r.data.superusers);
    });
  }, []);
  useEffect(() => load(), [load]);
  const act = async (action: "add" | "del", user_id?: number) => {
    const id = user_id ?? parseInt(qq);
    if (!id) return toast("请输入 QQ 号", false);
    const r = await post("/panel/api/superusers", { action, qq: id });
    if (r.ok) {
      toast(r.data.message);
      setQq("");
      load();
    } else toast(r.error, false);
  };
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">超管列表</CardTitle>
          <CardDescription>
            超管可使用全部管理指令（禁言 / 踢人 / 撤回 / 词库 / 白名单 / 群开关等）。
            环境变量超管需修改 XINGCHAO_SUPERUSERS 后重启；运行时超管立即生效、重启保留
          </CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="flex items-end gap-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="sqq">新超管 QQ</Label>
              <Input
                id="sqq"
                type="number"
                value={qq}
                onChange={(e) => setQq(e.target.value)}
                placeholder="输入 QQ 号"
              />
            </div>
            <Button onClick={() => act("add")}>添加</Button>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>QQ 号</TableHead>
                <TableHead>来源</TableHead>
                <TableHead className="w-24"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {superusers.map((u) => (
                <TableRow key={u.qq}>
                  <TableCell className="font-mono">{u.qq}</TableCell>
                  <TableCell>
                    <Badge variant={u.source === "env" ? "secondary" : "info"}>
                      {u.source === "env" ? "环境变量" : "运行时"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {u.source === "env" ? (
                      <span className="text-muted-foreground text-xs">需改 env</span>
                    ) : (
                      <Button variant="outline" size="sm" onClick={() => act("del", u.qq)}>
                        移除
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardPanel>
      </Card>
    </div>
  );
}
