# hermes-imagegen-openai-compatible

Generic OpenAI-compatible image generation provider plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Routes `image_generate` tool calls to any custom or local endpoint implementing the OpenAI `POST /v1/images/generations` and `POST /v1/images/edits` standards — such as **LiteLLM**, **LocalAI**, **vLLM**, or private reverse proxies.

## Features

- Supports both text-to-image generation (`images.generate`) and image-to-image / reference editing (`images.edit`).
- Configurable base URL, model ID, and optional API key.
- Decodes base64 (`b64_json`) or fetches URL image payloads and stores them under `$HERMES_HOME/cache/images/` for native media rendering (Telegram, Discord, CLI, TUI).
- Isolated environment variable namespace (`OPENAI_COMPAT_IMAGE_*`) to avoid polluting global `OPENAI_BASE_URL`.

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

2. Configure environment variables in `~/.hermes/.env` (optional):

| Environment Variable | Description | Default |
|---|---|---|
| `OPENAI_COMPAT_IMAGE_BASE_URL` | Base URL of the OpenAI-compatible API | `http://localhost:8000/v1` |
| `OPENAI_COMPAT_IMAGE_MODEL` | Model identifier to send in the payload | `image-model-v1` |
| `OPENAI_COMPAT_IMAGE_API_KEY` | Bearer API key (if required) | `dummy` |

## License

MIT
