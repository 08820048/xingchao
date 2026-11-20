import { Component, type ReactNode } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

// 页面级错误边界：某个页面渲染出错或按需加载失败时，只在内容区提示，侧边栏照常可用。
// 最常见的情况是面板开着时机器人重新部署——旧页面文件被新构建替换，刷新即可。
// 由 App 按 tab 设置 key，切换页面会自动重置。
export class PageBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("页面出错", error);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const stale = /dynamically imported module|Importing a module script failed|error loading dynamically/i.test(
      error.message,
    );
    return (
      <Alert variant="error">
        <AlertTitle>{stale ? "面板已更新，需要刷新" : "这个页面出错了"}</AlertTitle>
        <AlertDescription>
          <p>{stale ? "机器人刚重新部署过，刷新一下就能加载新版本。" : error.message}</p>
          <Button size="sm" variant="outline" className="mt-2" onClick={() => window.location.reload()}>
            刷新
          </Button>
        </AlertDescription>
      </Alert>
    );
  }
}
