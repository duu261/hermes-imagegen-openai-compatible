### openai-compatible installed

GPT Image through CLIProxyAPI, New API, or another OpenAI Images gateway.

```bash
hermes plugins enable openai-compatible
hermes config set image_gen.provider openai-compatible
hermes config set image_gen.model gpt-image-2
hermes config set plugins.entries.openai-compatible.settings.base_url https://<your-gateway>/v1
```

The gateway key goes in `~/.hermes/.env` as `OPENAI_COMPAT_IMAGE_API_KEY`, or point
`plugins.entries.openai-compatible.settings.key_env` at a variable you already have.

Restart a running gateway (`hermes gateway restart`) to load it. Gateway notes and all settings:
https://github.com/duu261/hermes-imagegen-openai-compatible#readme
