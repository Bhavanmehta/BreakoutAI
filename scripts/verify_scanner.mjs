import { chromium } from "playwright";
import fs from "fs";

const OUT = "scripts/_verify_shots";
fs.mkdirSync(OUT, { recursive: true });
const BASE = "http://localhost:8000/combined_breakout_scanner_platform.html";

const viewports = [
  { name: "1440", width: 1440, height: 1000 },
  { name: "390",  width: 390,  height: 844 },
];
const markets = ["IN", "US"];

const results = [];
const browser = await chromium.launch();

for (const vp of viewports) {
  for (const mkt of markets) {
    const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
    const page = await ctx.newPage();
    await page.addInitScript((m) => { try { localStorage.setItem("breakoutai_market", m); } catch (e) {} }, mkt);

    const consoleErrors = [];
    const failed404 = [];
    page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
    page.on("pageerror", (e) => consoleErrors.push("PAGEERROR: " + e.message));
    page.on("response", (r) => { if (r.status() === 404) failed404.push(r.url().split("/").pop()); });

    const resp = await page.goto(BASE, { waitUntil: "networkidle" });
    await page.waitForTimeout(1200);

    // Count conviction score badges and the honesty layer badges on cards.
    const stats = await page.evaluate(() => {
      const txt = document.body.innerText;
      const unproven = (txt.match(/unproven live/g) || []).length;
      // bucket hit-rate honesty badges say "live" with a W/L in title; count live-record joins
      const liveBadges = document.querySelectorAll('[title*="resolved live call"], [title*="live hit-rate"], [title*="Live forward record"]').length;
      const cards = document.querySelectorAll("[data-sym], .stock-card, [class*='card']").length;
      return { unproven, liveBadges, cards, sample: txt.slice(0, 0) };
    });

    const shot = `${OUT}/scanner_${vp.name}_${mkt}.png`;
    await page.screenshot({ path: shot, fullPage: false });

    results.push({
      vp: vp.name, mkt, status: resp.status(),
      ...stats,
      failed404: [...new Set(failed404)],
      consoleErrors: consoleErrors.slice(0, 4),
    });
    await ctx.close();
  }
}

await browser.close();
console.log(JSON.stringify(results, null, 2));
