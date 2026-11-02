# Security

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/ia14borzenko/lde/security/advisories/new)
for this repository. Include the affected component (Studio, MCP server, `lde-server`, sync/sharing),
the steps to reproduce and the impact you see.

## Security model in brief

**Local (Studio, MCP server)**

- Studio and the MCP server listen on `127.0.0.1` only.
- The MCP server requires a per-user key (`~/.config/lde/mcp.json`, mode 0600), compares it in constant
  time and validates `Host` and `Origin` to prevent DNS rebinding. The key grants full access to your
  LDE projects: keep the agent link private and rotate the key if it leaks.
- Agent tools cannot write generated or service files (`out/`, `.lde/`, `.git/`) or paths outside the project.
- Calculation scripts are your own code and run with your permissions (in a copy of the calculation
  folder with a timeout; network access is cut off with `unshare -rn` where the system allows
  unprivileged user namespaces). Do not run calculations from projects you do not trust.

**Server (`lde-server`)**

- End-to-end encryption of documents, edits, comments, history and titles (AES-256-GCM, X25519, HKDF,
  PBKDF2); the server stores only ciphertext.
- Passwords never leave the device; review-link keys travel in the URL fragment and never reach the server.
- Hardened container and HTTP settings — see [`docs/guide/review-and-server.md`](docs/guide/review-and-server.md#security-model).
- Known limitation: the web panel's scripts are served by the server, so a compromised server could
  replace them. LDE itself is the primary trusted client.

## Secrets in this repository

The repository contains no credentials. Machine-specific settings (`toolchain/local.env`), the server's
`.env`, agent keys and agent feedback reports are excluded by `.gitignore`.
