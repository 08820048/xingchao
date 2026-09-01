import {
  ArrowRight,
  BarChart3,
  Check,
  GitFork,
  HeartHandshake,
  LockKeyhole,
  MessageCircleHeart,
  MessagesSquare,
  ServerCog,
  ShieldCheck,
  UserRoundCheck,
} from "lucide-react";

import character from "@/assets/brand/xingchao-anime-character-full.jpg";
import {
  Accordion,
  AccordionItem,
  AccordionPanel,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardDescription,
  CardFooter,
  CardHeader,
  CardPanel,
  CardTitle,
} from "@/components/ui/card";

const github = "https://github.com/08820048/xingchao";

const features = [
  {
    icon: MessageCircleHeart,
    eyebrow: "回应",
    title: "被需要时，才轻轻出现",
    description:
      "支持指令、关键词、@ 唤起与可选 AI 问答。冷却机制避免刷屏，让她融入群聊，而不是占据群聊。",
  },
  {
    icon: UserRoundCheck,
    eyebrow: "秩序",
    title: "琐碎群务，交给她处理",
    description:
      "欢迎新人、管理白名单、处理加群申请，并为超管提供禁言、撤回与群状态控制。",
  },
  {
    icon: BarChart3,
    eyebrow: "洞察",
    title: "看见群聊真实的潮汐",
    description:
      "活跃统计、发言排行、关键词词库和本地日志汇聚在一个面板里，重要变化清晰可见。",
  },
];

const faqs = [
  {
    q: "星潮会回复群里的每一条消息吗？",
    a: "不会。她默认只响应指令、关键词、@ 唤起和昵称唤起；AI 问答也可以独立关闭。冷却机制会抑制重复触发。",
  },
  {
    q: "聊天数据会发送到第三方吗？",
    a: "日志、配置和统计默认保存在你自己的服务器。只有主动启用 AI 功能时，对应问题才会发送到你配置的 OpenAI 兼容服务。",
  },
  {
    q: "可以只让机器人服务指定群吗？",
    a: "可以。星潮采用安全优先的白名单机制：空白名单不会处理任何群；你也可以在运行时单独停用某个群。",
  },
  {
    q: "使用 NapCat 登录 QQ 安全吗？",
    a: "NapCat 使用非官方协议，存在账号风控与封禁风险。请使用小号，不要与电脑 QQ 同时登录，并避免把管理端口暴露到公网。",
  },
];

function Brand() {
  return (
    <a href="/" className="group flex items-center gap-2.5" aria-label="星潮首页">
      <span className="size-9 overflow-hidden rounded-full border-2 border-white shadow-md transition-transform group-hover:scale-105">
        <img src={character} alt="" className="size-full scale-150 object-cover object-center" />
      </span>
      <span className="leading-none">
        <span className="block text-[17px] font-semibold tracking-tight text-[#2d2540]">星潮</span>
        <span className="mt-1 block text-[9px] font-semibold tracking-[.22em] text-[#9a8bac]">XINGCHAO</span>
      </span>
    </a>
  );
}

export default function LandingPage() {
  const faqSchema = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqs.map(({ q, a }) => ({
      "@type": "Question",
      name: q,
      acceptedAnswer: { "@type": "Answer", text: a },
    })),
  };

  return (
    <div className="landing min-h-screen overflow-hidden bg-[#fffafb] text-[#312a3c] selection:bg-fuchsia-200/70">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(faqSchema) }}
      />

      <header className="fixed left-1/2 top-3 z-50 -translate-x-1/2">
        <div className="flex w-max max-w-[calc(100vw-1rem)] items-center gap-1.5 rounded-full border border-black/8 bg-white/80 p-1.5 shadow-[0_8px_30px_rgba(49,39,73,.12)] backdrop-blur-xl">
          <a href="/" aria-label="星潮首页" className="size-8 overflow-hidden rounded-full">
            <img src={character} alt="" className="size-full scale-150 object-cover object-center" />
          </a>
          <span className="mx-1 h-5 w-px bg-border" aria-hidden="true" />
          <nav className="hidden items-center gap-1 md:flex" aria-label="主导航">
            <a className="rounded-full bg-secondary px-3 py-1.5 text-xs font-medium text-foreground" href="#features" aria-current="location">能力</a>
            <a className="rounded-full px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground" href="#safety">边界</a>
            <a className="rounded-full px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground" href="#deploy">部署</a>
            <a className="rounded-full px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground" href="#faq">问答</a>
          </nav>
          <Button render={<a href="/panel" />} size="sm" className="rounded-full px-3">
            管理面板
          </Button>
        </div>
      </header>

      <main>
        <section className="relative isolate pt-18">
          <div className="pointer-events-none absolute inset-0 -z-10 overflow-hidden" aria-hidden="true">
            <div className="absolute -left-24 top-24 size-80 rounded-full bg-rose-200/35 blur-3xl" />
            <div className="absolute right-[-8rem] top-8 size-[32rem] rounded-full bg-violet-200/35 blur-3xl" />
            <div className="absolute bottom-0 left-1/3 size-80 rounded-full bg-cyan-100/45 blur-3xl" />
          </div>

          <div className="mx-auto grid min-h-[calc(100svh-4.5rem)] max-w-7xl items-center gap-10 px-5 py-16 sm:px-8 lg:grid-cols-[.92fr_1.08fr] lg:gap-16 lg:py-20">
            <div className="relative z-10 max-w-2xl">
              <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-fuchsia-200/70 bg-white/70 px-3.5 py-1.5 text-xs font-medium text-[#8a5aa0] shadow-sm backdrop-blur">
                <span className="size-1.5 rounded-full bg-cyan-400 shadow-[0_0_0_4px_rgba(34,211,238,.12)]" />
                开源 · 自托管 · 懂分寸的 QQ 群助手
              </div>
              <h1 className="text-[clamp(2.65rem,6vw,5.5rem)] font-semibold leading-[.94] tracking-[-.065em] text-[#2d2540]">
                <span className="whitespace-nowrap">群聊有潮汐，<span className="bg-gradient-to-r from-[#eb75a8] to-[#a46bd8] bg-clip-text text-transparent">她</span></span>
                <span className="mt-2 block bg-gradient-to-r from-[#dc6da1] via-[#a46bd8] to-[#55b8ce] bg-clip-text text-transparent">懂得分寸。</span>
              </h1>
              <p className="mt-7 max-w-xl text-base leading-8 text-[#746a82] sm:text-lg">
                星潮把关键词回复、群管理、活跃统计与 AI 问答放进一个温柔可靠的助手里。
                只服务你允许的群，只在被需要时回应。
              </p>
              <div className="mt-9 flex flex-col gap-3 sm:flex-row">
                <Button
                  render={<a href={`${github}#快速开始`} target="_blank" rel="noreferrer" />}
                  size="xl"
                  className="w-full sm:w-auto"
                >
                  开始部署 <ArrowRight className="size-4" />
                </Button>
                <Button
                  render={<a href={github} target="_blank" rel="noreferrer" />}
                  variant="outline"
                  size="xl"
                  className="w-full sm:w-auto"
                >
                  <GitFork className="size-4" /> 查看源码
                </Button>
              </div>
              <div className="mt-8 flex flex-wrap gap-x-6 gap-y-3 text-xs text-[#8c8198]">
                {["白名单安全默认", "数据留在本地", "Docker Compose 部署"].map((item) => (
                  <span key={item} className="flex items-center gap-1.5">
                    <Check className="size-3.5 text-[#58aebd]" /> {item}
                  </span>
                ))}
              </div>
            </div>

            <div className="relative mx-auto w-full max-w-[650px] lg:mr-[-3rem]">
              <div className="absolute -inset-4 -z-10 rotate-3 rounded-[3.5rem] bg-gradient-to-br from-rose-200/70 via-violet-200/50 to-cyan-100/70 blur-sm" />
              <div className="relative overflow-hidden rounded-[2.75rem] border border-white/80 bg-white p-2 shadow-[0_30px_90px_rgba(101,74,130,.18)]">
                <img
                  src={character}
                  width={1254}
                  height={1254}
                  alt="星潮的粉紫长卷发二次元女性角色"
                  fetchPriority="high"
                  className="aspect-square w-full rounded-[2.3rem] object-cover"
                />
                <div className="absolute inset-x-8 bottom-8 rounded-2xl border border-white/65 bg-white/78 p-4 shadow-lg backdrop-blur-xl sm:inset-x-auto sm:right-8 sm:w-72">
                  <div className="flex items-start gap-3">
                    <span className="size-10 shrink-0 overflow-hidden rounded-full border-2 border-white shadow-md">
                      <img src={character} alt="" className="size-full scale-150 object-cover object-center" />
                    </span>
                    <div>
                      <p className="text-xs font-semibold text-[#43364f]">星潮正在听</p>
                      <p className="mt-1 text-xs leading-5 text-[#82748d]">@我，或者轻声说一句“星潮”。</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <div className="bg-background text-foreground">
          <section id="features" className="scroll-mt-24 border-t px-5 py-20 sm:px-8 lg:py-24">
            <div className="mx-auto max-w-7xl">
              <div className="max-w-2xl">
                <Badge variant="outline">核心能力</Badge>
                <h2 className="mt-4 text-3xl font-semibold tracking-tight sm:text-4xl">群聊里常用的事，集中处理。</h2>
                <p className="mt-3 text-sm leading-6 text-muted-foreground">回复、管理与统计使用同一套配置，并可按群独立开关。</p>
              </div>
              <div className="mt-8 grid gap-4 lg:grid-cols-3">
                {features.map(({ icon: Icon, eyebrow, title, description }) => (
                  <Card key={title} className="h-full">
                    <CardHeader>
                      <CardTitle>{title}</CardTitle>
                      <CardDescription className="leading-6">{description}</CardDescription>
                      <CardAction><Icon className="size-4 text-muted-foreground" /></CardAction>
                    </CardHeader>
                    <CardFooter><Badge variant="secondary">{eyebrow}</Badge></CardFooter>
                  </Card>
                ))}
              </div>
            </div>
          </section>

          <section id="safety" className="scroll-mt-24 border-y bg-muted/40 px-5 py-20 sm:px-8 lg:py-24">
            <div className="mx-auto grid max-w-7xl gap-10 lg:grid-cols-[.8fr_1.2fr] lg:items-center">
              <div className="max-w-xl">
                <Badge variant="success"><ShieldCheck /> 安全默认</Badge>
                <h2 className="mt-4 text-3xl font-semibold tracking-tight sm:text-4xl">默认拒绝，再按需开放。</h2>
                <p className="mt-4 text-sm leading-7 text-muted-foreground">空白名单不会处理任何群。面板、NapCat WebUI 与机器人端口默认只对本机开放，敏感操作仅允许授权账号执行。</p>
              </div>
              <Card>
                <CardHeader className="border-b">
                  <CardTitle>安全策略</CardTitle>
                  <CardDescription>当前项目的默认保护规则</CardDescription>
                  <CardAction><Badge variant="success">已启用</Badge></CardAction>
                </CardHeader>
                <CardPanel className="divide-y py-0">
                  {[
                    [LockKeyhole, "最小暴露", "管理能力不直接暴露到公网", "localhost only"],
                    [ServerCog, "本地优先", "SQLite、词库和日志由你保管", "local storage"],
                    [HeartHandshake, "超管分权", "敏感操作只允许授权账号", "superuser"],
                    [MessagesSquare, "按需回应", "模块与群级业务都可独立关闭", "group scoped"],
                  ].map(([Icon, title, text, code]) => {
                    const I = Icon as typeof LockKeyhole;
                    return (
                      <div key={title as string} className="flex items-start gap-3 py-4">
                        <I className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium">{title as string}</p>
                          <p className="mt-1 text-xs text-muted-foreground">{text as string}</p>
                        </div>
                        <code className="hidden text-xs text-muted-foreground sm:block">{code as string}</code>
                      </div>
                    );
                  })}
                </CardPanel>
              </Card>
            </div>
          </section>

          <section id="deploy" className="scroll-mt-24 px-5 py-20 sm:px-8 lg:py-24">
            <div className="mx-auto max-w-5xl">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <Badge variant="outline">自托管部署</Badge>
                  <h2 className="mt-4 text-3xl font-semibold tracking-tight sm:text-4xl">三步启动星潮。</h2>
                  <p className="mt-3 text-sm text-muted-foreground">Docker Compose 同时运行 NoneBot2 与 NapCat。</p>
                </div>
                <Button render={<a href={`${github}#快速开始`} target="_blank" rel="noreferrer" />} variant="outline">
                  完整文档 <ArrowRight />
                </Button>
              </div>
              <Card className="mt-8">
                <CardPanel className="p-0">
                  <ol className="divide-y">
                    {[
                      ["01", "填写环境变量", "配置超管、群白名单与强随机访问令牌。"],
                      ["02", "启动两个容器", "运行 docker compose up -d。"],
                      ["03", "扫码并连接", "用 QQ 小号登录 NapCat，配置反向 WebSocket。"],
                    ].map(([n, title, text]) => (
                      <li key={n} className="grid gap-3 p-5 sm:grid-cols-[auto_1fr] sm:items-center sm:gap-5 sm:px-6">
                        <Badge variant="secondary" className="justify-self-start">{n}</Badge>
                        <div>
                          <h3 className="text-sm font-medium">{title}</h3>
                          <p className="mt-1 text-xs leading-5 text-muted-foreground">{text}</p>
                        </div>
                      </li>
                    ))}
                  </ol>
                </CardPanel>
              </Card>
            </div>
          </section>

          <section id="faq" className="scroll-mt-24 border-y bg-muted/40 px-5 py-20 sm:px-8 lg:py-24">
            <div className="mx-auto grid max-w-6xl gap-10 lg:grid-cols-[.7fr_1.3fr]">
              <div>
                <Badge variant="outline">常见问题</Badge>
                <h2 className="mt-4 text-3xl font-semibold tracking-tight sm:text-4xl">开始之前</h2>
                <p className="mt-3 text-sm leading-6 text-muted-foreground">能力范围、数据去向与使用风险。</p>
              </div>
              <Card>
                <CardPanel className="py-0">
                  <Accordion>
                    {faqs.map(({ q, a }) => (
                      <AccordionItem key={q} value={q}>
                        <AccordionTrigger>{q}</AccordionTrigger>
                        <AccordionPanel className="leading-6">{a}</AccordionPanel>
                      </AccordionItem>
                    ))}
                  </Accordion>
                </CardPanel>
              </Card>
            </div>
          </section>

          <section className="px-5 py-12 sm:px-8 lg:py-16">
            <Card className="mx-auto max-w-7xl">
              <CardPanel className="flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <CardTitle>让星潮加入你的群</CardTitle>
                  <CardDescription className="mt-2">MIT 协议开源，可自行部署、修改和扩展。</CardDescription>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Button render={<a href={`${github}#快速开始`} target="_blank" rel="noreferrer" />}>
                    开始部署 <ArrowRight />
                  </Button>
                  <Button render={<a href={github} target="_blank" rel="noreferrer" />} variant="outline">
                    <GitFork /> GitHub
                  </Button>
                </div>
              </CardPanel>
            </Card>
          </section>
        </div>
      </main>

      <footer className="border-t bg-background px-5 pb-10 pt-8 sm:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="flex flex-col gap-8 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <Brand />
              <p className="mt-4 max-w-sm text-xs leading-6 text-muted-foreground">基于 NapCat、OneBot v11 与 NoneBot2 构建的开源 QQ 群助手。与腾讯官方开放平台无关。</p>
            </div>
            <div className="flex items-center gap-5 text-xs text-muted-foreground">
              <a className="hover:text-foreground" href={github} target="_blank" rel="noreferrer">GitHub</a>
              <a className="hover:text-foreground" href={`${github}/blob/main/LICENSE`} target="_blank" rel="noreferrer">MIT License</a>
              <a className="hover:text-foreground" href="/panel">管理面板</a>
            </div>
          </div>
          <div className="mt-8 flex flex-wrap items-center gap-2 border-t pt-5 text-xs text-muted-foreground">
            <a href={github} target="_blank" rel="noreferrer" aria-label="星潮 GitHub 仓库" className="hover:text-foreground">
              <GitFork className="size-3.5" />
            </a>
            <span>© 2026 Xingchao</span>
            <span aria-hidden="true">·</span>
            <span>Made with love by <a href="https://xuyi.dev" target="_blank" rel="noreferrer" className="text-foreground underline-offset-4 hover:underline">xuyi</a></span>
          </div>
        </div>
      </footer>
    </div>
  );
}
