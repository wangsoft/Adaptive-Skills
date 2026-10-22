import { describe, expect, it } from "vitest";
import { readSourceFailures, sanitizeSourceError, sourceFailureKind } from "./sourceRefresh";

describe("source update diagnostics", () => {
  it("redacts credentials without removing the Git diagnostic", () => {
    const raw = [
      "fatal: unable to access 'https://alice:private-pass@example.test/team/repo.git?access_token=private-query': Could not resolve host: example.test",
      "Authorization: Bearer private-header",
      "Proxy-Authorization: Basic cHJpdmF0ZQ==",
      "password='private password' token=private-token",
      "github_pat_private_github ghp_privategithub glpat-privategitlab sk-privateapi",
    ].join("\n");
    const result = sanitizeSourceError(raw);
    expect(result).toContain("Could not resolve host: example.test");
    expect(result).toContain("https://[redacted]@example.test/team/repo.git?[redacted]");
    for (const value of ["private", "alice", "cHJpdmF0ZQ=="]) expect(result).not.toContain(value);
    expect(sanitizeSourceError(result)).toBe(result);
  });

  it("redacts before bounding long logs and removes terminal escape sequences", () => {
    const result = sanitizeSourceError(`\u001b[31mfatal\u001b[0m https://user:${"a".repeat(10000)}@example.test/repo\n${"x".repeat(7000)}`);
    expect(result).toContain("fatal https://[redacted]@example.test/repo");
    expect(result).not.toContain("\u001b");
    expect(result).not.toContain("a".repeat(20));
    expect(result.endsWith("[truncated]")).toBe(true);
    expect(result.length).toBeLessThan(6100);
    expect(sanitizeSourceError(result)).toBe(result);
  });

  it("allows only failure fields into history, tolerating malformed entries", () => {
    const failures = readSourceFailures([
      null, false, "invalid", { source: "missing-id" },
      { source_id: "ok", source: "ok", status: "updated", error: "discard" },
      { source_id: "failed", source: "demo", status: "failed", type: "ValidationError", error: "fatal", credential: "never-store", scan: { secret: "never-store" } },
      { source_id: "no-error", source: "no-error", error: {} },
    ]);
    expect(failures).toEqual([
      { source_id: "failed", source: "demo", type: "ValidationError", error: "fatal" },
      { source_id: "no-error", source: "no-error", error: "" },
    ]);
    expect(readSourceFailures(undefined)).toBeUndefined();
  });

  it.each([
    ["Refusing to update dirty source: demo", "local_changes"],
    ["Your local changes would be overwritten by merge", "local_changes"],
    ["Source has no remote URL: demo", "local_source"],
    ["Could not resolve host: github.com", "network"],
    ["SSL certificate problem", "network"],
    ["Operation timed out", "network"],
    ["The requested URL returned error: 403", "access"],
    ["Permission denied (publickey)", "access"],
    ["Repository not found", "access"],
    ["Not possible to fast-forward, aborting.", "diverged"],
    ["There is no tracking information for the current branch", "branch"],
    ["cannot change to '/missing': No such file or directory", "missing"],
    ["Unable to create '.git/index.lock': File exists", "locked"],
    ["Unexpected scanner failure", "unknown"],
  ])("explains %s as %s", (error, kind) => {
    expect(sourceFailureKind(error)).toBe(kind);
  });
});
