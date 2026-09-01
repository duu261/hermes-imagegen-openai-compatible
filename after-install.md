### OpenAI-Compatible Image Generation Plugin Installed

To activate this provider in Hermes:

```bash
hermes config set image_gen.provider openai-compatible
```

Configure your endpoint in `~/.hermes/.env`:
- `OPENAI_COMPAT_IMAGE_BASE_URL` (required; use HTTPS for remote endpoints)
- `OPENAI_COMPAT_IMAGE_MODEL` (default: `gpt-image-2`)
- `OPENAI_COMPAT_IMAGE_API_KEY` (optional)
