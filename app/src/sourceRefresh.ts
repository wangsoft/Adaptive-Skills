export interface SourceRefreshFailure {
  source_id: string;
  source: string;
  type?: string;
  error: string;
}

// Redact before truncating so a long credential cannot be cut into an unrecognizable fragment.
export function sanitizeSourceError(value: string, limit = 6000): string {
  const safe = value
    .replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\b[a-z][a-z0-9+.-]*:\/\/[^\s<>"']+/gi, (url) => url
      .replace(/(\/\/)[^/]*@/, "$1[redacted]@")
      .replace(/[?#].*$/, "?[redacted]"))
    .replace(/(authorization\s*[:=]\s*)[^\r\n]+/gi, "$1[redacted]")
    .replace(/\b(?:bearer|basic)\s+[a-z0-9._~+/=-]+/gi, "[redacted]")
    .replace(/((?:password|passwd|access[_-]?token|api[_-]?key|token|secret)\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)/gi, "$1[redacted]")
    .replace(/\b(?:gh[pousr]_[a-z0-9_]+|github_pat_[a-z0-9_]+|glpat-[a-z0-9_-]+|sk-[a-z0-9_-]+)/gi, "[redacted]")
    .trim();
  return safe.length > limit ? `${safe.slice(0, limit)}\n[truncated]` : safe;
}

export function readSourceFailures(value: unknown): SourceRefreshFailure[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.flatMap((item): SourceRefreshFailure[] => {
    if (!item || typeof item !== "object" || typeof item.source_id !== "string" ||
        typeof item.source !== "string" || (item.status && item.status !== "failed")) return [];
    return [{
      source_id: item.source_id.slice(0, 128),
      source: sanitizeSourceError(item.source, 200),
      ...(typeof item.type === "string" ? { type: sanitizeSourceError(item.type, 100) } : {}),
      error: typeof item.error === "string" ? sanitizeSourceError(item.error) : "",
    }];
  }).slice(0, 1000);
}

export function sourceFailureKind(error: string) {
  if (/dirty source|would be overwritten|local changes|untracked working tree/i.test(error)) return "local_changes";
  if (/local-maintained|no remote URL/i.test(error)) return "local_source";
  if (/could not resolve|failed to connect|timed? out|connection (?:reset|refused)|network is unreachable|unable to access.*(?:proxy|ssl)|ssl certificate|tls|http [45]\d\d|returned error: (?:429|5\d\d)/i.test(error)) return "network";
  if (/authentication failed|permission denied|could not read (?:username|password)|repository not found|returned error: (?:401|403)|host key verification failed/i.test(error)) return "access";
  if (/not possible to fast-forward|non-fast-forward|divergent branches|refusing to merge unrelated/i.test(error)) return "diverged";
  if (/no tracking information|couldn't find remote ref|not something we can merge|upstream branch|did not match any file/i.test(error)) return "branch";
  if (/no such file or directory|not a git repository|cannot change to|does not exist/i.test(error)) return "missing";
  if (/index\.lock|another git process/i.test(error)) return "locked";
  return "unknown";
}
