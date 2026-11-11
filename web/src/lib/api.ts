

export async function api<T = any>(path: string, opt: RequestInit = {}): Promise<T> {
  const r = await fetch(path, opt);
  if (r.status === 401) {
    window.dispatchEvent(new Event("panel:unauthorized"));
    throw new Error("unauthorized");
  }
  return r.json();
}
/* 群名称：群号 → 群名（面板启动后从机器人拉取；未取到时只显示群号） */

export const groupNameMap: Record<string, string> = {};

export const groupLabel = (id: number | string) => {
  const n = groupNameMap[String(id)];
  return n ? `${n}（${id}）` : `群 ${id}`;
};

export const post = (p: string, body?: unknown) =>
  api(p, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });

export const fmtDur = (s: number) => {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return (h ? `${h} 小时 ` : "") + `${m} 分钟`;
};

export function setGroupNames(m: Record<string, string>) {
  for (const k of Object.keys(groupNameMap)) delete groupNameMap[k];
  Object.assign(groupNameMap, m);
}
