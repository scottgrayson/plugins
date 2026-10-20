#!/usr/bin/env python3
"""
Table-driven redact / keep cases for ai_audit.common.redact_secrets.

Zero network / zero AI. Run via:
  PYTHONPATH=plugins/ai-audit/lib python3 -B plugins/ai-audit/tests/test_redact_cases.py
or from plugins/ai-audit/tests/run-tests.sh.
"""

from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))

from ai_audit.common import redact_secrets, redact_tree  # noqa: E402

# (input, must_not_appear_substring) — after redaction the leak must be gone
# and "REDACTED" must appear (unless noted).
REDACT_CASES: list[tuple[str, str]] = [
    # --- rounds 10–13 core ---
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
    # --- R5 nested / labeled / query / cookie ---
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
    # --- residual shapes ---
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
]

KEEP_CASES: list[str] = [
    # --- R6 ---
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
    # --- round-11 keep list ---
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
    "Default token: Composer model",
    "Explain token: what counts",
    "secret: the deploy key",
    "MONKEY_KEY=banana",
    "REACT_APP_PUBLIC_KEY=pk_test_x",
    "Basic authentication is disabled",
    "Use Bearer authentication for the API",
    "Bearer of bad news",
    "Basic setup: install pnpm",
    # placeholders
    "password=${DB_PASSWORD}",
    "password=$TOKEN",
    "token=<redacted>",
    "secret=***",
    "API_KEY=changeme",
    # paths / ids
    "/tmp/r10fp/abcdefghijklmnopqrstuv/CLAUDE.md",
    "/home/box/sand-data/workflows/why/references/epistemics.md",
    "commit abcdef1",
    "id 550e8400-e29b-41d4-a716-446655440000",
]


def _assert_redact(raw: str, leak: str) -> None:
    got = redact_secrets(raw)
    assert leak not in got, f"REDACT leak remains:\n  in:  {raw!r}\n  out: {got!r}\n  leak:{leak!r}"
    assert "REDACTED" in got, f"REDACT missing marker:\n  in:  {raw!r}\n  out: {got!r}"


def _assert_keep(raw: str) -> None:
    got = redact_secrets(raw)
    assert got == raw, f"KEEP changed:\n  in:  {raw!r}\n  out: {got!r}"


def run_all() -> tuple[int, int]:
    for raw, leak in REDACT_CASES:
        _assert_redact(raw, leak)
    # PEM / PGP / Slack (runtime-built) extras
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
    # inventory path field never rewritten
    path = "/home/box/sand-data/workflows/why/references/epistemics.md"
    tree = {"inventory": [{"path": path, "notes": "db password=Hunter2FakePass!99"}]}
    out = redact_tree(tree)
    assert out["inventory"][0]["path"] == path
    assert "Hunter2FakePass" not in out["inventory"][0]["notes"]

    for raw in KEEP_CASES:
        _assert_keep(raw)

    redact_n = len(REDACT_CASES) + 3  # + pem/pgp/slack
    keep_n = len(KEEP_CASES)
    return redact_n, keep_n


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
