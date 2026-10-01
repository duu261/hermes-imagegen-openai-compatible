# AGENTS.md

Contributor and agent rules for `hermes-imagegen-openai-compatible`. Public install and user docs
live in `README.md`; keep this file about how to change the plugin safely.

## What this plugin is

- A Hermes `ImageGenProvider` named `openai-compatible` (`kind: backend`), selected with
  `image_gen.provider: openai-compatible`. It services the standard `image_generate` tool.
- Target gateways, in priority order: **CLIProxyAPI (CPA)** and **New API** serving `gpt-image-*`;
  then any other OpenAI Images-compatible server. Gateway-defined non-GPT models (for example
  Gemini image models behind CPA) must keep working, sent verbatim without `size`/`quality`.
- Reason to exist: Hermes' bundled `openai` image provider already accepts a custom
  `base_url`/`key_env`. This plugin earns its place only through gateway-specific behavior
  (actionable errors, requested-vs-returned size, verbatim gateway models, strict source loading).
  Do not add features the bundled provider already covers unless they serve a gateway need, and
  keep the README's "Do you need this plugin?" section honest.

## Hard rules

- **Public repo.** No private domains, IPs, ports of real deployments, channel ids, account names,
  home paths or keys in code, tests, docs or commit messages. Examples use `gateway.example`,
  `newapi.example`, and the CPA default `127.0.0.1:8317`.
- **Never touch global OpenAI env.** Do not read or write `OPENAI_BASE_URL` / `OPENAI_API_KEY`.
  They feed Hermes chat routing, auxiliary models, TTS and memory plugins; isolating image routing
  from them is the point of the plugin.
- **Secrets through Hermes.** Read keys only via `agent.secret_scope.get_secret` using the variable
  name from `settings.key_env`. Never put a key in `config.yaml`, a URL, a log line or an error.
  Redact gateway error text (`_redact`) before returning it.
- **Settings through `ctx.get_config`.** Non-secret settings live in
  `plugins.entries.openai-compatible.settings` and are declared in `plugin.yaml` `config_schema`.
  No new environment variables for non-secret config. The 1.x `OPENAI_COMPAT_IMAGE_BASE_URL` /
  `OPENAI_COMPAT_IMAGE_MODEL` fallbacks stay for compatibility until a major release removes them.
- **Model precedence:** `image_gen.model` (passed by Hermes as `model`) > legacy env > `gpt-image-2`.
- **Errors are categories the model can act on.** Load source images before the API call and return
  `io_error` with the path. Map HTTP failures to `auth_error` / `rate_limited` / `api_error` with the
  status and redacted upstream message. Never collapse everything into one generic `api_error`
  with the cause logged only at debug level; that made the calling model retry blindly.
- **Public Hermes surface only.** Import from `agent.image_gen_provider`, `agent.secret_scope`
  (with the plain-env fallback) and `agent.file_safety`. Do not import bundled plugin helpers
  (`plugins.image_gen._common`) or other Hermes internals; they move between releases.
- **Keep the stricter source-image loader** (HTTPS only, no redirects, private addresses refused,
  50 MB cap). Output URLs returned by the gateway use Hermes' downloader; that trust boundary is
  documented in the README, not silently widened.
- **User-Agent tracks the manifest.** `PLUGIN_VERSION` in `__init__.py` must equal `version` in
  `plugin.yaml`; a test enforces it. Bump both, plus `CHANGELOG.md`, in the same commit.

## Gateway facts (measured; re-probe before relying on them)

- CPA Codex OAuth passes `gpt-image-1.5`, `gpt-image-2`, `gpt-image-2.5`, `-flare`, `-sunburst`
  through unchanged; other names become a Responses call with an image tool. Requested quality
  collapses to about medium, size is chosen by the backend, and the returned `model` only echoes the
  request. Omitting `quality` renders at `low`, so GPT models always get one.
- New API behind Cloudflare: default Python client signatures can get `403` / `1010`; requests over
  about 125 s are cut with `524` while upstream keeps billing. Do not auto-retry `524`.

## Verify before claiming done

```bash
PYTHONPATH=~/.hermes/hermes-agent PYTHONDONTWRITEBYTECODE=1 \
  ~/.hermes/hermes-agent/venv/bin/python -m unittest discover -s tests -v
hermes plugins doctor . --ci
hermes plugins validate .
```

- Unit tests prove the outgoing request (`model`, `size`, `quality`, headers) and error mapping.
  They do not prove what a gateway rendered: for a live check, generate once and compare
  `requested_size` with `output_size`.
- After pushing, check the GitHub Actions run; a red workflow means not shipped.
- Get one independent read-only review before a release tag or a catalog SHA bump.

## Release and distribution

- Semver tags (`vX.Y.Z`) on `main`; changelog entry per release.
- Discovery is the Hermes plugin catalog (`plugin-catalog/openai-compatible.yaml` in
  NousResearch/hermes-agent), pinned to an exact 40-hex SHA, with `capabilities` matching what the
  plugin registers (no tools/hooks/middleware; `requires_env` lists `OPENAI_COMPAT_IMAGE_API_KEY`).
  Every SHA bump is a new reviewed PR there. The catalog PR is opened by the maintainer, not by an
  agent on its own.
- Installs into a live Hermes (`hermes plugins install --force --ref <sha>`) and gateway restarts are
  the operator's actions.
