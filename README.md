# hermes-imagegen-openai-compatible

GPT Image for [Hermes Agent](https://github.com/NousResearch/hermes-agent) through your own gateway:
[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) (CPA), [New API](https://github.com/QuantumNous/new-api),
or any server that speaks the OpenAI Images API (`/v1/images/generations` and `/v1/images/edits`).

It plugs into the standard `image_generate` tool as the `openai-compatible` image provider. Chat
routing, `OPENAI_BASE_URL`, `OPENAI_API_KEY` and every other Hermes subsystem stay untouched.

## Do you need this plugin?

Hermes ships a bundled `openai` image provider that also accepts a custom endpoint
(`image_gen.openai.base_url` plus `image_gen.openai.key_env`). If you call OpenAI directly, or a
gateway that behaves exactly like OpenAI, use the bundled one.

Use this plugin when your images go through CPA or New API:

- **Gateway-aware errors.** HTTP status and the gateway's own message come back to the model,
  with hints for the usual gateway failures (Cloudflare `1010`, `524` timeouts, disabled channels,
  wrong `/v1` root). A missing reference image is reported as `io_error` with its path before any
  request is sent, so the model regenerates instead of retrying blind.
- **Requested vs returned size.** Results include `requested_size`, `requested_quality` and the
  decoded `output_size`, because Codex-backed gateways pick their own dimensions.
- **Gateway-defined models.** Any model id the gateway serves (for example a Gemini image model
  behind CPA) is sent verbatim, without the GPT-only `size` and `quality` fields that such
  channels reject.
- **Stricter source-image loading.** HTTPS only through Hermes' SSRF guard (private, CGNAT and
  link-local hosts refused, connect-time IP pinning), no redirects, image magic bytes required,
  50 MB cap.

## Install

From the [Hermes plugin catalog](https://hermes-agent.nousresearch.com/docs/plugins/openai-compatible)
(recommended):

```bash
hermes plugins install openai-compatible
hermes plugins enable openai-compatible
```

This installs the commit the catalog maintainers reviewed. `hermes plugins update openai-compatible`
moves to a newer release only after its catalog pin is bumped.

Restart a running gateway afterwards (`hermes gateway restart`); new `hermes chat` sessions pick it
up directly.

Want unreleased changes from `main`? Install from GitHub instead. Hermes marks this as an unreviewed
source, and `hermes plugins update openai-compatible` pulls the latest `main`:

```bash
hermes plugins install duu261/hermes-imagegen-openai-compatible
hermes plugins enable openai-compatible
```

## Configure

```bash
# 1. Select the provider and a model
hermes config set image_gen.provider openai-compatible
hermes config set image_gen.model gpt-image-2

# 2. Point it at your gateway's API root
hermes config set plugins.entries.openai-compatible.settings.base_url http://127.0.0.1:8317/v1
```

3. Put the gateway key in `~/.hermes/.env` as `OPENAI_COMPAT_IMAGE_API_KEY` (the install prompt
   offers this). Already have the key under another name? Point at it instead of copying it:

```bash
hermes config set plugins.entries.openai-compatible.settings.key_env MY_GATEWAY_KEY
```

| Setting (`plugins.entries.openai-compatible.settings.*`) | Default | Meaning |
|---|---|---|
| `base_url` | none (required) | Gateway API root, normally ending in `/v1`. HTTPS, or plain HTTP to a private address (see below). |
| `key_env` | `OPENAI_COMPAT_IMAGE_API_KEY` | Name of the `.env` variable holding the key. Empty key = keyless local gateway. |
| `quality` | `medium` | `auto`, `low`, `medium`, `high`, `xhigh` or `max`, sent for GPT image models. |

The model comes from `image_gen.model`. A quality suffix overrides the `quality` setting for that
model: `gpt-image-2-high` sends `model: gpt-image-2, quality: high`.

**Switching models.** Hermes' `image_generate` tool has no `model` argument; the configured
`image_gen.model` is the only selector. To switch, run `hermes config set image_gen.model <id>`
yourself, or let the agent run it from its terminal tool before calling `image_generate`. Hermes
re-reads config on every call, so the next generation uses the new model without a restart. The
change is profile-wide and persists until set again.

| Model id | Sent as |
|---|---|
| `gpt-image-2`, `gpt-image-2.5`, `gpt-image-2.5-flare`, `gpt-image-2.5-sunburst`, `gpt-image-1`, `gpt-image-1.5` | model + `size` from the aspect ratio + `quality` |
| Any of the above + `-low`/`-medium`/`-high`/`-xhigh`/`-max`/`-auto` | base model + that quality |
| Anything else (e.g. a Gemini image model your gateway exposes) | model only, verbatim |

Aspect ratios map to `1536x1024` (landscape), `1024x1024` (square) and `1024x1536` (portrait).

### Plain HTTP for self-hosted gateways

The key is sent as a bearer header, so `base_url` must be HTTPS unless the gateway is reachable
only over a private network. Plain `http://` is accepted when the host resolves **exclusively** to:

| Network | Typical use |
|---|---|
| `127.0.0.0/8`, `::1` | CPA on the same machine |
| `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` | LAN, Docker service names (`http://cpa:8317/v1`) |
| `100.64.0.0/10`, `fc00::/7` | Tailscale / CGNAT, IPv6 ULA |

Anything else is refused: public addresses, link-local `169.254.0.0/16` (cloud metadata),
hostnames that fail to resolve, and hostnames where even one resolved address is public. The check
runs when each request is prepared, against the addresses the host resolves to at that moment.

## Gateway notes

These were measured on live routes; your gateway version may differ, and the gateway remains the
source of truth for what it accepts.

### CLIProxyAPI (CPA)

- `base_url` is the CPA listener, e.g. `http://127.0.0.1:8317/v1`; the key is a CPA API key.
- With Codex OAuth accounts, the exact ids `gpt-image-1.5`, `gpt-image-2`, `gpt-image-2.5`,
  `gpt-image-2.5-flare` and `gpt-image-2.5-sunburst` pass through to ChatGPT's image backend. Other
  names become a Responses call with an image tool.
- Codex OAuth renders at roughly medium regardless of the requested quality, and picks its own
  size (a `2048x2048` request came back `1536x1024`). Compare `requested_*` with `output_size`
  before paying for higher tiers. Native 2K/4K and `xhigh`/`max` need an API-key route.
- The returned `model` only echoes the requested name; it does not prove which variant rendered.
- Gemini image models served by CPA (for example via Antigravity accounts) also work: set their id
  as `image_gen.model` and they are sent without `size`/`quality`.

### New API

- `base_url` is your New API site root plus `/v1`; the key is a New API token whose group can use
  the image channel.
- Enable the image model on a channel whose upstream supports `/v1/images/generations` (and
  `/v1/images/edits` for editing). A `404` or "model not enabled" error names the gap.
- Sites behind Cloudflare can block default HTTP client signatures (`403`, error `1010`). The
  plugin sends its own `User-Agent`; if it is still blocked, allow it in the WAF.
- Cloudflare cuts requests at about 125 s (`524`) while the upstream keeps rendering and billing.
  For slow, high-quality renders, point `base_url` at a route that bypasses the CDN.

## Upgrading from 1.x

1.x read everything from environment variables. 2.0 still reads them as a fallback:
`OPENAI_COMPAT_IMAGE_BASE_URL` is used when `settings.base_url` is unset, and
`OPENAI_COMPAT_IMAGE_MODEL` when `image_gen.model` is unset. Note that `image_gen.model` now wins
over `OPENAI_COMPAT_IMAGE_MODEL` (in 1.x the env var won). Move to the settings above and remove the
old variables when convenient.

## Trust boundary

Use only a gateway you trust. When it returns an image `url` instead of `b64_json`, Hermes
downloads that URL from the machine running Hermes with its standard image downloader, which is
not confined to public addresses. Base64 output is saved directly. Source images you pass for
editing go through this plugin's own restricted loader.

## Development

```bash
PYTHONPATH=~/.hermes/hermes-agent python -m unittest discover -s tests -v
hermes plugins doctor . --ci
hermes plugins validate .
```

See [CHANGELOG.md](CHANGELOG.md) and [AGENTS.md](AGENTS.md).

## License

MIT
