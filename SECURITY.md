# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately via GitHub's
["Report a vulnerability"](https://github.com/TokyoDanInJapan/nixplay-tools/security/advisories/new)
form rather than opening a public issue.

## Scope notes

- These tools talk to Nixplay's unofficial web API with credentials the
  user supplies via `.env` or environment variables. Credentials are
  only ever sent to `api.nixplay.com` and are never written to disk by
  the tools.
- `nixplay-prune --execute` permanently deletes photos from the
  authenticated Nixplay account; its backup interlock and verification
  behavior are security-relevant and regressions there are treated as
  vulnerabilities.
