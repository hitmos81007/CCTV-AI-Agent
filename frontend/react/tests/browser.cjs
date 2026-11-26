const { chromium } = require("@playwright/test");
const { parse } = require("dotenv");
const fs = require("node:fs");
const path = require("node:path");

(async () => {
  const env = process.env.PHASE3B_ISOLATED
    ? {REVIEW_USERNAME:"isolated-browser-test", REVIEW_PASSWORD:"isolated-test-password"}
    : parse(fs.readFileSync(path.resolve("../../.env")));
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const screenshots = path.resolve("../../work/phase3b-screenshots", process.env.PHASE3B_ISOLATED ? "isolated" : "live-read-only");
  const frontendUrl = process.env.PHASE3B_FRONTEND_URL || "http://127.0.0.1:5174";
  const backendUrl = process.env.PHASE3B_BACKEND_URL || "http://127.0.0.1:8011";
  if (process.env.PHASE3B_ISOLATED && (backendUrl !== "http://127.0.0.1:8012" || frontendUrl !== "http://127.0.0.1:5175")) {
    throw new Error("Review mutation is permitted only on the isolated test servers at ports 8012 and 5175.");
  }
  fs.mkdirSync(screenshots, { recursive: true });
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const login = async () => {
    await page.getByLabel("Username").fill(env.REVIEW_USERNAME);
    await page.getByLabel("Password").fill(env.REVIEW_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.getByRole("button", { name: "Sign in" }).waitFor({ state: "hidden", timeout: 30000 });
  };
  try {
    await page.goto(frontendUrl + "/");
    await page.getByRole("heading", { name: "Investigation Network" }).waitFor();
    console.log("PASS 1 React opened in isolated Chromium");
    await login();
    await page.getByRole("heading", { name: "Recent investigations" }).waitFor();
    await page.getByText("Loading cases…").waitFor({ state: "hidden", timeout: 30000 });
    assert(await page.locator('a[href^="/cases/"]').count() > 0, "No registered cases loaded on dashboard");
    await page.screenshot({ path: path.join(screenshots, "dashboard.png") });
    console.log("PASS 2 authenticated and loaded case dashboard");
    const id = "BROWSER-" + Date.now();
    await page.getByRole("link", { name: "Create case" }).click();
    await page.getByLabel("Case ID").fill(id);
    await page.getByLabel("Incident description").fill("Fictional vehicle theft for browser verification");
    await page.getByLabel("Vehicle type").fill("car");
    await page.getByLabel("Vehicle color").fill("blue");
    await page.getByRole("button", { name: "Create case" }).click();
    await page.getByRole("heading", { name: id, exact: true }).waitFor({ timeout: 30000 });
    console.log("PASS 3 case created through React form");
    await page.reload();
    await login();
    await page.getByRole("heading", { name: id, exact: true }).waitFor({ timeout: 30000 });
    console.log("PASS 4 case persisted after page refresh and sign-in");
    await page.getByRole("heading", { name: "Jurisdiction map" }).scrollIntoViewIfNeeded();
    await page.getByText("Zone-1", { exact: true }).waitFor({ timeout: 30000 });
    for (let i = 1; i <= 6; i++) assert(await page.getByText("Zone-" + i, { exact: true }).count() > 0, "Missing jurisdiction");
    await page.screenshot({ path: path.join(screenshots, "jurisdictions.png") });
    console.log("PASS 5 six configured jurisdictions displayed");
    await page.getByRole("link", { name: "All investigations" }).click();
    await page.getByRole("heading", { name: "Recent investigations" }).waitFor();
    const auth = "Basic " + Buffer.from(env.REVIEW_USERNAME + ":" + env.REVIEW_PASSWORD).toString("base64");
    const response = await context.request.get(backendUrl + "/api/v1/cases", { headers: { Authorization: auth } });
    const cases = await response.json();
    let selected = null;
    for (const item of cases) {
      const rec = await context.request.get(backendUrl + "/api/v1/recordings?case_id=" + encodeURIComponent(item.case_id), { headers: { Authorization: auth } });
      if (!rec.ok() || !(await rec.json()).items.length) continue;
      const sightings = await context.request.get(backendUrl + "/api/v1/sightings?case_id=" + encodeURIComponent(item.case_id), { headers: { Authorization: auth } });
      const rows = await sightings.json();
      if (rows.slice(0, 12).some(s => s.frame_url)) { selected = item.case_id; break; }
    }
    assert(selected, "No existing case with recordings and a visible evidence image");
    await page.getByRole("link", { name: new RegExp(selected.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) }).first().click();
    await page.getByRole("heading", { name: "Recordings", exact: true }).waitFor();
    await page.locator("#recordings .row").first().waitFor({ timeout: 30000 });
    assert(await page.locator("#recordings .row").count() > 0, "Recordings missing");
    console.log("PASS 6 registered recordings rendered");
    const image = page.getByAltText("Evidence frame").first();
    await image.waitFor({ timeout: 30000 });
    assert(await image.evaluate(el => el.complete && el.naturalWidth > 0), "Evidence image failed to decode");
    await image.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(screenshots, "evidence.png") });
    console.log("PASS 7 authenticated existing evidence image rendered");
    if (process.env.PHASE3B_ISOLATED) {
      const card = image.locator("xpath=ancestor::article");
      const sighting = await card.getAttribute("data-sighting-id");
      await page.getByLabel("Reviewer name").fill("Browser verification");
      await page.getByLabel("Review note").fill("UI verification test only");
      page.once("dialog", dialog => dialog.accept());
      await card.getByRole("button", { name: "Reject" }).click();
      await card.getByText("Review saved.").waitFor({ timeout: 30000 });
      const verify = await context.request.get(backendUrl + "/api/v1/sightings?case_id=" + encodeURIComponent(selected) + "&all_detections=true", { headers: { Authorization: auth } });
      assert((await verify.json()).find(s => s.id === sighting)?.review_status === "not_relevant", "Review was not persisted");
      await card.getByRole("button", { name: "Review history" }).click();
      await card.getByText("UI verification test only", { exact: false }).first().waitFor({ timeout: 30000 });
      await page.screenshot({ path: path.join(screenshots, "review.png") });
      console.log("PASS 8 review decision saved in isolated database via React and confirmed by API");
    } else {
      console.log("NOT RUN 8 review mutation against live evidence; isolated copy will be used");
    }
    await page.getByRole("link", { name: "All investigations" }).click();
    await page.getByRole("link", { name: "Create case" }).click();
    await page.getByRole("heading", { name: "Create investigation" }).waitFor();
    console.log("PASS 9 navigation completed");
    const isolated = await browser.newPage();
    await isolated.goto(frontendUrl + "/");
    await isolated.getByLabel("Username").fill("invalid-test-user");
    await isolated.getByLabel("Password").fill("invalid-test-password");
    await isolated.getByRole("button", { name: "Sign in" }).click();
    await isolated.getByRole("alert").getByText("Incorrect username or password.").waitFor();
    await isolated.close();
    const offline = await browser.newPage();
    await offline.route("**/api/v1/**", route => route.abort());
    await offline.goto(frontendUrl + "/");
    await offline.getByLabel("Username").fill("offline-test");
    await offline.getByLabel("Password").fill("offline-test");
    await offline.getByRole("button", { name: "Sign in" }).click();
    await offline.getByText("Cannot reach the backend. Check that it is running and allows this frontend origin.").waitFor();
    await offline.close();
    console.log("PASS 10 invalid login and unavailable backend displayed errors");
    console.log("Screenshots saved in", screenshots);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
