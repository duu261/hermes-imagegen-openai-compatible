# hermes-imagegen-openai-compatible

OpenAI Images-compatible image generation provider plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Routes Hermes `image_generate` calls to a trusted endpoint implementing the OpenAI
`POST /v1/images/generations` and `POST /v1/images/edits` paths. It is intended for
GPT-Image2 through an OpenAI-compatible reverse proxy, including a self-hosted
New API deployment. It is not a native Codex `image_generation` protocol adapter.

## Features

- Supports text-to-image generation (`images.generate`) and image-to-image / reference editing (`images.edit`).
- Maps Hermes `landscape`, `square`, and `portrait` to GPT-Image2 sizes `1536x1024`, `1024x1024`, and `1024x1536`.
- Supports GPT-Image2 quality tiers through `gpt-image-2-low`, `gpt-image-2-medium`, `gpt-image-2-high`, or exact `gpt-image-2` (medium default).
- Accepts up to 16 source images for GPT-Image2 editing.
- Decodes base64 (`b64_json`) or fetches URL image payloads and stores them under `$HERMES_HOME/cache/images/` for native media rendering (Telegram, Discord, CLI, TUI).
- Isolated environment variable namespace (`OPENAI_COMPAT_IMAGE_*`) to avoid polluting global `OPENAI_BASE_URL`.

## Why this is a plugin

This plugin keeps custom image routing separate from Hermes Agent's core OpenAI configuration. It does **not** change `OPENAI_BASE_URL`, `OPENAI_API_KEY`, Hermes model routing, or other built-in tools. Hermes dispatches to this backend only when `image_gen.provider` is set to `openai-compatible`; switching to another image provider immediately bypasses it.

The endpoint, model, and credential use separate `OPENAI_COMPAT_IMAGE_*` settings. This makes custom image routing opt-in and reversible while preserving Hermes core behavior.

## Installation

### From GitHub

```bash
hermes plugins install duu261/hermes-imagegen-openai-compatible --enable
```

### From Local Path / Git URL

```bash
hermes plugins install file:///path/to/hermes-imagegen-openai-compatible --enable
```

## Setup & Configuration

1. Set `openai-compatible` as your active provider and model in Hermes:

```bash
hermes config set image_gen.provider openai-compatible
hermes config set image_gen.model <your-model-id>
```

2. Configure environment variables in `~/.hermes/.env`:

| Environment Variable | Description | Default |
|---|---|---|
| `OPENAI_COMPAT_IMAGE_BASE_URL` | Base URL of the OpenAI-compatible API | required |
| `OPENAI_COMPAT_IMAGE_MODEL` | Model identifier to send in the payload | `gpt-image-2` |
| `OPENAI_COMPAT_IMAGE_API_KEY` | Bearer API key (if required) | `dummy` |

Remote base URLs must use HTTPS. Plain HTTP is allowed only for local
development on `localhost`, `127.0.0.1`, or `::1`. Source image URLs must use
HTTPS, cannot redirect, cannot resolve to private/local addresses, and are
bounded to 50MB. Credentials must never appear in URLs.

## GPT-Image2 scope

This plugin provides the basic OpenAI Images API path needed for GPT-Image2
through a compatible proxy. It sends `model`, `prompt`, `size`, `quality`, and
`n=1`, then saves `b64_json` output locally. It does not expose native Codex
`image_gen.imagegen`, partial-image streaming, masks, transparent-background
fallbacks, or arbitrary provider-specific controls.

The configured proxy remains the source of truth for model availability,
account permissions, billing, request rewriting, and actual option support.
This repository does not prove OpenAI entitlement or a successful production
generation.

## License

MIT
