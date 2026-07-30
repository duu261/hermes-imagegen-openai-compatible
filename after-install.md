### OpenAI-Compatible Image Generation Plugin Installed

To activate this provider in Hermes:

```bash
hermes config set image_gen.provider openai-compatible
```

Configure your endpoint in `~/.hermes/.env` if needed:
- `OPENAI_COMPAT_IMAGE_BASE_URL` (default: `http://localhost:8000/v1`)
- `OPENAI_COMPAT_IMAGE_MODEL` (default: `image-model-v1`)
- `OPENAI_COMPAT_IMAGE_API_KEY` (optional)
