import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

describe("desktop Python runner", () => {
  it("uses the platform virtual environment before PATH fallbacks", async () => {
    const runner = await import("./python-runner.mjs").catch(() => null);
    const existing = new Set([
      path.join(appRoot, "..", ".venv", "Scripts", "python.exe"),
      path.join(appRoot, "..", ".venv", "bin", "python"),
    ]);
    const exists = (candidate) => existing.has(candidate);

    expect(runner?.resolvePython(appRoot, "win32", {}, exists)).toBe(
      path.join(appRoot, "..", ".venv", "Scripts", "python.exe"),
    );
    expect(runner?.resolvePython(appRoot, "linux", {}, exists)).toBe(
      path.join(appRoot, "..", ".venv", "bin", "python"),
    );
  });

  it("honors an explicit interpreter and otherwise uses platform PATH names", async () => {
    const runner = await import("./python-runner.mjs").catch(() => null);
    const missing = () => false;

    expect(runner?.resolvePython(appRoot, "win32", { ADAPTIVE_SKILLS_PYTHON: "C:\\Python312\\python.exe" }, missing)).toBe(
      "C:\\Python312\\python.exe",
    );
    expect(runner?.resolvePython(appRoot, "win32", {}, missing)).toBe("python");
    expect(runner?.resolvePython(appRoot, "linux", {}, missing)).toBe("python3");
  });

  it("routes sidecar builds and bundle verification through the runner", () => {
    const packageJson = JSON.parse(readFileSync(path.join(appRoot, "package.json"), "utf8"));

    expect(packageJson.scripts["build:sidecar"]).toBe(
      "node scripts/python-runner.mjs ../scripts/build_desktop_sidecar.py",
    );
    expect(packageJson.scripts["verify:bundle"]).toBe(
      "node scripts/python-runner.mjs ../scripts/verify_desktop_bundle.py",
    );
  });
});
