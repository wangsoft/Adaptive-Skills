import { describe, expect, it, vi } from "vitest";
import type { DownloadEvent } from "@tauri-apps/plugin-updater";
import {
  EMPTY_DOWNLOAD_PROGRESS,
  installConfirmedUpdate,
  LAST_UPDATE_CHECK_KEY,
  nextDownloadProgress,
  needsRelaunchAfterInstall,
  performUpdateCheck,
  recordUpdateCheck,
  shouldRunAutomaticCheck,
  supportsInAppUpdates,
  UPDATE_CHECK_INTERVAL_MS,
  type UpdateAdapter,
  type UpdateLike,
} from "./update";

function fixture() {
  const update: UpdateLike = {
    currentVersion: "0.1.16",
    version: "0.1.17",
    downloadAndInstall: vi.fn(async () => undefined),
  };
  const adapter: UpdateAdapter = {
    currentVersion: vi.fn(async () => "0.1.16"),
    supportsUpdates: vi.fn(async () => true),
    check: vi.fn(async () => update),
    relaunch: vi.fn(async () => undefined),
    relaunchAfterInstall: vi.fn(async () => true),
  };
  return { adapter, update };
}

describe("desktop updater policy", () => {
  it("keeps DEB/RPM on package-manager updates and lets Windows own restarts", () => {
    expect(supportsInAppUpdates("appimage")).toBe(true);
    expect(supportsInAppUpdates("app")).toBe(true);
    expect(supportsInAppUpdates("nsis")).toBe(true);
    expect(supportsInAppUpdates("deb")).toBe(false);
    expect(supportsInAppUpdates("rpm")).toBe(false);
    expect(needsRelaunchAfterInstall("appimage")).toBe(true);
    expect(needsRelaunchAfterInstall("nsis")).toBe(false);
    expect(needsRelaunchAfterInstall("msi")).toBe(false);
  });

  it("checks automatically no more than once every 24 hours", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    };
    const now = 10 * UPDATE_CHECK_INTERVAL_MS;
    expect(shouldRunAutomaticCheck(storage, now)).toBe(true);
    recordUpdateCheck(storage, now);
    expect(values.get(LAST_UPDATE_CHECK_KEY)).toBe(String(now));
    expect(shouldRunAutomaticCheck(storage, now + UPDATE_CHECK_INTERVAL_MS - 1)).toBe(false);
    expect(shouldRunAutomaticCheck(storage, now + UPDATE_CHECK_INTERVAL_MS)).toBe(true);
  });

  it("never downloads or restarts during an update check", async () => {
    const { adapter, update } = fixture();
    expect(await performUpdateCheck(adapter)).toBe(update);
    expect(update.downloadAndInstall).not.toHaveBeenCalled();
    expect(adapter.relaunch).not.toHaveBeenCalled();
  });

  it("downloads and restarts only after the explicit install path", async () => {
    const { adapter, update } = fixture();
    await installConfirmedUpdate(adapter, update);
    expect(update.downloadAndInstall).toHaveBeenCalledOnce();
    expect(adapter.relaunch).toHaveBeenCalledOnce();
  });

  it("lets the Windows installer own restart behavior", async () => {
    const { adapter, update } = fixture();
    adapter.relaunchAfterInstall = vi.fn(async () => false);
    await installConfirmedUpdate(adapter, update);
    expect(update.downloadAndInstall).toHaveBeenCalledOnce();
    expect(adapter.relaunch).not.toHaveBeenCalled();
  });

  it("tracks known and unknown download sizes", () => {
    const events: DownloadEvent[] = [
      { event: "Started", data: { contentLength: 100 } },
      { event: "Progress", data: { chunkLength: 40 } },
      { event: "Progress", data: { chunkLength: 70 } },
      { event: "Finished" },
    ];
    const progress = events.reduce(nextDownloadProgress, EMPTY_DOWNLOAD_PROGRESS);
    expect(progress).toEqual({ downloaded: 100, total: 100, percent: 100, finished: true });

    const unknown = nextDownloadProgress(
      nextDownloadProgress(EMPTY_DOWNLOAD_PROGRESS, { event: "Started", data: {} }),
      { event: "Progress", data: { chunkLength: 12 } },
    );
    expect(unknown).toMatchObject({ downloaded: 12, total: null, percent: null });
  });
});
