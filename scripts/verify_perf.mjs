import { chromium } from "playwright";
import fs from "fs";

const OUT = "scripts/_verify_shots";
fs.mkdirSync(OUT, { recursive: true });
const BASE = "http://localhost:8000/performance.html";

const viewports = [
  { name: "1440", width: 1440, height: 1000 },
  { name: "390",  width: 390,  height: 844 },
];
const markets = ["IN", "US"];

const results = [];
const browser = await chromium.launch();

for (const vp of viewports) {
  for (const mkt of markets) {
    const ctx = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: 1,
    });
    const page = await ctx.newPage();
    // Preset market before any script runs so first render is correct.
    await page.addInitScript((m) => {
      try { localStorage.setItem("breakoutai_market", m); } catch (e) {}
    }, mkt);

    const consoleErrors = [];
    page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
    page.on("pageerror", (e) => consoleErrors.push("PAGEERROR: " + e.message));

    const resp = await page.goto(BASE, { waitUntil: "networkidle" });
    await page.waitForTimeout(800);

    // Measure default-state full document height (1b target: < 4000px).
    const fullHeight = await page.evaluate(() => document.documentElement.scrollHeight);

    // 1b: how many call rows are in the DOM at default state (should be paged).
    const rowCount = await page.evaluate(() => document.querySelectorAll("#list [data-sym]").length);
    const loadMore = await page.evaluate(() => {
      const b = document.getElementById("loadMoreBtn");
      return b ? b.innerText.replace(/\s+/g, " ").trim() : null;
    });
    const countLabel = await page.evaluate(() => {
      const c = document.getElementById("countLabel");
      return c ? c.innerText.trim() : null;
    });

    // 1a: conviction badges rendered on cards + presence of honesty badge text.
    const convBadges = await page.evaluate(() =>
      document.querySelectorAll('#list [title^="Conviction score at the time"]').length);
    const honestyBadges = await page.evaluate(() =>
      (document.body.innerText.match(/unproven live/g) || []).length);

    const shot = `${OUT}/perf_${vp.name}_${mkt}.png`;
    await page.screenshot({ path: shot, fullPage: false });

    results.push({
      vp: vp.name, mkt, status: resp.status(), fullHeight, rowCount,
      countLabel, loadMore, convBadges, honestyBadges,
      consoleErrors: consoleErrors.slice(0, 5),
    });

    // Second screenshot: click load-more once (1440/IN only) to prove it grows.
    if (vp.name === "1440" && mkt === "IN" && loadMore) {
      const before = rowCount;
      await page.click("#loadMoreBtn");
      await page.waitForTimeout(400);
      const after = await page.evaluate(() => document.querySelectorAll("#list [data-sym]").length);
      results[results.length - 1].loadMoreGrew = `${before} -> ${after}`;
    }

    await ctx.close();
  }
}

await browser.close();
console.log(JSON.stringify(results, null, 2));
