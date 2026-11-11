import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { api, post } from "@/lib/api";

type BlogData = {
  enabled: boolean;
  last_refresh: string;
  last_error: string;
  count: number;
  products: { name: string; url: string; tagline: string; status: string }[];
  posts: {
    title: string;
    url: string;
    category: string;
    published: string;
    summary: string;
  }[];
};

export function BlogTab({ toast }: { toast: (t: string, ok?: boolean) => void }) {
  const [data, setData] = useState<BlogData | null>(null);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const load = useCallback(() => {
    api<{ ok: boolean; data: BlogData }>("/panel/api/blog").then((r) => {
      if (r.ok) setData(r.data);
    });
  }, []);
  useEffect(load, [load]);
  if (!data) return <Spinner className="m-8" />;

  const toggle = async (enabled: boolean) => {
    const r = await post("/panel/api/blog", { enabled });
    if (r.ok) {
      toast(r.data.message);
      load();
    } else toast(r.error, false);
  };
  const refresh = async () => {
    setBusy(true);
    const r = await post("/panel/api/blog", { refresh: true });
    setBusy(false);
    if (r.ok) {
      toast(r.data.message);
      load();
    } else toast(r.error, false);
  };

  const keyword = q.trim().toLowerCase();
  const posts = keyword
    ? data.posts.filter((p) =>
        `${p.title} ${p.category} ${p.summary}`.toLowerCase().includes(keyword),
      )
    : data.posts;

  return (
    <div className="grid gap-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ["文章", `${data.count} 篇`],
          ["作品", `${data.products.length} 个`],
          ["最近刷新", data.last_refresh || "尚未刷新"],
          ["状态", data.enabled ? "已开启" : "已关闭"],
        ].map(([k, v]) => (
          <Card key={k}>
            <CardPanel>
              <p className="text-muted-foreground text-xs">{k}</p>
              <p className="text-sm font-semibold break-all">{v}</p>
            </CardPanel>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">博客知识关联推荐</CardTitle>
          <CardDescription>
            让 AI 熟悉 xuyi.dev 的文章与作品集，群聊话题相关时优先推荐；
            数据来自博客 RSS / sitemap，每 24 小时自动刷新。
          </CardDescription>
          <CardAction className="flex items-center gap-2">
            <Button size="sm" variant="outline" disabled={busy} onClick={refresh}>
              {busy && <Spinner />}立即刷新
            </Button>
            <Switch
              checked={data.enabled}
              onCheckedChange={toggle}
              aria-label="启用"
            />
          </CardAction>
        </CardHeader>
        {data.last_error && (
          <CardPanel>
            <p className="text-xs text-red-500">最近错误：{data.last_error}</p>
          </CardPanel>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            作品集（{data.products.length}）
          </CardTitle>
          <CardDescription>AI 推荐时使用的作品元数据与关键词别名</CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-2 sm:grid-cols-2">
          {data.products.map((p) => (
            <div key={p.name} className="border-input rounded-lg border p-3">
              <div className="flex items-center justify-between gap-2">
                <a
                  className="text-sm font-medium underline-offset-4 hover:underline"
                  href={p.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {p.name}
                </a>
                <Badge variant="secondary">{p.status}</Badge>
              </div>
              <p className="text-muted-foreground mt-1 text-xs">{p.tagline}</p>
            </div>
          ))}
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">文章（{posts.length}）</CardTitle>
          <CardAction>
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索标题 / 分类 / 摘要"
              className="w-64"
            />
          </CardAction>
        </CardHeader>
        <CardPanel>
          {posts.length === 0 ? (
            <p className="text-muted-foreground text-sm">没有匹配的文章</p>
          ) : (
            <div className="grid gap-2">
              {posts.map((p) => (
                <div key={p.url} className="border-input rounded-lg border p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <a
                      className="font-medium underline-offset-4 hover:underline"
                      href={p.url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {p.title}
                    </a>
                    {p.category && <Badge variant="secondary">{p.category}</Badge>}
                    <span className="text-muted-foreground text-xs">
                      {p.published}
                    </span>
                  </div>
                  {p.summary && (
                    <p className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                      {p.summary}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardPanel>
      </Card>
    </div>
  );
}
