import { readdirSync, readFileSync } from "node:fs";
import { resolve, sep } from "node:path";
import { expect, it } from "vitest";

function sourceFiles(path: string): string[] {
  return readdirSync(path, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory()
      ? sourceFiles(resolve(path, e.name))
      : /\.tsx?$/.test(e.name)
        ? [resolve(path, e.name)]
        : [],
  );
}
it("keeps all UI Cyrillic in the dictionary", () => {
  for (const file of sourceFiles(resolve("src"))) {
    if (
      file.includes(`${sep}i18n${sep}`) ||
      file.includes(".test.") ||
      file.endsWith("schema.d.ts")
    )
      continue;
    expect(readFileSync(file, "utf8"), file).not.toMatch(/[А-Яа-яЁё]/);
  }
});
