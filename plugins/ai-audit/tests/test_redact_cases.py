#!/usr/bin/env python3
"""
Table-driven redact / keep cases for ai_audit.common.redact_secrets.

Also exercises CLI end-to-end: plant each case into a fixtures tree and assert
on real `facts` (md+json) and `token --usage-period` output.

Zero network / zero AI. Run via:
  PYTHONPATH=plugins/ai-audit/lib python3 -B plugins/ai-audit/tests/test_redact_cases.py
or from plugins/ai-audit/tests/run-tests.sh.
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
FIX = PLUGIN / "fixtures"
sys.path.insert(0, str(LIB))

from ai_audit.common import redact_secrets, redact_tree  # noqa: E402
from ai_audit.detect import detect_host  # noqa: E402
from ai_audit.facts_audit import run_facts_audit  # noqa: E402

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
    # --- R7 weak / numeric / model-prefix / extra shapes ---
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
    # --- round-15: colon single-word weak passwords (status allowlist only) ---
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
    "Default token: Composer is the default model",
    "Explain token: what actually counts",
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
    # --- status-word allowlist + pass/fail variants ---
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


def _run_cli(args: list[str], env: dict[str, str] | None = None) -> str:
    e = os.environ.copy()
    e["PYTHONPATH"] = str(LIB) + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
    if env:
        e.update(env)
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


def _leak_is_distinctive(leak: str) -> bool:
    """Short common words (admin/root/test) appear elsewhere; only check distinctive leaks."""
    if any(leak in k for k in KEEP_CASES):
        return False  # e.g. abcdefghijklmnop inside a kept path
    if len(leak) >= 8:
        return True
    if any(c.isdigit() for c in leak) and len(leak) >= 4:
        return True
    return False


def run_cli_e2e() -> None:
    """Plant every table case into fixtures; assert facts + usage-period output."""
    # --- facts: plant into Grok profile ---
    td = Path(tempfile.mkdtemp(prefix="ai-audit-redact-e2e."))
    try:
        grok = td / "grok"
        shutil.copytree(FIX / "grok", grok)
        profile = next(grok.glob("sand-data/user-memory/by-agent/*/profile.md"))
        lines = []
        for raw, _leak in REDACT_CASES:
            if "\n" in raw:
                continue
            lines.append(f"- (2025-06-01) planted {raw}")
        for raw in KEEP_CASES:
            if "\n" in raw:
                continue
            lines.append(f"- (2025-06-01) keepcase {raw}")
        with profile.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

        facts_json = _run_cli(
            ["facts", "--host", "grok", "--root", str(grok), "--json-only"]
        )
        facts_md = _run_cli(
            ["facts", "--host", "grok", "--root", str(grok), "--md-only"]
        )
        for raw, leak in REDACT_CASES:
            if "\n" in raw:
                continue
            # Unredacted assignment must not survive in either channel.
            assert raw not in facts_json, f"facts json still has raw {raw!r}"
            assert raw not in facts_md, f"facts md still has raw {raw!r}"
            if _leak_is_distinctive(leak):
                assert leak not in facts_json, f"facts json leak {leak!r} from {raw!r}"
                assert leak not in facts_md, f"facts md leak {leak!r} from {raw!r}"
        assert "REDACTED" in facts_json and "REDACTED" in facts_md
        for raw in KEEP_CASES:
            if "\n" in raw:
                continue
            # Keep text must survive in json (md quotes truncate at 80 chars)
            assert raw in facts_json or raw[:70] in facts_json, (
                f"facts lost keep case {raw!r}"
            )

        # --- token --usage-period (needs --usage-total for period to surface) ---
        for raw, leak in REDACT_CASES:
            if "\n" in raw or len(raw) > 180:
                continue
            # Values starting with "-" would be parsed as CLI flags; use --usage-period=…
            period_arg = f"--usage-period={raw}"
            out_json = _run_cli(
                [
                    "token",
                    "--host",
                    "grok",
                    "--root",
                    str(grok),
                    "--usage-total",
                    "9",
                    period_arg,
                    "--json-only",
                ]
            )
            out_md = _run_cli(
                [
                    "token",
                    "--host",
                    "grok",
                    "--root",
                    str(grok),
                    "--usage-total",
                    "9",
                    period_arg,
                    "--md-only",
                ]
            )
            assert raw not in out_json, f"usage-period json still has raw {raw!r}"
            assert raw not in out_md, f"usage-period md still has raw {raw!r}"
            if _leak_is_distinctive(leak):
                assert leak not in out_json, f"usage-period json leak {leak!r} from {raw!r}"
                assert leak not in out_md, f"usage-period md leak {leak!r} from {raw!r}"
            assert "REDACTED" in out_json or "REDACTED" in out_md, (
                f"usage-period missing REDACTED for {raw!r}"
            )
        # Explicit R7 usage-period shape
        admin_out = _run_cli(
            [
                "token",
                "--host",
                "grok",
                "--root",
                str(grok),
                "--usage-total",
                "9",
                "--usage-period",
                "db password=admin",
                "--md-only",
            ]
        )
        assert "password=admin" not in admin_out
        assert "REDACTED" in admin_out

        for raw in KEEP_CASES:
            if "\n" in raw or len(raw) > 180:
                continue
            out_json = _run_cli(
                [
                    "token",
                    "--host",
                    "grok",
                    "--root",
                    str(grok),
                    "--usage-total",
                    "9",
                    f"--usage-period={raw}",
                    "--json-only",
                ]
            )
            blob = json.dumps(json.loads(out_json))
            assert raw in blob, f"usage-period json altered keep {raw!r} -> {blob[:200]}"
    finally:
        shutil.rmtree(td, ignore_errors=True)


def run_project_eq_home_dedupe() -> None:
    """project root == HOME must not double-count ~/.cursor/rules facts."""
    td = Path(tempfile.mkdtemp(prefix="ai-audit-homeproj."))
    try:
        home = td / "home"
        rules = home / ".cursor" / "rules"
        rules.mkdir(parents=True)
        (rules / "dup.mdc").write_text(
            "---\ndescription: d\n---\n"
            "- Always use the shared lint config for this workspace.\n"
            "- Prefer small pull requests over large ones.\n",
            encoding="utf-8",
        )
        (home / "AGENTS.md").write_text("# Agents\n\n- Ship small changes daily.\n", encoding="utf-8")
        # project == home (cwd-style layout)
        roots = detect_host(host="cursor", home=home, project=home)
        assert roots.project.resolve() == roots.home.resolve()
        result = run_facts_audit(roots, stale_days=30)
        facts = result["facts"]
        texts = [f["text"] for f in facts]
        assert texts.count("Always use the shared lint config for this workspace.") == 1, texts
        assert texts.count("Prefer small pull requests over large ones.") == 1, texts
        dups = [i for i in result["issues"] if i["kind"] == "duplicate"]
        assert dups == [], dups
        # Inventory also one row per rule file
        from ai_audit.context_audit import run_context_audit

        ctx = run_context_audit(roots)
        rule_rows = [
            i
            for i in ctx["inventory"]
            if i.get("kind") == "rule" and i["path"].endswith("dup.mdc")
        ]
        assert len(rule_rows) == 1, rule_rows
    finally:
        shutil.rmtree(td, ignore_errors=True)


def run_same_file_duplicate_lines() -> None:
    """A line written twice in one file must still be reported as a duplicate."""
    td = Path(tempfile.mkdtemp(prefix="ai-audit-samedup."))
    try:
        home = td / "home"
        rules = home / ".cursor" / "rules"
        rules.mkdir(parents=True)
        (rules / "c.mdc").write_text(
            "---\ndescription: d\n---\n"
            "- Same line twice for duplicate detection.\n"
            "- Same line twice for duplicate detection.\n",
            encoding="utf-8",
        )
        roots = detect_host(host="cursor", home=home, project=home)
        result = run_facts_audit(roots, stale_days=30)
        texts = [f["text"] for f in result["facts"]]
        assert texts.count("Same line twice for duplicate detection.") == 2, texts
        dups = [i for i in result["issues"] if i["kind"] == "duplicate"]
        assert len(dups) >= 1, result["issues"]
        assert "Same line twice" in dups[0]["detail"]
    finally:
        shutil.rmtree(td, ignore_errors=True)


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

    run_cli_e2e()
    run_project_eq_home_dedupe()
    run_same_file_duplicate_lines()

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
