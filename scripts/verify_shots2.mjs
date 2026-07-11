import { chromium } from "playwright";
const browser = await chromium.launch();

// Mobile scanner: scroll to the watchlist cards and prove the mobile card badge renders.
{
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  await page.addInitScript(() => { try { localStorage.setItem("breakoutai_market", "IN"); } catch(e){} });
  await page.goto("http://localhost:8000/combined_breakout_scanner_platform.html", { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  const el = await page.$("[data-sym]");
  if (el) await el.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await page.screenshot({ path: "scripts/_verify_shots/scanner_390_IN_cards.png" });
  await ctx.close();
}

// Performance page: prove the paged default state (short) at desktop.
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  await page.addInitScript(() => { try { localStorage.setItem("breakoutai_market", "IN"); } catch(e){} });
  await page.goto("http://localhost:8000/performance.html", { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  // scroll to the bottom of the list to capture the honest load-more control
  await page.evaluate(() => { const b = document.getElementById("loadMoreBtn"); if (b) b.scrollIntoView(); });
  await page.waitForTimeout(300);
  await page.screenshot({ path: "scripts/_verify_shots/perf_1440_IN_loadmore.png" });
  await ctx.close();
}

await browser.close();
console.log("done");
