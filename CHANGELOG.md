# Changelog

## 2.0.2

- Source-image URLs now go through Hermes' SSRF guard (`tools.url_safety.is_safe_url` plus
  `create_ssrf_safe_client`, redirects off). This closes a gap where hosts resolving to CGNAT /
  Tailscale `100.64.0.0/10` were fetched, and pins the vetted IP at connect time against DNS
  rebinding. `security.allow_private_urls` is honored the same way as in Hermes' other fetchers.
- Source images from local paths, data URLs and HTTPS URLs must start with PNG, JPEG, WebP or GIF
  magic bytes; anything else is an `io_error` and is never uploaded to the gateway.
- Never forward `OPENAI_ORG_ID` / `OPENAI_PROJECT_ID` as `OpenAI-Organization` / `OpenAI-Project`
  headers to the third-party gateway.
- Declare `requires_hermes: ">=0.20.1"`, the first release that reads
  `plugins.entries.<id>.settings` through `ctx.get_config`.

## 2.0.1

- Accept plain `http://` gateways whose host resolves only to private networks (loopback, RFC 1918
  LAN / Docker, Tailscale `100.64.0.0/10`, IPv6 ULA), so self-hosted CLIProxyAPI works by service
  name, LAN or Tailscale address. Public, link-local (cloud metadata), unresolvable and mixed
  private/public hosts are still refused; HTTPS is unchanged.

## 2.0.0

- Settings move to `plugins.entries.openai-compatible.settings` (`base_url`, `key_env`, `quality`);
  the key is read through Hermes' profile-scoped secret lookup.
- `image_gen.model` now takes precedence over `OPENAI_COMPAT_IMAGE_MODEL`. The 1.x environment
  variables still work as fallbacks.
- Real error results: source images load before the request and report `io_error` with the path;
  gateway failures return the HTTP status, a redacted message and a gateway-specific hint
  (`auth_error`, `rate_limited`, `timeout`, `connection_error`, `api_error`).
- Full quality ladder (`auto` to `max`) for GPT image models, via the setting or a model suffix.
- Results report `api_model`, `requested_size`, `requested_quality` and the decoded `output_size`.
- Explicit `User-Agent` on gateway and source-image requests.
- README rewritten around CLIProxyAPI and New API, with measured gateway notes.
- CI runs the unit tests, `hermes plugins doctor --ci` and `hermes plugins validate`.

## 1.1.1

- Send `quality: medium` and size for `gpt-image-2.5` models.

## 1.1.0

- Harden source-image loading (HTTPS only, no redirects, private addresses refused, 50 MB cap).

## 1.0.0

- Initial OpenAI Images-compatible provider.
