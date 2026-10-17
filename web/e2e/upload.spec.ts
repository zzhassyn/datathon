import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

test("real backend: upload, durable job URL and reload", async ({ page }) => {
  await page.goto("/");
  await page
    .getByLabel("Выбрать файл", { exact: true })
    .setInputFiles(resolve("e2e/fixtures/clean.mov"));
  await expect(page).toHaveURL(/job=[a-f0-9]{32}/);
  await expect(page.getByText("Нарушение не обнаружено", { exact: true })).toBeVisible({
    timeout: 20000,
  });
  await page.reload();
  await expect(page.getByText("Нарушение не обнаружено", { exact: true })).toBeVisible();
});
test("invalid ID makes no job request", async ({ page }) => {
  let count = 0;
  page.on("request", (r) => {
    if (r.url().includes("/api/ui/jobs/")) count++;
  });
  await page.goto("/?job=abc");
  await expect(page.getByText("Неверная ссылка на проверку.")).toBeVisible();
  expect(count).toBe(0);
});
test("rejects format before network", async ({ page }) => {
  let posts = 0;
  page.on("request", (r) => {
    if (r.method() === "POST") posts++;
  });
  await page.goto("/");
  await page
    .getByLabel("Выбрать файл", { exact: true })
    .setInputFiles({ name: "bad.txt", mimeType: "text/plain", buffer: Buffer.from("not-video") });
  await expect(page.getByText("Нужен файл .mov или .mp4.")).toBeVisible();
  expect(posts).toBe(0);
});
