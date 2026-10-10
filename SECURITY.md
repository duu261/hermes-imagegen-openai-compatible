# Security Policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub private vulnerability reporting](https://github.com/duu261/hermes-imagegen-openai-compatible/security/advisories/new).
Do not open a public issue for a vulnerability.

Include the plugin version, your Hermes version, the gateway type (CLIProxyAPI, New API or other),
and steps to reproduce. Never include real API keys, gateway URLs or account details; use
placeholders such as `gateway.example`.

## Scope

In scope:

- Leaking the gateway API key (logs, error messages, tracebacks, request headers).
- Source-image loading bypassing the SSRF guard (private, loopback, metadata or Tailscale addresses).
- Plain-HTTP gateway access outside the documented private-network list.
- Forwarding OpenAI organization or project IDs, or reading `OPENAI_API_KEY` / `OPENAI_BASE_URL`.

Out of scope:

- Behavior of the gateway itself (CLIProxyAPI, New API or the upstream model provider).
- Image URLs returned by a gateway you configured; see "Trust boundary" in the README.

## Supported versions

Only the latest release receives fixes. Security fixes are released and submitted to the Hermes
plugin catalog promptly.
