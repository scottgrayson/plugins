#!/usr/bin/env python3
"""
Table-driven redact / keep cases for ai_audit.common.redact_secrets.

Also plants secrets into a tiny project and asserts context CLI output
redacts them (stage 1 — no token/facts commands).

Zero network / zero AI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
PLUGIN = Path(__file__).resolve().parents[1]
BIN = PLUGIN / "bin" / "ai-audit"
sys.path.insert(0, str(LIB))

from ai_audit.common import redact_secrets, redact_tree  # noqa: E402
from ai_audit.detect import detect_host  # noqa: E402
from ai_audit.context_audit import run_context_audit  # noqa: E402

# (input, must_not_appear_substring)
REDACT_CASES: list[tuple[str, str]] = [
    ("password=hunter2", "hunter2"),
    ("db password=FakePw99", "FakePw99"),
    ("password=correct-horse-battery-staple", "correct-horse-battery-staple"),
    ("password: Hunter2!", "Hunter2!"),
    ("password: FakeQuoted123", "FakeQuoted123"),
    ('password: "FakeQuoted123"', "FakeQuoted123"),
    ('password = "Fake Pass With Spaces99"', "Fake Pass With Spaces99"),
    ("DB_PASSWORD=short1", "short1"),
    ("DB_PASSWORD=EnvFakePw12345", "EnvFakePw12345"),
    ("TOKEN=FakeTok99", "FakeTok99"),
    ("token: FakeTok12345", "FakeTok12345"),
    ("SECRET=shortFake1", "shortFake1"),
    ("API_KEY=abc123def", "abc123def"),
    ("GITHUB_TOKEN=ghfake", "ghfake"),
    ("pass=FakePass12345", "FakePass12345"),
    ("passphrase: FakePhrase123", "FakePhrase123"),
    ("credentials: FakeCred12345", "FakeCred12345"),
    ("x-api-key: FakeHdr12345678", "FakeHdr12345678"),
    ("MYSQL_PWD=FakeMy123", "FakeMy123"),
    ("DB_PASS=DbPassValue99", "DbPassValue99"),
    ("client_secret=ClientFake9876543", "ClientFake9876543"),
    ("OPENAI_API_KEY=plainvalueABCDEF12", "plainvalueABCDEF12"),
    ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI"),
    ("Prod: DB_PASSWORD=FakeEnvPw99", "FakeEnvPw99"),
    ("config: password=hunter2", "hunter2"),
    ("Note: token=FakeTok12345", "FakeTok12345"),
    ("env: GITHUB_TOKEN=ghfake12", "ghfake12"),
    ("A: password: Hunter2x", "Hunter2x"),
    ("url: https://x.com/?api_key=FakeQ9", "FakeQ9"),
    ("https://x.com/cb?access_token=FakeQueryTok9", "FakeQueryTok9"),
    ("staging=password=hunter2", "hunter2"),
    ("Cookie: session=abcSessionTok99", "abcSessionTok99"),
    ("Set-Cookie: session=SetCookieTok99; Path=/", "SetCookieTok99"),
    ("--password FakeCli123", "FakeCli123"),
    ("--password=FakeCliEquals99", "FakeCliEquals99"),
    ("pw=FakePwValue99", "FakePwValue99"),
    ("pw: FakePwColon99", "FakePwColon99"),
    ("apikey=FakeApiKeyValue9", "FakeApiKeyValue9"),
    ("ApiKey: FakeApiKeyColon9", "FakeApiKeyColon9"),
    ("auth: FakeAuthToken99xx", "FakeAuthToken99xx"),
    ("Bearer abcdefghijklmnop", "abcdefghijklmnop"),
    ("Authorization: Bearer opaqueTokenValue99XYZ", "opaqueTokenValue99XYZ"),
    ("Authorization: Basic dXNlcjpwYXNzMTIzNDU2", "dXNlcjpwYXNzMTIzNDU2"),
    ("ghs_abcdefghijklmnopqrstuv", "ghs_abcdefghijklmnopqrstuv"),
    ("hf_abcdefghijklmnopqrstuv", "hf_abcdefghijklmnopqrstuv"),
    ("glpat-abcdefghijklmnopqrstuv", "glpat-abcdefghijklmnopqrstuv"),
    ("npm_abcdefghijklmnopqrstuvwx", "npm_abcdefghijklmnopqrstuvwx"),
    ("sk_test_51FAKEabcdefghijklmnop", "sk_test_51FAKEabcdefghijklmnop"),
    ("sk_live_51FAKEabcdefghijklmnop", "sk_live_51FAKEabcdefghijklmnop"),
    ("whsec_abcdefghijklmnopqrstuvwx", "whsec_abcdefghijklmnopqrstuvwx"),
    ("crsr_fake0123456789abcdef0123456789", "crsr_fake0123456789"),
    (
        "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        "eyJhbGciOiJIUzI1NiJ9",
    ),
    ("postgres://admin:SuperS3cretPw@db.example/x", "SuperS3cretPw"),
    ("https://scott:ghostPass123@example.com/a", "ghostPass123"),
    ("redis://:EmptyUserPass99ab@db.host/0", "EmptyUserPass99ab"),
    ("'password': 'singleQuotedSecret99'", "singleQuotedSecret99"),
    ('{"password": "JsonFakePw77"}', "JsonFakePw77"),
    ("password=admin", "admin"),
    ("pass=root", "root"),
    ("pwd=test", "test"),
    ("password=Summer", "Summer"),
    ("password: Dragon", "Dragon"),
    ("DB_PASSWORD=Welcome", "Welcome"),
    ("PASSWORD=ADMIN", "ADMIN"),
    ("password=123456", "123456"),
    ("password: 1234", "1234"),
    ("TOKEN=12345678901234567890", "12345678901234567890"),
    ("api_key=1234567890", "1234567890"),
    ("password=o1secretXyz", "o1secretXyz"),
    ("password=v1.2.3", "v1.2.3"),
    ("password=claude-rocks-1", "claude-rocks-1"),
    ("password=/Secret123", "/Secret123"),
    ("secret=~abc123", "~abc123"),
    ("GITHUB_TOKEN=Abcdef", "Abcdef"),
    ("password is FakeIs123", "FakeIs123"),
    ("Set-Cookie: sid=FakeSidTok99xx", "FakeSidTok99xx"),
    ("Bearer abc.def", "abc.def"),
    ("Authorization: token ghfakeToken123", "ghfakeToken123"),
    ("privateKey: FakePrivKey99", "FakePrivKey99"),
    ("secretKey=FakeSecKey99xx", "FakeSecKey99xx"),
    ("apiKey=FakeApiCamel99", "FakeApiCamel99"),
    ("ghu_abcdefghijklmnopqrstuv", "ghu_abcdefghijklmnopqrstuv"),
    ("ghr_abcdefghijklmnopqrstuv", "ghr_abcdefghijklmnopqrstuv"),
    ("db password=admin", "admin"),
    ("password: dragon", "dragon"),
    ("password: monkey", "monkey"),
    ("password: sunshine", "sunshine"),
    ("PASSWORD: iloveyou", "iloveyou"),
    ("DB_PASSWORD: welcome", "welcome"),
    ("pwd: qwerty", "qwerty"),
    ("token: hunter", "hunter"),
    ("secret: banana", "banana"),
    ("api_key: abcdefghij", "abcdefghij"),
    ("pw: shadow", "shadow"),
    ("auth: trustno", "trustno"),
    ("token: Hunter", "Hunter"),
    ("secret: Banana", "Banana"),
    ("password: correct horse", "correct horse"),
]

KEEP_CASES: list[str] = [
    "Max token: 8192",
    "Default token: 4096 limit",
    "Secret: 3 rules to follow",
    "token: 2 per request",
    "Password: 8+ chars required",
    "token: v2",
    "pwd=/Users/scott/Code",
    "PWD=/home/box",
    "pass=1 fail=0",
    "PASS=12 FAIL=0",
    "DEFAULT_TOKEN=gpt-4o",
    "is_token=true",
    "has_secret=false",
    "csrf_token=enabled",
    "NEXT_PAGE_TOKEN=null",
    "token=null",
    "secret=true",
    "password=<your password>",
    "Key: use pnpm not npm",
    "API key: stored in 1Password",
    "primary_key: user_id",
    "sort_key: created_at",
    "cache_key: v2-node20",
    "MAX_TOKEN=4096",
    "pwd: /Users/x",
    "password: none needed",
    "token: claude-sonnet-4-5",
    "token: gpt-4o-mini-2024",
    "token: grok-2",
    "token: gemini-1.5-pro",
    "token: o1-preview",
    "Default token: Composer is the default model",
    "Explain token: what actually counts",
    "secret: the deploy key",
    "MONKEY_KEY=banana",
    "REACT_APP_PUBLIC_KEY=pk_test_x",
    "Basic authentication is disabled",
    "Use Bearer authentication for the API",
    "Bearer of bad news",
    "Basic setup: install pnpm",
    "password=${DB_PASSWORD}",
    "password=$TOKEN",
    "token=<redacted>",
    "secret=***",
    "API_KEY=changeme",
    "/tmp/r10fp/abcdefghijklmnopqrstuv/CLAUDE.md",
    "/home/box/sand-data/workflows/why/references/epistemics.md",
    "commit abcdef1",
    "id 550e8400-e29b-41d4-a716-446655440000",
    "token: optional",
    "password: changed",
    "Authorization: required",
    "password: reset",
    "token: expired",
    "secret: unknown",
    "session: deprecated",
    "auth: revoked",
    "Tests: pass=196 failed=0",
    "PASS: 12 FAIL: 0",
    "pass=3 failures=1",
]


def _assert_redact(raw: str, leak: str) -> None:
    got = redact_secrets(raw)
    assert leak not in got, f"REDACT leak remains:\n  in:  {raw!r}\n  out: {got!r}\n  leak:{leak!r}"
    assert "REDACTED" in got, f"REDACT missing marker:\n  in:  {raw!r}\n  out: {got!r}"


def _assert_keep(raw: str) -> None:
    got = redact_secrets(raw)
    assert got == raw, f"KEEP changed:\n  in:  {raw!r}\n  out: {got!r}"


def _run_cli(args: list[str]) -> str:
    e = os.environ.copy()
    e["PYTHONPATH"] = str(LIB) + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [str(BIN), *args],
        capture_output=True,
        text=True,
        env=e,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"cli failed {args}: {out}"
    return out


def run_context_secret_e2e() -> None:
    """Plant secrets into Grok bot name meta; assert context CLI redacts them."""
    fix = PLUGIN / "fixtures" / "grok"
    td = Path(tempfile.mkdtemp(prefix="ai-audit-ctx-secret."))
    try:
        grok = td / "grok"
        shutil.copytree(fix, grok)
        profile = grok / "sand-data" / "agents" / "agent-active" / "profile.json"
        # Inventory emits bot name as a string leaf; redact_tree must scrub it.
        plants = [
            raw
            for raw, _ in REDACT_CASES
            if "\n" not in raw and len(raw) < 80 and "=" in raw
        ][:12]
        profile.write_text(
            json.dumps({"name": " | ".join(plants), "description": "test"}, indent=2),
            encoding="utf-8",
        )
        out_json = _run_cli(
            ["context", "--host", "grok", "--root", str(grok), "--json-only"]
        )
        out_md = _run_cli(
            ["context", "--host", "grok", "--root", str(grok), "--md-only"]
        )
        blob = out_json + "\n" + out_md
        assert "REDACTED" in blob, blob[:500]
        for raw, leak in REDACT_CASES:
            if raw not in plants:
                continue
            assert raw not in blob, f"context still has raw {raw!r}"
            if len(leak) >= 8 and not any(leak in k for k in KEEP_CASES):
                assert leak not in blob, f"context leak {leak!r} from {raw!r}"
    finally:
        shutil.rmtree(td, ignore_errors=True)


def run_project_eq_home_context_dedupe() -> None:
    """project root == HOME must not double-list ~/.cursor/rules in inventory."""
    td = Path(tempfile.mkdtemp(prefix="ai-audit-homeproj."))
    try:
        home = td / "home"
        rules = home / ".cursor" / "rules"
        rules.mkdir(parents=True)
        (rules / "dup.mdc").write_text(
            "---\ndescription: d\n---\n- Always use the shared lint config.\n",
            encoding="utf-8",
        )
        roots = detect_host(host="cursor", home=home, project=home)
        assert roots.project.resolve() == roots.home.resolve()
        inv = run_context_audit(roots)["inventory"]
        rule_rows = [
            i for i in inv if i.get("kind") == "rule" and i["path"].endswith("dup.mdc")
        ]
        assert len(rule_rows) == 1, rule_rows
    finally:
        shutil.rmtree(td, ignore_errors=True)


def run_all() -> tuple[int, int]:
    for raw, leak in REDACT_CASES:
        _assert_redact(raw, leak)
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----"
        + chr(10)
        + "MIIEowIBAAKCAQEAfakePEM"
        + chr(10)
        + "-----END RSA PRIVATE KEY-----"
    )
    _assert_redact(pem, "MIIEowIBAAKCAQEAfakePEM")
    pgp = (
        "-----BEGIN PGP PRIVATE KEY BLOCK-----"
        + chr(10)
        + "lQHYFakePGPMaterial123"
        + chr(10)
        + "-----END PGP PRIVATE KEY BLOCK-----"
    )
    _assert_redact(pgp, "lQHYFakePGPMaterial123")
    slack_tok = "FAKE" + ("x" * 20)
    slack_url = "https://hooks.slack.com/services/%s/%s/%s" % (
        "TFAKE0000",
        "BFAKE0000",
        slack_tok,
    )
    _assert_redact(slack_url, slack_tok)
    path = "/home/box/sand-data/workflows/why/references/epistemics.md"
    tree = {"inventory": [{"path": path, "notes": "db password=Hunter2FakePass!99"}]}
    out = redact_tree(tree)
    assert out["inventory"][0]["path"] == path
    assert "Hunter2FakePass" not in out["inventory"][0]["notes"]

    for raw in KEEP_CASES:
        _assert_keep(raw)

    run_context_secret_e2e()
    run_project_eq_home_context_dedupe()

    return len(REDACT_CASES) + 3, len(KEEP_CASES)


def main() -> int:
    redact_n, keep_n = run_all()
    print(f"PASS redact_cases redact={redact_n} keep={keep_n} total={redact_n + keep_n}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        print(f"FAIL {e}", file=sys.stderr)
        raise SystemExit(1)
