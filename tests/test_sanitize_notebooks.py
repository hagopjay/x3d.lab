"""tools/sanitize_notebooks.py: secrets that Colab's "Save a copy in GitHub" carries in outputs."""
import base64
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sanitize_notebooks", ROOT / "tools" / "sanitize_notebooks.py")
san = importlib.util.module_from_spec(spec); spec.loader.exec_module(san)

TOKEN = "Zq3_fakeBearerTokenForTests_0123456789abcdef"     # 43 chars, like secrets.token_urlsafe(32)


def _nb(*texts):
    return json.dumps({"cells": [{"cell_type": "code", "source": "x", "metadata": {}, "execution_count": 1,
                                  "outputs": [{"output_type": "stream", "name": "stdout", "text": list(texts)}]}],
                       "metadata": {}, "nbformat": 4, "nbformat_minor": 5})


def test_bearer_is_redacted_everywhere_including_the_bare_password_box():
    text = _nb("claude mcp add x --header \"Authorization: Bearer %s\"\n" % TOKEN, "<pre>%s</pre>\n" % TOKEN)
    out = san.redact(text)
    assert TOKEN not in out and out.count("<redacted bearer token>") == 2
    json.loads(out)


def test_tunnel_addresses_are_redacted_but_docs_examples_are_not():
    text = _nb("https://weekly-fake-words.trycloudflare.com/mcp\n", "https://68fc-1-2-3-4.ngrok-free.app/mcp\n",
               "not https://api.trycloudflare.com nor https://my-name.ngrok-free.app\n")
    kinds = [k for k, _ in san.findings(text)]
    assert kinds == ["tunnel address", "tunnel address"]
    out = san.redact(text)
    assert "weekly-fake-words" not in out and "68fc" not in out and "my-name.ngrok-free.app" in out


def test_cloudflare_tunnel_token_is_recognised_by_its_json():
    tok = base64.b64encode(json.dumps({"a": "0" * 32, "t": "1" * 36, "s": "c2VjcmV0" * 4}).encode()).decode()
    assert [k for k, _ in san.findings(_nb("run --token " + tok))] == ["cloudflare tunnel token"]
    other = base64.b64encode(json.dumps({"alg": "none", "typ": "JWT", "pad": "x" * 60}).encode()).decode()
    assert san.findings(_nb(other)) == []                   # some other base64 JSON is left alone


def test_check_never_prints_the_secret(tmp_path, capsys):
    f = tmp_path / "n.ipynb"; f.write_text(_nb("Bearer " + TOKEN))
    assert san.main([str(f)]) == 1
    assert TOKEN not in capsys.readouterr().out
    assert san.main(["--fix", str(f)]) == 0 and TOKEN not in f.read_text()
    assert san.main([str(f)]) == 0


def test_every_committed_notebook_is_clean():
    dirty = [str(p.relative_to(ROOT)) for p in san.notebooks([]) if san.findings(p.read_text(encoding="utf-8"))]
    assert not dirty, "secrets in %s: run python tools/sanitize_notebooks.py --fix" % dirty
