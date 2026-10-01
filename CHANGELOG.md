# Changelog

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
