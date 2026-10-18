import { writeFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

// Explicit operator-only test. Never runs on bank data in CI/default e2e.
test.use({ trace: "off", screenshot: "off", video: "off" });
test("local calibration: displayed image and pointer coordinates share the same geometry", async ({
  page,
}) => {
  test.skip(process.env.CG_E2E_REAL_MODELS !== "1");
  let writes = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("local-calibration")) writes++;
  });
  await page.goto("/local-calibration");
  await page.waitForFunction(
    () => (document.getElementById("frame") as HTMLImageElement).naturalWidth > 0,
  );
  const metadata = await (await page.request.get("/api/local-calibration/meta")).json();
  if (metadata.saved_calibration) {
    await expect(page.locator("#coords")).toHaveText(
      JSON.stringify(metadata.saved_calibration.desk_roi),
    );
    await expect(page.locator("#saved-state")).toContainText("Сохранённая поверхность");
    await expect(page.locator("#ownership")).not.toBeChecked();
    await expect(page.locator("#cash")).not.toBeChecked();
    await expect(page.locator("#surface")).not.toBeChecked();
    await expect(page.locator("#save")).toBeDisabled();
  }
  const image = await page.locator("#frame").boundingBox();
  const overlay = await page.locator("#overlay").boundingBox();
  if (!image || !overlay) throw new Error("Calibration geometry missing");
  expect(Math.abs(image.width - overlay.width)).toBeLessThan(1);
  expect(Math.abs(image.height - overlay.height)).toBeLessThan(1);
  await page.mouse.move(
    image.x + (20 / metadata.width) * image.width,
    image.y + (30 / metadata.height) * image.height,
  );
  await page.mouse.down();
  await page.mouse.move(
    image.x + (120 / metadata.width) * image.width,
    image.y + (90 / metadata.height) * image.height,
  );
  await page.mouse.up();
  await expect(page.locator("#coords")).toHaveText("[20,30,120,90]");
  await expect(page.locator("#save")).toBeDisabled();
  expect(writes).toBe(0); // Only the human may confirm/save the real calibration.
});
test("local trained models: real upload, progress and calibration gate", async ({ page }) => {
  test.skip(process.env.CG_E2E_REAL_MODELS !== "1" || !process.env.CG_LOCAL_TEST_VIDEO);
  test.setTimeout(250000);
  const samples: unknown[] = [];
  const pageErrors: string[] = [];
  const externalOrigins = new Set<string>();
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (
      ["http:", "https:"].includes(url.protocol) &&
      !["127.0.0.1", "localhost"].includes(url.hostname)
    ) {
      externalOrigins.add(url.origin);
    }
  });
  const state: {
    final: {
      status: string;
      verdict: string | null;
      stages: unknown[];
      elapsed_sec: number;
    } | null;
  } = { final: null };
  const source = process.env.CG_LOCAL_TEST_VIDEO || "";
  page.on("response", async (response) => {
    if (!/\/api\/ui\/jobs\/[a-f0-9]{32}$/.test(new URL(response.url()).pathname)) return;
    if (response.request().method() !== "GET") return;
    const value = await response.json();
    samples.push({
      status: value.status,
      verdict: value.verdict,
      elapsed_sec: value.elapsed_sec,
      stages: value.stages,
      error: value.error,
    });
    if (value.status === "done" || value.status === "failed") state.final = value;
  });
  const started = Date.now();
  await page.goto("/");
  await page.getByLabel("Выбрать файл", { exact: true }).setInputFiles(source);
  await expect(page).toHaveURL(/job=[a-f0-9]{32}/, { timeout: 60000 });
  await expect.poll(() => state.final?.status, { timeout: 220000 }).toBe("failed");
  await expect(
    page.getByRole("heading", { name: "Анализ не выполнен", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Нарушение не обнаружено", { exact: true })).toHaveCount(0);
  await expect(
    page
      .getByText(
        /Не подтверждена зона стола|Принадлежность личных вещей|PERSONAL_ITEMS не подтверждено/,
      )
      .first(),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Этапы обработки", exact: true })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Проверить калибровку и подтверждения оператора", exact: true }),
  ).toHaveAttribute("href", "/local-calibration");
  expect(pageErrors).toEqual([]);
  expect([...externalOrigins]).toEqual([]);
  const report = {
    url: page.url(),
    elapsed_wall_sec: (Date.now() - started) / 1000,
    final: state.final,
    polls: samples,
    bank_images_exported: false,
    screenshots: false,
    accuracy_evaluation: false,
    page_errors: pageErrors,
    external_origins: [...externalOrigins],
  };
  if (process.env.CG_E2E_NUMERIC_REPORT) {
    await writeFile(process.env.CG_E2E_NUMERIC_REPORT, JSON.stringify(report, null, 2));
  }
  console.log(
    JSON.stringify({
      status: state.final?.status,
      elapsed_wall_sec: report.elapsed_wall_sec,
      polls: samples.length,
      verdict: state.final?.verdict,
    }),
  );
});
