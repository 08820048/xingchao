import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";

export function LogsTab() {
  const [files, setFiles] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [tail, setTail] = useState(200);
  const [filter, setFilter] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    api<{ ok: boolean; data: { name: string }[] }>("/panel/api/logfiles").then((r) => {
      if (r.ok) {
        setFiles(r.data.map((f) => f.name));
        if (r.data.length) setName(r.data[r.data.length - 1].name);
      }
    });
  }, []);
  const show = async () => {
    setLoading(true);
    const r = await api<{ ok: boolean; data: { records: any[] } }>(
      `/panel/api/logs?name=${encodeURIComponent(name)}&tail=${tail}`,
    );
    if (r.ok)
      setLines(
        r.data.records.map(
          (x) =>
            `[${(x.time || "").slice(11, 19)}] ${x.user_id}: ${x.raw_plain || ""}`,
        ),
      );
    setLoading(false);
  };
  const shown = filter
    ? lines.filter((l) => l.toLowerCase().includes(filter.toLowerCase()))
    : lines;
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">群消息日志</CardTitle>
          <CardDescription>白名单群全量文本日志（jsonl），支持关键字过滤</CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-3">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex min-w-56 flex-col gap-1.5">
              <Label htmlFor="lf">日志文件</Label>
              <select
                id="lf"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="border-input bg-background flex h-8 w-full items-center rounded-lg border px-3 text-sm"
              >
                {files.map((f) => (
                  <option key={f}>{f}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="tail">行数</Label>
              <Input
                id="tail"
                type="number"
                className="w-24"
                value={tail}
                onChange={(e) => setTail(+e.target.value || 200)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="kw">过滤关键字</Label>
              <Input
                id="kw"
                className="w-44"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="如：用户QQ号"
              />
            </div>
            <Button onClick={show} disabled={!name}>
              {loading && <Spinner />}查看
            </Button>
          </div>
          <pre className="bg-muted/50 max-h-[30rem] overflow-auto rounded-lg border p-3 font-mono text-xs">
            {shown.length
              ? shown.join("\n")
              : loading
                ? "加载中…"
                : "选择日志文件后查看"}
          </pre>
        </CardPanel>
      </Card>
    </div>
  );
}
