import { readFile } from "node:fs/promises";
import { type APIRequestContext, expect, test } from "@playwright/test";
import { record } from "../src/shared/api/decode";

async function result(request: APIRequestContext) {
  const response = await request.post("/api/ui/jobs", {
    multipart: {
      file: {
        name: "synthetic.mov",
        mimeType: "video/quicktime",
        buffer: await readFile("e2e/fixtures/violation.mov"),
      },
    },
  });
  expect(response.status()).toBe(200);
  const { job_id } = await response.json();
  let view: Record<string, unknown> = {};
  await expect
    .poll(async () => {
      view = await (await request.get(`/api/ui/jobs/${job_id}`)).json();
      return view.status;
    })
    .toBe("done");
  return { id: String(job_id), view };
}
test("real backend: event, playable video, evidence and honest missing norm", async ({
  page,
  request,
}) => {
  const { id } = await result(request);
  await page.goto(`/?job=${id}`);
  await expect(
    page.getByRole("heading", { name: "Обнаружены признаки возможных нарушений", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".event-card")).toHaveCount(1);
  await expect(page.getByText("Норма не указана")).toBeVisible();
  const video = page.locator("video");
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThan(0);
  await page.locator(".timecode-button").click();
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime))
    .toBeGreaterThan(0);
  await expect(page.locator(".evidence img").first()).toBeVisible();
  await page.locator(".evidence").first().click();
  await expect(page.getByRole("button", { name: "Закрыть", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".evidence").first()).toBeFocused();
});
test("documented fixture: multiple events, four-second context, sources and stopped polling", async ({
  page,
  request,
}, testInfo) => {
  const { id, view } = await result(request);
  const original = record((view.violations as unknown[])[0]);
  const legal = [
    {
      act_title_ru: "Тестовый источник для проверки интерфейса",
      clause: "Тестовый пункт",
      quote_ru: "Синтетическая цитата. Не является нормативным основанием.",
      url: "https://adilet.zan.kz/rus/docs/V1900019680",
      strength: "indirect",
    },
  ];
  const fixture = {
    ...view,
    violations: [
      { ...original, id: "v0", title_ru: "Синтетическое событие 1", timecode_sec: 3, legal },
      { ...original, id: "v1", title_ru: "Синтетическое событие 2", timecode_sec: 8, legal },
    ],
    signals: [
      {
        id: "s0",
        title_ru: "Синтетическое наблюдение",
        t_sec: 10,
        description_ru: "Только тест UI",
        evidence: [],
      },
    ],
    degradations: [],
    result_level: "full",
  };
  let polls = 0;
  await page.route(`**/api/ui/jobs/${id}`, (route) => {
    polls++;
    return route.fulfill({ json: fixture });
  });
  await page.goto(`/?job=${id}`);
  await expect(page.locator(".event-card")).toHaveCount(2);
  await expect(page.locator(".event-count")).toHaveText("2");
  await page.locator(".context-button").first().click();
  const video = page.locator("video");
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 8000 })
    .toBeGreaterThanOrEqual(5);
  expect(await video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeLessThan(5.4);
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  await page.locator(".legal-list summary").first().click();
  await expect(page.getByText("Тестовый источник для проверки интерфейса").first()).toBeVisible();
  await expect(page.getByRole("link", { name: /Открыть источник/ }).first()).toHaveAttribute(
    "rel",
    "noopener noreferrer",
  );
  expect(polls).toBe(1);
  await page.screenshot({ path: testInfo.outputPath("result.png"), fullPage: true });
  await page.reload();
  await expect(page.locator(".event-card")).toHaveCount(2);
  expect(polls).toBe(2);
});
test("mobile layout, navigation and empty catalog", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Проверка видео" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("upload.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("link", { name: "Типы и нормы", exact: true }).click();
  await expect(page.getByText("Каталог пока не предоставлен сервером.")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Проверка видео" })).toBeVisible();
});
