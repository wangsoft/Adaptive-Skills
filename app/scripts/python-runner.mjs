import { existsSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

export function resolvePython(
  appRoot,
  platform = process.platform,
  environment = process.env,
  exists = existsSync,
) {
  const configured = environment.ADAPTIVE_SKILLS_PYTHON?.trim();
  if (configured) return configured;

  const virtualEnvironment = platform === "win32"
    ? path.join(appRoot, "..", ".venv", "Scripts", "python.exe")
    : path.join(appRoot, "..", ".venv", "bin", "python");
  if (exists(virtualEnvironment)) return virtualEnvironment;
  return platform === "win32" ? "python" : "python3";
}

function main() {
  const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const arguments_ = process.argv.slice(2);
  if (arguments_.length === 0) {
    throw new Error("A Python script path is required");
  }

  const result = spawnSync(resolvePython(appRoot), arguments_, {
    cwd: appRoot,
    env: process.env,
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status === null) {
    throw new Error(`Python process ended without an exit status (${result.signal ?? "unknown signal"})`);
  }
  process.exitCode = result.status;
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main();
}
