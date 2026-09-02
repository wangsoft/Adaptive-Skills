import { isTauri } from "@tauri-apps/api/core";
import type { DownloadEvent } from "@tauri-apps/plugin-updater";

export const UPDATE_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000;
export const LAST_UPDATE_CHECK_KEY = "adaptive-skills:last-update-check";

export type UpdateLike = {
  currentVersion: string;
  version: string;
  date?: string;
  body?: string;
  downloadAndInstall: (onEvent?: (event: DownloadEvent) => void) => Promise<void>;
  close?: () => Promise<void>;
};

export type UpdateAdapter = {
  currentVersion: () => Promise<string>;
  supportsUpdates: () => Promise<boolean>;
  check: () => Promise<UpdateLike | null>;
  relaunch: () => Promise<void>;
  relaunchAfterInstall: () => Promise<boolean>;
};

export type DownloadProgress = {
  downloaded: number;
  total: number | null;
  percent: number | null;
  finished: boolean;
};

export const EMPTY_DOWNLOAD_PROGRESS: DownloadProgress = {
  downloaded: 0,
  total: null,
  percent: null,
  finished: false,
};

export function supportsInAppUpdates(bundleType: string): boolean {
  return bundleType !== "deb" && bundleType !== "rpm";
}

export function needsRelaunchAfterInstall(bundleType: string): boolean {
  return bundleType !== "nsis" && bundleType !== "msi";
}

export function shouldRunAutomaticCheck(
  storage: Pick<Storage, "getItem">,
  now = Date.now(),
): boolean {
  const raw = storage.getItem(LAST_UPDATE_CHECK_KEY);
  if (!raw) return true;
  const lastCheck = Number(raw);
  return !Number.isFinite(lastCheck) || now - lastCheck >= UPDATE_CHECK_INTERVAL_MS;
}

export function recordUpdateCheck(
  storage: Pick<Storage, "setItem">,
  now = Date.now(),
): void {
  storage.setItem(LAST_UPDATE_CHECK_KEY, String(now));
}

export function nextDownloadProgress(
  current: DownloadProgress,
  event: DownloadEvent,
): DownloadProgress {
  if (event.event === "Started") {
    return {
      downloaded: 0,
      total: event.data.contentLength ?? null,
      percent: event.data.contentLength ? 0 : null,
      finished: false,
    };
  }
  if (event.event === "Finished") {
    return {
      ...current,
      downloaded: current.total ?? current.downloaded,
      percent: current.total ? 100 : current.percent,
      finished: true,
    };
  }
  const downloaded = current.downloaded + event.data.chunkLength;
  return {
    ...current,
    downloaded,
    percent: current.total
      ? Math.min(100, Math.round((downloaded / current.total) * 100))
      : null,
  };
}

export async function performUpdateCheck(adapter: UpdateAdapter): Promise<UpdateLike | null> {
  return adapter.check();
}

export async function installConfirmedUpdate(
  adapter: UpdateAdapter,
  update: UpdateLike,
  onEvent?: (event: DownloadEvent) => void,
): Promise<void> {
  await update.downloadAndInstall(onEvent);
  if (await adapter.relaunchAfterInstall()) await adapter.relaunch();
}

export function createTauriUpdateAdapter(): UpdateAdapter | null {
  if (!isTauri()) return null;
  return {
    currentVersion: async () => {
      const { getVersion } = await import("@tauri-apps/api/app");
      return getVersion();
    },
    supportsUpdates: async () => {
      const { getBundleType } = await import("@tauri-apps/api/app");
      const bundleType = await getBundleType();
      // Tauri v2 emits updater signatures for AppImage, NSIS, and macOS App
      // bundles. DEB/RPM installs remain on the package-manager update path.
      return supportsInAppUpdates(bundleType);
    },
    check: async () => {
      const { check } = await import("@tauri-apps/plugin-updater");
      return check({ timeout: 20_000 });
    },
    relaunch: async () => {
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    },
    relaunchAfterInstall: async () => {
      const { getBundleType } = await import("@tauri-apps/api/app");
      const bundleType = await getBundleType();
      // Windows installers exit and restart the application themselves.
      return needsRelaunchAfterInstall(bundleType);
    },
  };
}
