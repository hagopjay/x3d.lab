"""Find and redact secrets that a Colab "Save a copy in GitHub" carries in a notebook's outputs.

    python tools/sanitize_notebooks.py                 # check every notebook under notebooks/; exit 1 if anything is found
    python tools/sanitize_notebooks.py --fix           # redact in place, keep every other output (pictures, tables, logs)
    python tools/sanitize_notebooks.py --fix FILE ...  # only these files

What counts as a secret here:
- the MCP bearer token, wherever it appears: after "Bearer ", and then the same value anywhere else (the blurred
  "Password" box shows it bare);
- tunnel addresses (trycloudflare.com, ngrok-free.app / ngrok.app / ngrok.io / ngrok.dev), which live until the runtime stops;
- Cloudflare tunnel tokens (base64 JSON with the account tag, tunnel id and secret), GitHub tokens, NVIDIA keys,
  ngrok authtokens.
Redaction rewrites text only; the notebook stays valid JSON with the same cells and outputs.
The check reads the raw file, so it also sees widget state and metadata, not just cell outputs.
"""

import argparse
import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BEARER = re.compile(r"(Bearer\s+)([A-Za-z0-9_\-]{20,})")
TUNNEL_URL = re.compile(r"https?://[-a-z0-9]+\.(trycloudflare\.com|ngrok-free\.app|ngrok-free\.dev|ngrok\.app|ngrok\.io|ngrok\.dev)")
CF_TOKEN = re.compile(r"eyJ[A-Za-z0-9+/=_\-]{60,}")
OTHER = {
    "github token": re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "nvidia key": re.compile(r"\bnvapi-[A-Za-z0-9_\-]{30,}"),
    "ngrok authtoken": re.compile(r"\b[0-9][A-Za-z0-9]{24,}_[0-9A-Za-z]{18,}\b"),
}


def _is_cf_token(s: str) -> bool:
    try:
        d = json.loads(base64.b64decode(s + "=" * (-len(s) % 4)))
    except Exception:
        return False
    return isinstance(d, dict) and {"a", "t", "s"} <= set(d)


def findings(text: str):
    """[(kind, value)] for everything that should not be committed."""
    out = []
    bearers = {m.group(2) for m in BEARER.finditer(text)}
    out += [("bearer token", b) for b in sorted(bearers)]
    out += [("tunnel address", m.group(0)) for m in TUNNEL_URL.finditer(text)
            if not re.match(r"https?://(api|your|example|my-name)[.-]", m.group(0))]
    out += [("cloudflare tunnel token", m.group(0)) for m in CF_TOKEN.finditer(text) if _is_cf_token(m.group(0))]
    for kind, rx in OTHER.items():
        out += [(kind, m.group(0)) for m in rx.finditer(text)]
    seen, uniq = set(), []
    for kind, v in out:
        if v not in seen:
            seen.add(v); uniq.append((kind, v))
    return uniq


def redact(text: str) -> str:
    for kind, value in findings(text):
        if kind == "tunnel address":
            host = value.split("://", 1)[1]
            repl = value.split("://", 1)[0] + "://<redacted-tunnel>." + host.split(".", 1)[1]
        else:
            repl = "<redacted %s>" % kind
        text = text.replace(value, repl)   # every occurrence: the bare token in the password box too
    return text


def notebooks(paths):
    if paths:
        return [Path(p) for p in paths]
    return sorted(p for p in (ROOT / "notebooks").rglob("*.ipynb") if ".ipynb_checkpoints" not in p.parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fix", action="store_true", help="redact in place")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv)
    dirty = 0
    for nb in notebooks(args.files):
        text = nb.read_text(encoding="utf-8")
        found = findings(text)
        if not found:
            continue
        dirty += 1
        rel = nb.relative_to(ROOT) if nb.is_relative_to(ROOT) else nb
        kinds = sorted({k for k, _ in found})
        if args.fix:
            fixed = redact(text)
            json.loads(fixed)                      # still a valid notebook
            assert not findings(fixed), rel
            nb.write_text(fixed, encoding="utf-8")
            print("redacted %s: %s" % (rel, ", ".join(kinds)))
        else:
            # never print the values themselves: this output can end up in CI logs
            print("SECRET in %s: %s" % (rel, ", ".join("%s (%s…)" % (k, v[:4]) for k, v in found)))
    if dirty and not args.fix:
        print("%d notebook(s) carry secrets. Run: python tools/sanitize_notebooks.py --fix" % dirty)
        return 1
    if not dirty:
        print("clean: no secrets in %d notebook(s)" % len(notebooks(args.files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
