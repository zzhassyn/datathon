import { describe, expect, it } from "vitest";
import { ru } from "../../shared/i18n/ru";
import { MAX_FILE_BYTES, validateFiles } from "./validateFile";

describe("file validation", () => {
  it("uses decimal boundary and ignores MIME", () => {
    expect(validateFiles([{ name: "test.MOV", size: MAX_FILE_BYTES }])).toBeNull();
    expect(validateFiles([{ name: "test.mp4", size: MAX_FILE_BYTES + 1 }])).toBe(ru.tooLarge);
  });
  it("rejects empty, unsupported and multiple files", () => {
    expect(validateFiles([{ name: "x.mov", size: 0 }])).toBe(ru.emptyFile);
    expect(validateFiles([{ name: "x.webm", size: 1 }])).toBe(ru.wrongType);
    expect(validateFiles([])).toBe(ru.multiple);
    expect(
      validateFiles([
        { name: "a.mov", size: 1 },
        { name: "b.mov", size: 1 },
      ]),
    ).toBe(ru.multiple);
  });
});
