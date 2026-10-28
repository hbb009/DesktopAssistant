// =============================================================================
// 图片速存助手 · Service Worker (MV3)  v2.3.2
// =============================================================================
//
// 稳定原则
// 1. 存图 fail-open：连不上桌面也照常下；只有「已在线且桌面 DISABLE」才跳过。
// 2. 空闲不狂连：离线探活 1～8 分钟一次，避免 setInterval 每秒撞端口刷
//    net::ERR_CONNECTION_REFUSED（该网络日志仍可能出现在 SW 控制台，属正常）。
// 3. SW 被 Chrome 杀掉后：只靠 chrome.alarms 唤醒，不依赖 setInterval。
// 4. SW 每次冷启动：立刻排一次短延迟重连 + 恢复闹钟（旧版只恢复闹钟、不连，
//    在闹钟被节流或「online 假死」时会数小时无连接）。
// 5. 半开连接自愈：online 但 socket 非 OPEN、或长时间收不到桌面帧 → 强制重连。
//
// 协议 ws://127.0.0.1:19876
//   插件→桌面 PING    桌面→插件 ENABLE | DISABLE | PONG
// =============================================================================

const STAGING_SUBDIR = "WebImageSaver";
const WS_URL = "ws://127.0.0.1:19876";

const ALARM_PROBE = "wsProbe";
const ALARM_PING = "wsPing";
const ALARM_BOOT = "wsBoot";

/** 在线心跳周期（分钟），Chrome 120+ 下限 0.5≈30s */
const PING_PERIOD_MIN = 0.5;
/** 离线探活初始 / 最大（分钟）——略收紧上限，避免长时间假死 */
const OFFLINE_PROBE_MIN = 1;
const OFFLINE_PROBE_MAX_MIN = 8;
/** 握手超时 */
const CONNECT_TIMEOUT_MS = 5000;
/**
 * 已标 online 后，超过此时长仍未收到桌面任何帧（ENABLE/DISABLE/PONG），
 * 视为半开连接，强制重连。桌面约 10s 推一次状态 + PING 回 PONG。
 */
const STALE_RX_MS = 90 * 1000;

/** @type {WebSocket|null} */
let ws = null;
let online = false;
let appEnabled = true;
let connecting = false;
let offlineProbeMin = OFFLINE_PROBE_MIN;
let connectGen = 0;
/** 最近一次收到桌面下行帧的时间戳 */
let lastRxAt = 0;

// ── storage ────────────────────────────────────────────────────────────────
function loadState() {
  try {
    chrome.storage.local.get({ appEnabled: true }, (r) => {
      if (chrome.runtime.lastError) return;
      if (typeof r.appEnabled === "boolean") appEnabled = r.appEnabled;
    });
  } catch (_) {}
}

function saveEnabled(v) {
  appEnabled = !!v;
  try {
    chrome.storage.local.set({ appEnabled }, () => {
      void chrome.runtime.lastError;
    });
  } catch (_) {}
}

loadState();

// ── alarms ─────────────────────────────────────────────────────────────────
function clearAlarm(name) {
  try {
    chrome.alarms.clear(name, () => {
      void chrome.runtime.lastError;
    });
  } catch (_) {}
}

function armPing() {
  try {
    chrome.alarms.create(ALARM_PING, { periodInMinutes: PING_PERIOD_MIN });
  } catch (_) {
    // 极旧 Chrome 可能拒绝 0.5：退到 1 分钟
    try {
      chrome.alarms.create(ALARM_PING, { periodInMinutes: 1 });
    } catch (_) {}
  }
}

function armOfflineProbe(minutes) {
  const m = Math.min(
    OFFLINE_PROBE_MAX_MIN,
    Math.max(OFFLINE_PROBE_MIN, Number(minutes) || offlineProbeMin)
  );
  offlineProbeMin = m;
  // 只用 periodInMinutes，避免 when+period 在部分版本上行为怪异
  try {
    chrome.alarms.create(ALARM_PROBE, { periodInMinutes: m });
  } catch (_) {
    try {
      chrome.alarms.create(ALARM_PROBE, { periodInMinutes: OFFLINE_PROBE_MIN });
    } catch (_) {}
  }
}

/** 保证永远至少有一个周期闹钟；并排一次冷启动短延迟连接 */
function ensureAlarmsOnBoot() {
  try {
    chrome.alarms.getAll((list) => {
      if (chrome.runtime.lastError) {
        armOfflineProbe(OFFLINE_PROBE_MIN);
        armPing();
        scheduleBootConnect(2);
        return;
      }
      const names = new Set((list || []).map((a) => a.name));
      if (!names.has(ALARM_PING)) armPing();
      if (!names.has(ALARM_PROBE)) armOfflineProbe(offlineProbeMin || OFFLINE_PROBE_MIN);
      // 冷启动：2s 后连一次（不等下一次 30s～数分钟闹钟）
      scheduleBootConnect(2);
    });
  } catch (_) {
    armOfflineProbe(OFFLINE_PROBE_MIN);
    armPing();
    scheduleBootConnect(2);
  }
}

function scheduleBootConnect(seconds) {
  const sec = Math.max(1, Number(seconds) || 2);
  try {
    chrome.alarms.create(ALARM_BOOT, { when: Date.now() + sec * 1000 });
  } catch (_) {
    // alarms 不可用时尽力直接连（SW 可能很快被杀，仍好过不连）
    try {
      connectWS("boot-fallback");
    } catch (_) {}
  }
}

function socketIsOpen() {
  return !!(ws && ws.readyState === WebSocket.OPEN);
}

function noteRx() {
  lastRxAt = Date.now();
}

function tearDownSocket() {
  if (!ws) return;
  const old = ws;
  ws = null;
  try {
    old.onopen = old.onclose = old.onerror = old.onmessage = null;
    if (
      old.readyState === WebSocket.OPEN ||
      old.readyState === WebSocket.CONNECTING
    ) {
      old.close();
    }
  } catch (_) {}
}

function goOffline() {
  online = false;
  connecting = false;
  tearDownSocket();
  // 在线闹钟保留也可；离线时靠 PROBE。两者并存更抗节流。
  armOfflineProbe(offlineProbeMin);
}

function markOnline() {
  online = true;
  connecting = false;
  offlineProbeMin = OFFLINE_PROBE_MIN;
  noteRx();
  armPing();
  // 在线时仍保留低频 PROBE 作兜底（防止 PING 闹钟丢失后永不再连）
  armOfflineProbe(OFFLINE_PROBE_MAX_MIN);
}

/**
 * 发送应用层 PING。若 socket 已死或半开，转为重连，绝不静默 no-op。
 * 旧版 bug：online===true 且 readyState!==OPEN 时 sendPing 直接 return，
 * 闹钟空转数小时，插件表现为「挂了/报错」。
 */
function sendPingOrRepair(reason) {
  if (socketIsOpen()) {
    // 半开：TCP 看似 OPEN 但桌面早已不回
    if (lastRxAt > 0 && Date.now() - lastRxAt > STALE_RX_MS) {
      goOffline();
      connectWS(reason || "stale-rx");
      return;
    }
    try {
      ws.send("PING");
    } catch (_) {
      goOffline();
      connectWS(reason || "ping-throw");
    }
    return;
  }
  // 状态不一致：曾以为在线，或残留死 socket
  online = false;
  connecting = false;
  tearDownSocket();
  connectWS(reason || "repair");
}

/**
 * 连接桌面。仅由：闹钟 / 冷启动 / Alt+1 / 安装与浏览器启动 触发。
 * 失败后拉长探活间隔，不 tight-loop。
 */
function connectWS(reason) {
  try {
    if (connecting) {
      // 防卡死：connecting 超过超时仍未结束则强制清
      return;
    }
    if (socketIsOpen()) {
      sendPingOrRepair(reason || "already-open");
      return;
    }
    if (ws && ws.readyState === WebSocket.CONNECTING) return;

    connecting = true;
    const gen = ++connectGen;
    tearDownSocket();

    let sock;
    try {
      sock = new WebSocket(WS_URL);
    } catch (_) {
      connecting = false;
      online = false;
      offlineProbeMin = Math.min(OFFLINE_PROBE_MAX_MIN, offlineProbeMin * 2);
      armOfflineProbe(offlineProbeMin);
      return;
    }
    ws = sock;

    let timeoutId = null;
    const clearTO = () => {
      if (timeoutId != null) {
        try {
          clearTimeout(timeoutId);
        } catch (_) {}
        timeoutId = null;
      }
    };

    try {
      timeoutId = setTimeout(() => {
        if (gen !== connectGen) return;
        // 无论是否仍 CONNECTING：超时必须清 connecting，防止永久锁死
        if (sock.readyState === WebSocket.CONNECTING) {
          try {
            sock.close();
          } catch (_) {}
        }
        if (connecting && gen === connectGen && ws === sock) {
          connecting = false;
          online = false;
          try {
            if (ws === sock) ws = null;
          } catch (_) {}
          offlineProbeMin = Math.min(
            OFFLINE_PROBE_MAX_MIN,
            Math.max(OFFLINE_PROBE_MIN, offlineProbeMin * 1.5)
          );
          armOfflineProbe(offlineProbeMin);
        }
      }, CONNECT_TIMEOUT_MS);
    } catch (_) {}

    sock.onopen = () => {
      if (gen !== connectGen) return;
      clearTO();
      markOnline();
      try {
        sock.send("PING");
      } catch (_) {}
    };

    sock.onmessage = (e) => {
      if (gen !== connectGen) return;
      noteRx();
      const data = e && e.data;
      if (data === "ENABLE") saveEnabled(true);
      else if (data === "DISABLE") saveEnabled(false);
      // PONG / 其它帧：只刷新 lastRxAt
    };

    sock.onclose = () => {
      clearTO();
      // 过期代次：不改当前状态
      if (gen !== connectGen && ws !== sock) return;
      if (ws === sock) ws = null;
      const wasOnline = online;
      online = false;
      connecting = false;
      if (wasOnline) offlineProbeMin = OFFLINE_PROBE_MIN;
      else {
        offlineProbeMin = Math.min(
          OFFLINE_PROBE_MAX_MIN,
          Math.max(OFFLINE_PROBE_MIN, offlineProbeMin * 1.5)
        );
      }
      armOfflineProbe(offlineProbeMin);
      armPing(); // 保持 PING 闹钟，作二次兜底唤醒
    };

    // 不 console.error：浏览器已对 CONNECTION_REFUSED 打网络日志
    sock.onerror = () => {
      try {
        if (sock.readyState === WebSocket.OPEN || sock.readyState === WebSocket.CONNECTING) {
          sock.close();
        }
      } catch (_) {}
    };
  } catch (_) {
    connecting = false;
    online = false;
    try {
      armOfflineProbe(offlineProbeMin || OFFLINE_PROBE_MIN);
    } catch (_) {}
  }
}

chrome.alarms.onAlarm.addListener((alarm) => {
  try {
    if (!alarm || !alarm.name) return;
    if (alarm.name === ALARM_BOOT) {
      connectWS("boot");
      return;
    }
    if (alarm.name === ALARM_PING) {
      // 无论 online 标记如何，都以真实 socket 为准
      sendPingOrRepair("ping");
      return;
    }
    if (alarm.name === ALARM_PROBE) {
      if (!socketIsOpen()) connectWS("probe");
      else sendPingOrRepair("probe-alive");
    }
  } catch (_) {
    try {
      connecting = false;
      armOfflineProbe(OFFLINE_PROBE_MIN);
    } catch (_) {}
  }
});

try {
  chrome.runtime.onInstalled.addListener(() => {
    offlineProbeMin = OFFLINE_PROBE_MIN;
    connectWS("installed");
    armOfflineProbe(OFFLINE_PROBE_MIN);
    armPing();
  });
} catch (_) {}

try {
  chrome.runtime.onStartup.addListener(() => {
    offlineProbeMin = OFFLINE_PROBE_MIN;
    connectWS("browser-startup");
    armOfflineProbe(OFFLINE_PROBE_MIN);
    armPing();
  });
} catch (_) {}

// 每次 SW 脚本加载：恢复闹钟 + 短延迟重连
ensureAlarmsOnBoot();

// =============================================================================
// Alt+1
// =============================================================================
let _lastFire = 0;

chrome.commands.onCommand.addListener(async (cmd) => {
  try {
    if (cmd !== "save-hover-image") return;
    const now = Date.now();
    if (now - _lastFire < 400) return;
    _lastFire = now;

    // 用时再连（不等待结果）；顺带修半开
    if (!socketIsOpen()) connectWS("hotkey");
    else sendPingOrRepair("hotkey");

    if (online && !appEnabled) {
      console.log("[ImgSaver] 跳过：桌面已停用速存图片");
      return;
    }

    let tab = null;
    try {
      const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
      tab = tabs && tabs[0];
    } catch (e) {
      console.warn("[ImgSaver] tabs.query 失败", e && e.message ? e.message : e);
      return;
    }
    if (!tab?.id || !tab?.url) return;
    if (/^(chrome|edge|about|chrome-extension|devtools|view-source|chrome-search|chrome-error):/i.test(tab.url)) {
      return;
    }

    let info = null;
    try {
      const [res] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: findHoverImage,
      });
      info = res?.result || null;
    } catch (e) {
      console.warn("[ImgSaver] 注入失败", e && e.message ? e.message : e);
      return;
    }

    if (!info || !info.url) {
      console.log("[ImgSaver] 未找到图片（鼠标停在图上再按 Alt+1）");
      return;
    }

    // 在线时交给桌面程序下载（curl + Referer，支持 pixiv 等防盗链）
    if (socketIsOpen()) {
      try {
        ws.send("SAVE " + info.url);
        return;
      } catch (_) {
        // 降级浏览器下载
      }
    }

    try {
      const id = await saveImage(info);
      console.log("[ImgSaver] 已下载", id);
    } catch (e) {
      console.warn("[ImgSaver] 下载失败", e && e.message ? e.message : e);
    }
  } catch (e) {
    // 吞掉未处理异常，避免 SW 进入 chrome://extensions 的 Errors 红条
    console.warn("[ImgSaver] 命令处理异常", e && e.message ? e.message : e);
  }
});

// ── 找图 ───────────────────────────────────────────────────────────────────
// DeviantArt / 现代站点常见坑：
// 1) 透明 overlay / 按钮盖在 <img> 上 → :hover 最深层不是 IMG
// 2) closest("img") 只向上找祖先，遮罩是兄弟节点时会 miss
// 3) wixmp CDN URL 可能无常规扩展名尾缀、或中间夹 /v1/fill/
// 策略：沿 :hover 链向上搜「自身 / 子树最大图 / 背景图」，并识别 CDN host。
async function findHoverImage() {
  const abs = (u) => {
    try {
      return u ? new URL(u, location.href).href : null;
    } catch {
      return u;
    }
  };
  const looksImg = (u) => {
    const s = String(u || "");
    if (!s || s === "about:blank") return false;
    if (/^data:image\//i.test(s) || s.startsWith("blob:")) return true;
    if (/\.(png|jpe?g|webp|gif|bmp|tiff|avif|svg)(?=$|[?#/])/i.test(s)) return true;
    // DeviantArt / Wix 媒体、常见图床：路径不一定以扩展名结尾
    if (
      /(?:wixmp\.com|deviantart\.(?:net|com)|images-wixmp|img\.|i\.pximg\.net|pbs\.twimg\.com|pinimg\.com|imgur\.com|gelbooru|sankaku|danbooru)/i.test(
        s
      )
    ) {
      return true;
    }
    return false;
  };

  const fromSrcset = (img) => {
    const ss = img.getAttribute("srcset") || img.getAttribute("data-srcset");
    if (!ss) return null;
    const items = ss
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => {
        const m = s.match(/(\S+)\s+(\d+)(?:w|x)/i);
        return m ? { u: m[1], n: parseInt(m[2], 10) } : { u: s.split(/\s+/)[0], n: 1 };
      });
    items.sort((a, b) => b.n - a.n);
    return items.length ? items[0].u : null;
  };
  const fromData = (img) => {
    const keys = [
      "data-original",
      "data-actualsrc",
      "data-src",
      "data-large",
      "data-large-src",
      "data-hd",
      "data-big",
      "data-zoom-image",
      "data-image",
      "data-full",
      "data-super-full-img",
      "data-consistent-quality-image",
    ];
    for (const k of keys) {
      const v = img.getAttribute(k);
      if (looksImg(v)) return v;
    }
    // schema.org / DA: property="contentUrl"
    const prop = img.getAttribute("content") || img.getAttribute("property");
    if (prop === "contentUrl" && looksImg(img.getAttribute("src"))) {
      return img.getAttribute("src");
    }
    const a = img.closest && img.closest("a[href]");
    if (a && looksImg(a.href)) return a.href;
    return null;
  };

  const urlFromImg = (img) => {
    if (!img) return null;
    const raw =
      img.currentSrc ||
      img.getAttribute("src") ||
      fromSrcset(img) ||
      fromData(img) ||
      img.getAttribute("data-src") ||
      "";
    const u = abs(raw);
    return looksImg(u) || (u && /^https?:/i.test(u)) ? u : null;
  };

  const areaOf = (img) => {
    const w = img.clientWidth || img.naturalWidth || 0;
    const h = img.clientHeight || img.naturalHeight || 0;
    return w * h;
  };

  /** 在 root 子树里挑面积最大、且像作品图的 img（跳过极小图标） */
  const largestImgIn = (root, minArea = 80 * 80) => {
    if (!root || !root.querySelectorAll) return null;
    const list = root.querySelectorAll("img");
    let best = null;
    let bestA = 0;
    for (const im of list) {
      const u = urlFromImg(im);
      if (!u) continue;
      // 跳过明显是 avatar / emoji / 追踪像素
      const a = areaOf(im);
      if (a < minArea && (im.naturalWidth || 0) < 120) continue;
      const score = a || (im.naturalWidth || 0) * (im.naturalHeight || 0) || 1;
      // 主站作品 CDN 加权
      const bonus = /wixmp|deviantart\.(net|com)\/.*\.(png|jpe?g|webp)/i.test(u)
        ? 1e9
        : 0;
      if (score + bonus > bestA) {
        bestA = score + bonus;
        best = im;
      }
    }
    return best;
  };

  const bgUrl = (el) => {
    try {
      const bg = getComputedStyle(el).backgroundImage;
      const m = bg && bg.match(/url\(["']?(.*?)["']?\)/);
      if (m && m[1] && m[1] !== "none") {
        const u = abs(m[1]);
        if (looksImg(u) || /^https?:/i.test(u || "")) return u;
      }
    } catch (_) {}
    return null;
  };

  const finalize = async (url, alt) => {
    if (!url) return null;
    if (url.startsWith("blob:")) {
      try {
        const b = await fetch(url).then((r) => r.blob());
        const dataUrl = await new Promise((res, rej) => {
          const fr = new FileReader();
          fr.onload = () => res(fr.result);
          fr.onerror = rej;
          fr.readAsDataURL(b);
        });
        return { url: dataUrl, filename: alt || "" };
      } catch {
        return null;
      }
    }
    return { url, filename: alt || "" };
  };

  // ── 主路径：沿 :hover 链从里到外找 ─────────────────────────────────────
  const hov = Array.from(document.querySelectorAll(":hover"));
  // 从最深层开始
  for (let i = hov.length - 1; i >= 0; i--) {
    const el = hov[i];
    if (!el || !el.tagName) continue;

    if (el.tagName === "IMG") {
      const u = urlFromImg(el);
      if (u) return await finalize(u, el.getAttribute("alt"));
    }
    if (el.tagName === "PICTURE") {
      const im = el.querySelector("img");
      const u = urlFromImg(im);
      if (u) return await finalize(u, im && im.getAttribute("alt"));
    }
    if (el.tagName === "SOURCE" && el.parentElement) {
      const im = el.parentElement.querySelector("img");
      const u = urlFromImg(im) || abs(el.getAttribute("srcset") || "");
      if (u && looksImg(u)) return await finalize(u, "");
    }
    if (el.tagName === "VIDEO") {
      const u = abs(el.currentSrc || el.getAttribute("poster") || "");
      if (u && looksImg(u)) return await finalize(u, el.getAttribute("aria-label") || "video");
    }

    // 子树最大图（overlay 盖住 img 时，父级仍能 query 到）
    const childImg = largestImgIn(el, 60 * 60);
    if (childImg) {
      const u = urlFromImg(childImg);
      if (u) return await finalize(u, childImg.getAttribute("alt"));
    }

    const bu = bgUrl(el);
    if (bu) return await finalize(bu, el.getAttribute("aria-label") || "");
  }

  // 祖先链：hover 元素向上 10 层，每层再找最大图（兄弟 img / 舞台容器）
  let p = hov[hov.length - 1] || null;
  for (let d = 0; p && d < 12; d++, p = p.parentElement) {
    if (p.tagName === "IMG") {
      const u = urlFromImg(p);
      if (u) return await finalize(u, p.getAttribute("alt"));
    }
    const im = largestImgIn(p, 100 * 100);
    if (im) {
      const u = urlFromImg(im);
      if (u) return await finalize(u, im.getAttribute("alt"));
    }
    const bu = bgUrl(p);
    if (bu) return await finalize(bu, p.getAttribute("aria-label") || "");
  }

  // DeviantArt 作品页兜底：主舞台大图（鼠标在侧栏按钮上时也能抓到主图）
  try {
    if (/(^|\.)deviantart\.com$/i.test(location.hostname)) {
      const cands = document.querySelectorAll(
        'img[src*="wixmp"], img[src*="deviantart.net"], img[property="contentUrl"], img[src*="/v1/fill/"]'
      );
      let best = null;
      let bestA = 0;
      for (const im of cands) {
        const a = areaOf(im);
        if (a > bestA) {
          bestA = a;
          best = im;
        }
      }
      if (best && bestA >= 120 * 120) {
        const u = urlFromImg(best);
        if (u) return await finalize(u, best.getAttribute("alt"));
      }
    }
  } catch (_) {}

  return null;
}

// ── 下载 ───────────────────────────────────────────────────────────────────
function sanitize(s) {
  return String(s || "")
    .replace(/[\\/:*?"<>|]+/g, "_")
    .replace(/\s+/g, " ")
    .trim();
}

const pendingNames = new Map();
const ourIds = new Set();
let _armed = false;

chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  try {
    let key = pendingNames.has(item.url)
      ? item.url
      : pendingNames.has(item.finalUrl)
        ? item.finalUrl
        : null;
    const mine = key !== null || ourIds.has(item.id) || _armed;
    if (!mine) {
      suggest();
      return;
    }
    _armed = false;
    const desired = key !== null ? pendingNames.get(key) : null;
    if (key !== null) pendingNames.delete(key);
    ourIds.add(item.id);
    const chosen =
      desired || String(item.filename || "image").split(/[\\/]/).pop() || "image";
    suggest({ filename: `${STAGING_SUBDIR}/${chosen}`, conflictAction: "uniquify" });
  } catch (_) {
    try {
      suggest();
    } catch (_) {}
  }
});

async function saveImage(info) {
  const url = info.url;
  let desired = null;
  if (/^data:/i.test(url)) {
    const mime = ((url.match(/^data:([^;,]+)/i) || [])[1] || "").toLowerCase();
    const ext =
      {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
        "image/bmp": "bmp",
      }[mime] || "png";
    desired = `${sanitize(info.filename) || "image"}.${ext}`;
  }
  pendingNames.set(url, desired);
  _armed = true;
  try {
    const id = await chrome.downloads.download({ url, saveAs: false });
    ourIds.add(id);
    eraseWhenDone(id);
    // 防止 pendingNames 泄漏：30s 后清掉未消费条目
    setTimeout(() => {
      try {
        pendingNames.delete(url);
      } catch (_) {}
    }, 30000);
    return id;
  } catch (e) {
    pendingNames.delete(url);
    _armed = false;
    throw e;
  }
}

function eraseWhenDone(id) {
  const onChanged = (d) => {
    if (d.id !== id) return;
    const st = d.state && d.state.current;
    if (st === "complete" || st === "interrupted") {
      try {
        chrome.downloads.onChanged.removeListener(onChanged);
      } catch (_) {}
      ourIds.delete(id);
      if (st === "complete") {
        try {
          chrome.downloads.erase({ id }).catch(() => {});
        } catch (_) {}
      }
    }
  };
  try {
    chrome.downloads.onChanged.addListener(onChanged);
  } catch (_) {}
  // 兜底：2 分钟后移除监听，防泄漏
  setTimeout(() => {
    try {
      chrome.downloads.onChanged.removeListener(onChanged);
    } catch (_) {}
    ourIds.delete(id);
  }, 120000);
}

// ── Pixiv Referer 注入 ──────────────────────────────────────────────────────
// pixiv 图片 CDN (i.pximg.net) 校验 Referer，chrome.downloads.download() 不带，
// 通过 declarativeNetRequest 在网络层自动追加。只匹配 i.pximg.net 的请求。
(function () {
  try {
    chrome.declarativeNetRequest.updateDynamicRules(
      {
        removeRuleIds: [100],
        addRules: [
          {
            id: 100,
            priority: 1,
            action: {
              type: "modifyHeaders",
              requestHeaders: [
                {
                  header: "referer",
                  operation: "set",
                  value: "https://www.pixiv.net/",
                },
              ],
            },
            condition: { urlFilter: "i.pximg.net", resourceTypes: ["image", "xmlhttprequest", "other"] },
          },
        ],
      },
      () => {
        if (chrome.runtime.lastError) {
          console.warn(
            "[ImgSaver] declarativeNetRequest:",
            chrome.runtime.lastError.message
          );
        }
      }
    );
  } catch (e) {
    console.warn("[ImgSaver] declarativeNetRequest 注册失败", e && e.message ? e.message : e);
  }
})();
