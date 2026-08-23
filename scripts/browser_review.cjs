const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

async function main() {
  const [browserBinary, reportUrl, outputDirectory] = process.argv.slice(2);
  if (!browserBinary || !reportUrl || !outputDirectory) {
    throw new Error("usage: browser_review.cjs BROWSER URL OUTPUT_DIRECTORY");
  }
  fs.mkdirSync(outputDirectory, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath: browserBinary,
  });
  const checks = [];
  try {
    for (const viewport of [
      { name: "desktop", width: 1440, height: 1000, isMobile: false },
      { name: "mobile", width: 390, height: 844, isMobile: true },
    ]) {
      const context = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
        deviceScaleFactor: 1,
        isMobile: viewport.isMobile,
      });
      const page = await context.newPage();
      await page.goto(reportUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
      await page.waitForTimeout(300);
      const metrics = await page.evaluate(() => {
        const root = document.documentElement;
        const allowedScrollContainer = (element) => element.closest(
          "table, .reading-paths, p:has(> img[src$='.svg'])",
        );
        const offenders = [...document.querySelectorAll("body *")]
          .filter((element) => {
            const rect = element.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return false;
            if (rect.left >= -1 && rect.right <= root.clientWidth + 1) return false;
            return !allowedScrollContainer(element);
          })
          .slice(0, 12)
          .map((element) => ({
            tag: element.tagName.toLowerCase(),
            className: typeof element.className === "string" ? element.className : "",
            left: Math.round(element.getBoundingClientRect().left),
            right: Math.round(element.getBoundingClientRect().right),
          }));
        return {
          title: document.title,
          textLength: (document.querySelector("main")?.innerText || "").trim().length,
          clientWidth: root.clientWidth,
          scrollWidth: root.scrollWidth,
          globalOverflow: root.scrollWidth > root.clientWidth + 1,
          offenders,
        };
      });
      const screenshot = path.join(outputDirectory, `html-review-${viewport.name}.png`);
      await page.screenshot({ path: screenshot, fullPage: false });
      checks.push({
        viewport: viewport.name,
        requestedSize: [viewport.width, viewport.height],
        screenshot,
        metrics,
      });
      await context.close();
    }
  } finally {
    await browser.close();
  }
  process.stdout.write(`${JSON.stringify({ checks })}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
