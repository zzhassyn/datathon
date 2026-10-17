import { readFile } from "node:fs/promises";
import { type APIRequestContext, expect, test } from "@playwright/test";

async function sample(request: APIRequestContext) {
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
  const example = JSON.parse(
    await readFile("../tests/fixtures/ui/08_diagnostics_demo.json", "utf8"),
  );
  let view = example;
  await expect
    .poll(async () => {
      view = await (await request.get(`/api/ui/jobs/${job_id}`)).json();
      return view.status;
    })
    .toBe("done");
  view.violations[0].explanation = example.violations[0].explanation;
  view.visibility = example.visibility;
  return { id: String(job_id), view };
}

test("synthetic diagnostics: conditions, evidence seek, visibility and reload", async ({
  page,
  request,
}, testInfo) => {
  const { id, view } = await sample(request);
  await page.route(`**/api/ui/jobs/${id}`, (route) => route.fulfill({ json: view }));
  await page.goto(`/?job=${id}`);
  const explanation = page.locator(".rule-explanation");
  await expect(explanation.locator(".diagnostic-body")).toBeHidden();
  await explanation.locator("summary").focus();
  await page.keyboard.press("Enter");
  await expect(explanation.getByText("Подтверждено", { exact: true })).toBeVisible();
  await expect(explanation.getByText("Опровергнуто", { exact: true })).toBeVisible();
  await expect(explanation.getByText("Неизвестно", { exact: true })).toBeVisible();
  await expect(explanation.locator(".condition-unknown button")).toHaveCount(0);
  const video = page.locator(".player video");
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThan(0);
  await video.evaluate((v: HTMLVideoElement) => {
    v.pause();
    v.currentTime = 6;
  });
  await explanation
    .getByRole("button", { name: /Показать момент/ })
    .first()
    .click();
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeLessThan(2);
  await video.evaluate((v: HTMLVideoElement) => v.pause());
  const visibility = page.locator(".visibility-panel");
  await visibility.locator("summary").click();
  await expect(visibility.getByText("Ограничена", { exact: true })).toBeVisible();
  await expect(visibility.getByText("Без привязки ко времени")).toBeVisible();
  await visibility
    .getByRole("button", { name: /Показать начало интервала: Мелкие детали/ })
    .click();
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime))
    .toBeGreaterThanOrEqual(1);
  await video.evaluate((v: HTMLVideoElement) => v.pause());
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("diagnostics.png"), fullPage: true });
  await page.reload();
  await page.locator(".rule-explanation summary").click();
  await expect(
    page.locator(".rule-explanation").getByText("Подтверждено", { exact: true }),
  ).toBeVisible();
});

test("clean result retains visibility; legacy result never invents assessment", async ({
  page,
  request,
}) => {
  const { id, view } = await sample(request);
  view.verdict = "no_violation";
  view.violations = [];
  await page.route(`**/api/ui/jobs/${id}`, (route) => route.fulfill({ json: view }));
  await page.goto(`/?job=${id}`);
  await page.locator(".visibility-panel summary").click();
  await expect(
    page.getByRole("heading", { name: "Нарушение не обнаружено", exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".visibility-panel").getByText("Ограничена", { exact: true }),
  ).toBeVisible();
  delete view.visibility;
  await page.reload();
  await page.locator(".visibility-panel summary").click();
  await expect(page.getByText("Оценка видимости не предоставлена.")).toBeVisible();
  await expect(page.locator(".visibility-panel .diagnostic-status")).toHaveCount(0);
});

test("invalid diagnostics never turn into a confirmed finding", async ({ page, request }) => {
  const { id, view } = await sample(request);
  view.violations[0].explanation.conditions[0].evidence_indices = [99];
  await page.route(`**/api/ui/jobs/${id}`, (route) => route.fulfill({ json: view }));
  await page.goto(`/?job=${id}`);
  await expect(
    page.getByText("Сервер вернул некорректный результат. Анализ не подтверждён."),
  ).toBeVisible({ timeout: 15000 });
  await expect(page.locator(".condition-met")).toHaveCount(0);
});
