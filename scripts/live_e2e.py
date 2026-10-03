"""Live end-to-end check of this plugin against real gateways, without touching any real profile.

Each run builds a throwaway HERMES_HOME, copies this repo's plugin into it, loads it through Hermes'
real plugin discovery and image_gen registry, and calls real gateways through the real ``openai``
SDK. Gateway keys are read in-process from the variable you name; they are never printed.

    PYTHONPATH=~/.hermes/hermes-agent ~/.hermes/hermes-agent/venv/bin/python scripts/live_e2e.py \\
        --gateway cpa=http://127.0.0.1:8317/v1=CPA_KEY_VAR \\
        --gateway newapi=env:MY_NEWAPI_BASE_URL_VAR=NEWAPI_KEY_VAR

``--gateway label=<base_url | env:VAR>=<key variable name>``; ``env:VAR`` reads the base URL from an
environment variable so it stays out of shell history. Gateway URLs and hosts, the throwaway home and
tailnet names are scrubbed from every printed line, including plugin error text. Per gateway it spends three
image generations (generate, edit from a local file, edit from a public HTTPS URL). The refusal
checks never reach a gateway. Exit code 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_URL = "https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_272x92dp.png"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
KEEP = ("success", "model", "modality", "api_model", "requested_size", "requested_quality",
        "output_size", "error_type", "error")

failures: list[str] = []
# Strings that must never reach output (gateway URLs and hosts, the throwaway home, tailnet names).
# Every printed detail goes through check(), which replaces them; register before printing.
_scrub: dict[str, str] = {}


def scrub(text: object) -> str:
    out = str(text)
    for secret, label in sorted(_scrub.items(), key=lambda kv: -len(kv[0])):
        out = out.replace(secret, label)
    return out


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {scrub(detail)}" if detail != "" else ""))
    if not ok:
        failures.append(name)


def host_class(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return "loopback"
    return f"{urlparse(url).scheme}://<redacted host>"


def resolves_only_to_cgnat(name: str) -> bool:
    import ipaddress
    import socket
    try:
        addrs = {ipaddress.ip_address(str(i[4][0]).split("%")[0]) for i in socket.getaddrinfo(name, 443)}
    except OSError:
        return False
    cgnat = ipaddress.ip_network("100.64.0.0/10")
    return bool(addrs) and all(a.version == 4 and a in cgnat for a in addrs)


def tailnet_url() -> tuple[str | None, str]:
    """``(https://<this node>.ts.net/x.png, note)`` when that name resolves only to CGNAT here.

    With MagicDNS off, or Funnel on, the name resolves to Tailscale's public relays; it is then a
    public host and the refusal check would prove nothing, so it is skipped (unit tests cover it).
    """
    if not shutil.which("tailscale"):
        return None, "Tailscale not available on this host"
    try:
        out = subprocess.run(["tailscale", "status", "--self", "--json"], capture_output=True,
                             text=True, timeout=15, check=True).stdout
        name = json.loads(out)["Self"]["DNSName"].rstrip(".")
    except Exception:  # noqa: BLE001 - optional probe
        return None, "could not read this node's tailnet name"
    if resolves_only_to_cgnat(name):
        return f"https://{name}/x.png", ""
    return None, "this node's *.ts.net name does not resolve to CGNAT here (MagicDNS off or Funnel)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gateway", action="append", required=True, metavar="LABEL=URL=KEY_ENV")
    ap.add_argument("--model", default="gpt-image-2.5-sunburst-low",
                    help="image_gen.model to request (default: %(default)s)")
    ap.add_argument("--source-url", default=DEFAULT_SOURCE_URL, help="public HTTPS PNG used as an edit source")
    ap.add_argument("--out", default=os.environ.get("TMPDIR") or tempfile.gettempdir(),
                    help="directory for copies of the generated images")
    ap.add_argument("--refusals-only", action="store_true",
                    help="skip the paid generate/edit legs; run only the refusal checks")
    args = ap.parse_args()

    gateways = []
    for spec in args.gateway:
        label, url, key_env = spec.split("=", 2)
        if url.startswith("env:"):
            url = os.environ.get(url[4:], "").strip()
            if not url:
                ap.error(f"{label}: base URL variable {spec.split('=')[1][4:]} is not set")
        if not os.environ.get(key_env):
            ap.error(f"{label}: key variable {key_env} is not set")
        gateways.append((label, url, key_env))
        # Register the URL, its host and its rstripped form so plugin error text can't leak them.
        redacted = f"<{label} gateway>"
        _scrub[url] = _scrub[url.rstrip("/")] = redacted
        host = urlparse(url).netloc
        if host:
            _scrub[host] = f"<{label} host>"

    home = Path(tempfile.mkdtemp(prefix="openai-compat-e2e-"))
    _scrub[str(home)] = "<home>"
    out_dir = Path(args.out)
    os.environ["HERMES_HOME"] = str(home)
    for name in [n for n in os.environ if n.startswith("OPENAI_COMPAT_IMAGE_")]:
        if not any(name == key_env for _, _, key_env in gateways):
            del os.environ[name]  # legacy 1.x fallbacks must not mask the settings under test
    # Present on purpose: neither may reach a gateway.
    os.environ["OPENAI_ORG_ID"], os.environ["OPENAI_PROJECT_ID"] = "org-e2e-must-not-leak", "proj-e2e-must-not-leak"

    plugin_dir = home / "plugins" / "openai-compatible"
    shutil.copytree(REPO, plugin_dir, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "tests", "scripts", ".github"))

    def write_config(base_url: str, key_env: str) -> None:
        (home / "config.yaml").write_text(
            "plugins:\n  enabled: [openai-compatible]\n  entries:\n    openai-compatible:\n      settings:\n"
            f"        base_url: {base_url}\n        key_env: {key_env}\n"
            f"image_gen:\n  provider: openai-compatible\n  model: {args.model}\n", encoding="utf-8")

    write_config(gateways[0][1], gateways[0][2])
    import httpx
    import openai
    from hermes_cli.plugins import discover_plugins

    discover_plugins(force=True)
    from agent.image_gen_registry import get_active_provider

    provider = get_active_provider()
    module = sys.modules[type(provider).__module__]
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    print(f"plugin {module.PLUGIN_VERSION} from {Path(module.__file__).parent.name} @ {head[:12]}"
          f"{' (DIRTY tree)' if dirty else ''}; model {args.model}")
    check("loaded the copy under test", Path(module.__file__).resolve().is_relative_to(home.resolve()))

    # Count gateway clients and record every outgoing request's headers, on the real SDK.
    clients: list[int] = []
    sent_headers: list[dict] = []
    real_openai = openai.OpenAI

    def recording_openai(**kwargs):
        clients.append(1)
        hook = lambda request: sent_headers.append({k.lower(): v for k, v in request.headers.items()})  # noqa: E731
        return real_openai(http_client=openai.DefaultHttpxClient(event_hooks={"request": [hook]}), **kwargs)

    openai.OpenAI = recording_openai

    def run(label: str, prompt: str, **kw) -> dict:
        result = provider.generate(prompt, "landscape", model=args.model, **kw)
        return {k: result.get(k) for k in KEEP if result.get(k) is not None} | {"image": result.get("image")}

    def is_png(path: str | None) -> bool:
        return bool(path) and Path(path).read_bytes()[:8] == PNG_MAGIC

    for label, url, key_env in ([] if args.refusals_only else gateways):
        print(f"\n== {label}: {host_class(url)}, key from {key_env}")
        write_config(url, key_env)
        sent_headers.clear()

        gen = run(label, "A small orange pixel-art crab on a cream desk next to a coffee mug, flat "
                         "illustration. No text, no logos.")
        check("generate", bool(gen.get("success")), {k: v for k, v in gen.items() if k != "image"})
        if not gen.get("success"):
            continue
        check("generate returned a PNG with decoded size", is_png(gen["image"]) and "output_size" in gen)
        shutil.copy(gen["image"], out_dir / f"openai-compat-e2e-{label}-gen.png")

        edit = run(label, "Keep the same scene. Give the crab a tiny red scarf. No text.", image_url=gen["image"])
        check("edit from local file", bool(edit.get("success")) and edit.get("modality") == "image",
              {k: v for k, v in edit.items() if k != "image"})
        if edit.get("success"):
            shutil.copy(edit["image"], out_dir / f"openai-compat-e2e-{label}-edit.png")

        url_edit = run(label, "Redraw this logo as a hand-painted wooden sign. No extra text.",
                       image_url=args.source_url)
        check("edit from public HTTPS URL (SSRF-guarded fetch)", bool(url_edit.get("success")),
              {k: v for k, v in url_edit.items() if k != "image"})
        if url_edit.get("success"):
            shutil.copy(url_edit["image"], out_dir / f"openai-compat-e2e-{label}-urledit.png")

        leaked = [h for h in sent_headers if "openai-organization" in h or "openai-project" in h]
        check("no OpenAI-Organization / OpenAI-Project header sent", bool(sent_headers) and not leaked,
              f"{len(sent_headers)} request(s) inspected")
        check("plugin User-Agent sent", all(h.get("user-agent") == module.USER_AGENT for h in sent_headers))

    print("\n== refusals (must fail as io_error before any gateway request)")
    write_config(gateways[0][1], gateways[0][2])
    not_image = home / "notes.png"
    not_image.write_bytes(b"api_key = this is not an image\n")
    blocked = "private, local or unresolvable"
    cases = [("CGNAT/Tailscale literal https://100.100.100.100", "https://100.100.100.100/x.png", blocked),
             ("Tailscale IPv6 ULA literal", "https://[fd7a:115c:a1e0::53]/x.png", blocked),
             ("metadata https://169.254.169.254", "https://169.254.169.254/latest/x.png", blocked),
             ("loopback https://127.0.0.1", "https://127.0.0.1/x.png", blocked),
             ("plain http source URL", "http://example.com/x.png", "must use HTTPS"),
             ("non-image local file", str(not_image), "not a PNG, JPEG, WebP or GIF"),
             ("missing local file", str(home / "missing.png"), "source image not found")]
    ts, ts_note = tailnet_url()
    if ts:
        _scrub[ts] = "<tailnet-url>"
        _scrub[urlparse(ts).netloc] = "<tailnet-host>"
        cases.insert(0, ("this node's *.ts.net hostname (redacted)", ts, blocked))
    else:
        print(f"  [SKIP] *.ts.net hostname: {ts_note}")
    # The review's scenario: a DNS name (not a literal) that resolves to CGNAT, via public wildcard DNS.
    cgnat_name = "100-100-1-2.nip.io"
    if resolves_only_to_cgnat(cgnat_name):
        cases.insert(0, (f"DNS name resolving to CGNAT ({cgnat_name})", f"https://{cgnat_name}/x.png", blocked))
    else:
        print(f"  [SKIP] {cgnat_name}: does not resolve to CGNAT from this host")
    for name, source, expected in cases:
        before = len(clients)
        res = run("refusal", "probe", image_url=source)
        error = res.get("error") or ""
        check(name, res.get("error_type") == "io_error" and expected in error and len(clients) == before,
              scrub(error)[:100])

    openai.OpenAI = real_openai
    shutil.rmtree(home, ignore_errors=True)
    print(f"\nimages copied to {out_dir}; throwaway home removed: {not home.exists()}")
    print(f"RESULT: {'PASS' if not failures else 'FAIL: ' + ', '.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
