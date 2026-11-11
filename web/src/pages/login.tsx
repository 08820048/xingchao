import { useState } from "react";
import { MessageSquareText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";

export function Login({ onOk }: { onOk: () => void }) {
  const [pwd, setPwd] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const submit = async () => {
    setBusy(true);
    setErr("");
    const r = await fetch("/panel/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pwd }),
    });
    setBusy(false);
    if (r.ok) onOk();
    else setErr((await r.json()).error || "登录失败");
  };
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-xl">
            <MessageSquareText className="size-5 text-primary" />
            星潮 · 管理面板
          </CardTitle>
          <CardDescription>请输入面板密码登录</CardDescription>
        </CardHeader>
        <CardPanel className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="pwd">密码</Label>
            <Input
              id="pwd"
              type="password"
              value={pwd}
              onChange={(e) => setPwd(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              autoFocus
            />
            {err && <p className="text-xs text-red-600 dark:text-red-400">{err}</p>}
          </div>
          <Button onClick={submit} disabled={busy || !pwd}>
            {busy && <Spinner />}登 录
          </Button>
        </CardPanel>
      </Card>
    </div>
  );
}
