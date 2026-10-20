#!/usr/bin/env python3
"""
Table-driven redact / keep / expected-overredact cases for ai_audit.common.redact_secrets.

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
    # --- round-16 D1: secret + trailing words (whole value redacts) ---
    ("password: Hunter2FakePass99 for prod", "Hunter2FakePass99"),
    ("token: ghfakeTok123 for CI", "ghfakeTok123"),
    ("api_key: abc123def456 is prod", "abc123def456"),
    ("DB_PASSWORD: Welcome1 on staging", "Welcome1"),
    ("password: FakeQuoted123 in vault", "FakeQuoted123"),
    ("db password: Hunter2FakePass!99 for prod", "Hunter2FakePass!99"),
    ("password: correct horse battery staple", "correct horse"),
    ("password: blue sky 7", "blue sky"),
    ("password: Dragon Monkey Tiger", "Dragon Monkey"),
    ("pwd: qwerty asdf zxcv", "qwerty"),
    ("password: dragon for wifi", "dragon"),
    ("password: dragon, rotate monthly", "dragon"),
    # --- round-16 D2: status word must not shield a secret ---
    ("password: set to dragon", "dragon"),
    ("password: changed to Hunter2x", "Hunter2x"),
    ("password: reset to welcome123", "welcome123"),
    ("password: none, use dragon", "dragon"),
    ("token: rotated abc123XYZ", "abc123XYZ"),
    ("password: required dragon", "dragon"),
    ("password: n/a dragon", "dragon"),
    ("secret: unset banana", "banana"),
    # --- round-16 D4: dash / arrow / password whitespace separators ---
    ("password - dragon", "dragon"),
    ("password → dragon", "dragon"),
    ("password -> dragon", "dragon"),
    ("passwd dragon", "dragon"),
    # --- round-17 D1: exceptions only when ENTIRE value matches ---
    ("pass: 1 dragon", "dragon"),
    ("pass=196 Hunter2Fake", "Hunter2Fake"),
    ("max_token=100 Hunter2Fake", "Hunter2Fake"),
    ("token_limit=5 Hunter2Fake", "Hunter2Fake"),
    ("pwd=/tmp Hunter2Secret", "Hunter2Secret"),
    ("pwd: /tmp/x Hunter2Secret", "Hunter2Secret"),
    # --- round-17 D2: colon skip only for secret name before = ---
    ("password: x=Hunter2Fake", "Hunter2Fake"),
    # --- round-17 D3: wider names / separators; changeme is a secret ---
    ("password_prod=dragon", "dragon"),
    ("passwordProd: dragon", "dragon"),
    ("password[prod]=dragon", "dragon"),
    ("api key: FakeApiSpace99", "FakeApiSpace99"),
    ("secret key: FakeSecSpace99", "FakeSecSpace99"),
    ("sessionid=FakeSess99xx", "FakeSess99xx"),
    ("passcode: 4821", "4821"),
    ("pin: 4821", "4821"),
    ("p@ssword: dragon", "dragon"),
    ("password := dragon", "dragon"),
    ("password：dragon", "dragon"),
    ("password is: dragon", "dragon"),
    ("password; dragon", "dragon"),
    ("password, dragon", "dragon"),
    ("-p dragon", "dragon"),
    ("password: changeme", "changeme"),
    ("API_KEY=changeme", "changeme"),
    # --- round-18 D8: more names / separators / glued -p ---
    ("access key: FakeAccessKey99", "FakeAccessKey99"),
    ("private key: FakePrivateKey99", "FakePrivateKey99"),
    ("DBPASS=DbPassValue99", "DbPassValue99"),
    ("mypwd: MyPwdValue99xx", "MyPwdValue99xx"),
    ("userpass=UserPass99xx", "UserPass99xx"),
    ("password[]=dragon", "dragon"),
    ("password[a][b]=dragon", "dragon"),
    ("password.prod=dragon", "dragon"),
    ("password(prod)=dragon", "dragon"),
    ("pa$$word: dragon", "dragon"),
    ("pin code: 4821", "4821"),
    ("-pdragon", "dragon"),
    ("mysql -uroot -pSecretPw99", "SecretPw99"),
    # --- round-19 #5: quoted trailing, pass word, ZW, i18n, braces, p@ss ---
    ("password: 'Hunter2Fake' dragon", "Hunter2Fake"),
    ("pass word: dragon", "dragon"),
    ("Passwort: geheim99xx", "geheim99xx"),
    ("mot de passe: secret99xx", "secret99xx"),
    ("contraseña: secreto99", "secreto99"),
    ("password{prod}=dragon", "dragon"),
    ("p@ss: dragon", "dragon"),
    ("p@55word: dragon", "dragon"),
]

# Still kept under the simple rule (empty / placeholder / null / bool / status
# alone / knob numbers / pass=N counts / pwd paths / non-secret names).
# Round-19 #1: -p must not eat hyphenated names / port / mkdir.
KEEP_CASES: list[str] = [
    "workers-best-practices",
    "web-perf",
    "split-to-prs",
    "mkdir -p /tmp/x",
    "ssh -p 22",
    "docker -p 8080:80",
    "web-pin: x",
    "supabase-postgres-best-practices",
    "local-security-prompts.mdc",
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
    "primary_key: user_id",
    "sort_key: created_at",
    "cache_key: v2-node20",
    "MAX_TOKEN=4096",
    "pwd: /Users/x",
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
    "/tmp/r10fp/abcdefghijklmnopqrstuv/CLAUDE.md",
    "/home/box/sand-data/workflows/why/references/epistemics.md",
    "commit abcdef1",
    "id 550e8400-e29b-41d4-a716-446655440000",
    # status-word allowlist alone (nothing else)
    "token: optional",
    "password: changed",
    "Authorization: required",
    "password: reset",
    "token: expired",
    "secret: unknown",
    "session: deprecated",
    "auth: revoked",
    "password: none",
    "PASSWORD=required",
    "token=expired",
    # pass=N counts (including alone)
    "Tests: pass=196 failed=0",
    "Tests: pass=196",
    "PASS: 12 FAIL: 0",
    "pass=3 failures=1",
]

# Intentional over-redaction under the simple whole-value rule.
# Do not delete or reword these to make tests pass — document the tradeoff.
# Includes restored round-10 strings that prose carve-outs previously "kept".
EXPECTED_OVERREDACT: list[str] = [
    # restored round-10 originals (were reworded in later rounds to dodge FP)
    "Default token: Composer model",
    "Explain token: what counts",
    # "api key" is a secret name (round-17 D3); instructional use over-redacts
    "API key: stored in 1Password",
    # former KEEP / instructional prose now redacted
    "Max token: 8192",
    "Default token: 4096 limit",
    "Secret: 3 rules to follow",
    "token: 2 per request",
    "Password: 8+ chars required",
    "token: v2",
    "password: none needed",
    "token: claude-sonnet-4-5",
    "token: gpt-4o-mini-2024",
    "token: grok-2",
    "token: gemini-1.5-pro",
    "token: o1-preview",
    "secret: the deploy key",
    "auth: github",
    "session: weekly",
    "cookie: consent banner",
    # round-16 over-redaction survey
    "token: sonnet",
    "token: budget",
    "token: 1h",
    "token: Composer-1",
    "Token: see docs",
    "auth: sso",
    "Auth: OAuth",
    "session: daily",
    "Session: Monday standup",
    "cookie: chocolate",
    "cookie: blocked",
    "secret: shared",
    "secret: 2fa",
    "The secret: practice daily",
    "password: policy",
    "password: weak",
    "Password: Strong",
    "password: 2FA enabled",
    "x-api-key: header name",
    "access_key: from IAM",
    "apikey: in vault",
    "credentials: in vault",
    "passphrase: ask Scott",
    "private_key: in ~/.ssh",
    "token=sonnet-4",
    "AUTH=sso",
    "auth=oauth",
    "session=abc",
]


def _assert_redact(raw: str, leak: str) -> None:
    got = redact_secrets(raw)
    assert leak not in got, f"REDACT leak remains:\n  in:  {raw!r}\n  out: {got!r}\n  leak:{leak!r}"
    assert "REDACTED" in got, f"REDACT missing marker:\n  in:  {raw!r}\n  out: {got!r}"


def _assert_keep(raw: str) -> None:
    got = redact_secrets(raw)
    assert got == raw, f"KEEP changed:\n  in:  {raw!r}\n  out: {got!r}"


def _assert_overredact(raw: str) -> None:
    got = redact_secrets(raw)
    assert "REDACTED" in got, (
        f"EXPECTED_OVERREDACT did not redact:\n  in:  {raw!r}\n  out: {got!r}"
    )


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
        # Prefer D1/D2/D4 + distinctive eq forms so CLI covers round-16 defects.
        prefer = [
            raw
            for raw, _ in REDACT_CASES
            if any(
                raw.startswith(p)
                for p in (
                    "password: Hunter2",
                    "password: set to",
                    "token: rotated",
                    "password - ",
                    "password →",
                    "passwd ",
                    "password: dragon, rotate",
                    "db password:",
                )
            )
            or (
                "\n" not in raw
                and len(raw) < 80
                and ("=" in raw or ":" in raw)
            )
        ]
        plants = prefer[:16]
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


def _assert_cli_blob(host: str, surface: str, blob: str, raw: str, leak: str) -> None:
    # Leak substring must never appear (JSON or MD).
    if len(leak) >= 6 and not any(leak in k for k in KEEP_CASES):
        assert leak not in blob, f"CLI {host}/{surface} leak {leak!r} from {raw!r}"
    assert raw not in blob, f"CLI {host}/{surface} still has raw {raw!r}"
    # JSON always redacts the planted leaf. Cursor grouped MD prints skill names
    # (Item column), so REDACTED must appear there too. Legacy Claude/Grok MD
    # only lists paths, so REDACTED may be absent when the plant is not MD-visible.
    if surface == "json" or host == "cursor":
        assert "REDACTED" in blob, (
            f"CLI {host}/{surface} missing REDACTED for {raw!r}: {blob[:300]}"
        )


def run_cli_redact_cases() -> None:
    """
    Plant every REDACT_CASES string via CLI on all hosts; assert JSON and MD (D1).
    """
    td = Path(tempfile.mkdtemp(prefix="ai-audit-cli-redact."))
    try:
        # --- Grok: bot profile name ---
        grok = td / "grok"
        shutil.copytree(PLUGIN / "fixtures" / "grok", grok)
        profile = grok / "sand-data" / "agents" / "agent-active" / "profile.json"

        # --- Cursor: MCP server name (surfaces in grouped MD Item column) ---
        cursor = td / "cursor"
        shutil.copytree(PLUGIN / "fixtures" / "cursor", cursor)
        cursor_mcp = cursor / "home" / ".cursor" / "mcp.json"

        # --- Claude: MCP server name in ~/.claude.json (servers[] in inventory) ---
        claude = td / "claude"
        shutil.copytree(PLUGIN / "fixtures" / "claude", claude)
        claude_json = claude / "home" / ".claude.json"
        claude_base = json.loads(claude_json.read_text(encoding="utf-8"))

        for raw, leak in REDACT_CASES:
            if "\n" in raw:
                continue

            profile.write_text(
                json.dumps({"name": raw, "description": "test bot"}, indent=2),
                encoding="utf-8",
            )
            # MCP server names are JSON keys — preserves the raw string exactly.
            cursor_mcp.write_text(
                json.dumps({"mcpServers": {raw: {"command": "true"}}}, indent=2),
                encoding="utf-8",
            )
            claude_cfg = json.loads(json.dumps(claude_base))  # deep copy
            servers = claude_cfg.setdefault("mcpServers", {})
            servers.clear()
            servers[raw] = {"command": "true"}
            claude_json.write_text(json.dumps(claude_cfg, indent=2), encoding="utf-8")

            for host, root in (
                ("grok", grok),
                ("cursor", cursor),
                ("claude", claude),
            ):
                for flag, surface in (("--json-only", "json"), ("--md-only", "md")):
                    blob = _run_cli(
                        ["context", "--host", host, "--root", str(root), flag]
                    )
                    _assert_cli_blob(host, surface, blob, raw, leak)
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


def run_all() -> tuple[int, int, int]:
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

    for raw in EXPECTED_OVERREDACT:
        _assert_overredact(raw)

    run_context_secret_e2e()
    run_cli_redact_cases()
    run_project_eq_home_context_dedupe()

    return len(REDACT_CASES) + 3, len(KEEP_CASES), len(EXPECTED_OVERREDACT)


def main() -> int:
    redact_n, keep_n, over_n = run_all()
    print(
        f"PASS redact_cases redact={redact_n} keep={keep_n} "
        f"overredact={over_n} total={redact_n + keep_n + over_n}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        print(f"FAIL {e}", file=sys.stderr)
        raise SystemExit(1)
