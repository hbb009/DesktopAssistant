// content.js — 职责：找光标下的图片，供 background.js 调用
(() => {
    const KEY = "__hover_image_saver_" + chrome.runtime.id;
    if (window[KEY]) return;
    window[KEY] = true;

    // 持续追踪鼠标位置
    let lastXY = { x: 0, y: 0 };
    window.addEventListener("mousemove", e => {
        lastXY = { x: e.clientX, y: e.clientY };
    }, { passive: true });

    // 从 srcset 取最高分辨率 URL
    function pickFromSrcset(img) {
        const items = (img.getAttribute("srcset") || "")
            .split(",").map(s => s.trim()).filter(Boolean)
            .map(s => {
                const m = s.match(/(\S+)\s+(\d+)(w|x)/i);
                return m ? { url: m[1], n: parseInt(m[2]), kind: m[3].toLowerCase() }
                         : { url: s, n: 1, kind: "x" };
            });
        if (!items.length) return null;
        items.sort((a, b) =>
            (a.kind === "w" ? 0 : 1) - (b.kind === "w" ? 0 : 1) || b.n - a.n
        );
        return items[0].url;
    }

    // 从 data-* 属性猜原图 URL
    function pickFromAttrs(img) {
        const looksImg = u => /\.(png|jpe?g|webp|gif|bmp|tiff|svg|avif)(?:$|\?)/i.test(String(u || ""));
        const keys = ["data-src","data-original","data-origin","data-large","data-large-src",
                      "data-zoom-image","data-image","data-full","data-url","data-hd","data-big"];
        const cand = [];
        for (const k of keys) {
            const v = img.getAttribute(k);
            if (looksImg(v)) cand.push(v);
        }
        const a = img.closest?.("a[href]");
        if (a && looksImg(a.href)) cand.push(a.href);
        const score = u => {
            let sc = 0;
            if (/orig|original|master|full|large|hd/.test(String(u).toLowerCase())) sc += 10;
            const nums = (String(u).match(/\d{3,5}/g) || []).map(Number);
            if (nums.length) sc += Math.max(...nums) / 1000;
            return sc;
        };
        cand.sort((a, b) => score(b) - score(a));
        return cand[0] || null;
    }

    // 找光标下的图片
    async function pickImageUnderCursor() {
        const el = document.elementFromPoint(lastXY.x, lastXY.y);
        if (!el) return null;

        if (el.tagName === "IMG" || el.tagName === "PICTURE") {
            const img = el.tagName === "PICTURE" ? el.querySelector("img") : el;
            if (!img) return null;
            const url = pickFromSrcset(img) || pickFromAttrs(img)
                     || img.getAttribute("src") || img.currentSrc;
            return url ? { kind: "url", url, filename: img.alt || "image" } : null;
        }

        // CSS background-image
        const bg = getComputedStyle(el).backgroundImage;
        const m  = bg?.match(/url\(["']?(.*?)["']?\)/);
        if (m?.[1]) return { kind: "url", url: m[1], filename: el.getAttribute("aria-label") || "image" };

        // canvas
        if (el.tagName === "CANVAS") {
            try { return { kind: "data", dataUrl: el.toDataURL("image/png"), filename: "canvas" }; } catch {}
        }

        // blob URL
        if (el.src?.startsWith("blob:")) {
            try {
                const blob = await (await fetch(el.src)).blob();
                const dataUrl = await new Promise(r => {
                    const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(blob);
                });
                return { kind: "data", dataUrl, filename: el.alt || "image" };
            } catch {}
        }

        return null;
    }

    // background 来取图片信息
    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
        if (msg?.type === "GET_HOVER_IMAGE") {
            pickImageUnderCursor().then(sendResponse);
            return true;
        }
    });
})();
