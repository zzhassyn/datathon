import { describe, expect, it } from 'vitest';
import { timecode, validateFile } from '../src/workspace';
describe('Local video input', () => {
  it('accepts case MOV files, including uppercase extensions', () => {
    expect(validateFile({ name: 'Х-34.MOV', size: 29_000_000 })).toBeNull();
  });
  it('rejects empty, unsupported and over-limit files', () => {
    expect(validateFile({ name: 'video.mov', size: 0 })).toContain('пуст');
    expect(validateFile({ name: 'script.html', size: 100 })).toContain('формате');
    expect(validateFile({ name: 'big.mov', size: 501 * 1024 * 1024 })).toContain('500');
  });
});
describe('Evidence timestamp display', () => {
  it('preserves milliseconds and supports long videos', () => {
    expect(timecode(13, true)).toBe('00:13.000');
    expect(timecode(44.2, true)).toBe('00:44.200');
    expect(timecode(3661.5, true)).toBe('01:01:01.500');
  });
  it('does not show NaN or negative time while metadata is loading', () => {
    expect(timecode(NaN)).toBe('00:00');
    expect(timecode(-5)).toBe('00:00');
  });
});
