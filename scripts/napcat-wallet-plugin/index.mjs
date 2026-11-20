// 星潮本地 NapCat 补丁：
// 把 QQ 红包（walletElement）转成 OneBot json 段，让 bot 能识别并发红包提醒。
//
// 这里只做「读取/转换」，不提供领取、抢红包或任何自动操作接口。

function safeStringify(value) {
  try {
    return JSON.stringify(value, (_key, val) =>
      typeof val === "bigint" ? val.toString() : val
    );
  } catch (err) {
    return JSON.stringify({ error: String(err) });
  }
}

function installConverter(ctx) {
  // 注意：core.apis.MsgApi 是 QQ 底层 API；带 rawToOb11Converters 的是
  // OneBot 上下文里的 ctx.oneBot.apis.MsgApi。
  const msgApi = ctx?.oneBot?.apis?.MsgApi || ctx?.core?.apis?.MsgApi;
  const converters = msgApi?.rawToOb11Converters;
  if (!converters) return false;

  converters.walletElement = async (element) => {
    const raw = safeStringify({ walletElement: element });
    ctx.logger.info(`[xingchao-wallet] 收到红包元素：${raw.slice(0, 1200)}`);
    return {
      type: "json",
      data: { data: raw },
    };
  };
  ctx.logger.info("[xingchao-wallet] walletElement 转换器已注册（仅提醒，不领取）");
  return true;
}

export async function plugin_init(ctx) {
  try {
    if (installConverter(ctx)) return;

    // 插件管理器在 MsgApi 初始化之前就会加载插件，这里异步等它就绪。
    ctx.logger.info("[xingchao-wallet] MsgApi 尚未就绪，等待后重试注册");
    const timer = setInterval(() => {
      try {
        if (installConverter(ctx)) clearInterval(timer);
      } catch (err) {
        ctx.logger.error(`[xingchao-wallet] 注册失败：${err?.stack || err}`);
        clearInterval(timer);
      }
    }, 1000);
    setTimeout(() => {
      clearInterval(timer);
      const msgApi = ctx?.oneBot?.apis?.MsgApi || ctx?.core?.apis?.MsgApi;
      if (!msgApi?.rawToOb11Converters?.walletElement) {
        ctx.logger.warn("[xingchao-wallet] 等待超时，红包仍会显示为空消息");
      }
    }, 60000);
  } catch (err) {
    ctx.logger.error(`[xingchao-wallet] 初始化失败：${err?.stack || err}`);
  }
}
