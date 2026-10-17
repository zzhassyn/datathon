import { expect, test } from "@playwright/test";

const id = "f".repeat(32);
const endpoint = `**/api/ui/jobs/${id}`;
const base = {
  job_id: id,
  status: "running",
  created_at: "2026-10-17T00:00:00Z",
  elapsed_sec: 1,
  queue_position: 0,
  eta_sec: 3,
  provisional: false,
  perception_level: "full",
  result_level: null,
  stages: [{ code: "perception", status: "running", progress: 0.5, note_ru: null }],
  degradations: [],
  video: null,
  verdict: null,
  violations: [],
  signals: [],
  error: null,
};
test("provisional does not become final until server completes", async ({ page }) => {
  let final = false;
  let polls = 0;
  await page.route(endpoint, (route) => {
    polls++;
    return route.fulfill({
      json: {
        ...base,
        status: final ? "done" : "running",
        verdict: "no_violation",
        provisional: !final,
        result_level: final ? "rules" : null,
      },
    });
  });
  await page.goto(`/?job=${id}`);
  await expect(
    page.getByRole("heading", { name: "Предварительный результат", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Нарушение не обнаружено", exact: true }),
  ).toHaveCount(0);
  final = true;
  await expect(
    page.getByRole("heading", { name: "Нарушение не обнаружено", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Упрощённая проверка")).toBeVisible();
  expect(polls).toBeGreaterThan(1);
});
test("failed and malformed done responses never display a clean verdict", async ({ page }) => {
  await page.route(endpoint, (route) =>
    route.fulfill({
      json: {
        ...base,
        status: "failed",
        error: { code: "PIPELINE_CRASH", message_ru: "Синтетический сбой модели" },
      },
    }),
  );
  await page.goto(`/?job=${id}`);
  await expect(page.getByRole("heading", { name: "Анализ не выполнен" })).toBeVisible();
  await expect(page.locator(".event-card")).toHaveCount(0);
  await page.unroute(endpoint);
  await page.route(endpoint, (route) =>
    route.fulfill({ json: { ...base, status: "done", verdict: null } }),
  );
  await page.reload();
  await expect(
    page.getByText("Сервер вернул некорректный результат. Анализ не подтверждён."),
  ).toBeVisible({ timeout: 15000 });
  await expect(
    page.getByRole("heading", { name: "Нарушение не обнаружено", exact: true }),
  ).toHaveCount(0);
});
test("polling recovers after network failure and preserves the job", async ({ page }) => {
  let calls = 0;
  await page.route(endpoint, (route) => {
    calls++;
    if (calls === 2 || calls === 3) return route.abort("internetdisconnected");
    return route.fulfill({
      json: {
        ...base,
        status: calls > 3 ? "done" : "running",
        verdict: calls > 3 ? "no_violation" : null,
      },
    });
  });
  await page.goto(`/?job=${id}`);
  await expect(
    page.getByRole("heading", { name: "Идёт анализ", exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Нарушение не обнаружено", exact: true }),
  ).toBeVisible({ timeout: 15000 });
  expect(calls).toBeGreaterThanOrEqual(4);
  await expect(page).toHaveURL(`/?job=${id}`);
});
test("404 stops polling and 401 supports a session retry", async ({ page }) => {
  let calls = 0;
  let allowed = false;
  await page.route(endpoint, (route) => {
    calls++;
    return route.fulfill({
      status: allowed ? 200 : 404,
      json: allowed ? { ...base, status: "done", verdict: "no_violation" } : { detail: "missing" },
    });
  });
  await page.goto(`/?job=${id}`);
  await expect(page.getByText("Проверка не найдена. Возможно, срок хранения истёк.")).toBeVisible();
  expect(calls).toBe(1);
  await page.unroute(endpoint);
  await page.route(endpoint, (route) =>
    route.fulfill({
      status: allowed ? 200 : 401,
      json: allowed
        ? { ...base, status: "done", verdict: "no_violation" }
        : { detail: "unauthorized" },
    }),
  );
  await page.route("**/api/ui/session", (route) => {
    allowed = true;
    return route.fulfill({ json: { ok: true } });
  });
  await page.reload();
  await page.getByLabel("Код доступа", { exact: true }).fill("synthetic-test-code");
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Нарушение не обнаружено", exact: true }),
  ).toBeVisible();
});
test("rate limit honors Retry-After without exposing raw server HTML", async ({ page }) => {
  await page.route("**/api/ui/jobs", (route) =>
    route.fulfill({
      status: 429,
      headers: { "Retry-After": "2" },
      contentType: "text/html",
      body: "<h1>Internal detail</h1>",
    }),
  );
  await page.goto("/");
  await page.getByLabel("Выбрать файл", { exact: true }).setInputFiles({
    name: "synthetic.mov",
    mimeType: "video/quicktime",
    buffer: Buffer.from("fixture"),
  });
  await expect(page.getByText("Сейчас слишком много проверок. Повторите позже.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Повторить", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Повторить", exact: true })).toBeEnabled({
    timeout: 5000,
  });
  await expect(page.getByText("Internal detail")).toHaveCount(0);
});
