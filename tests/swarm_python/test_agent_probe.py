"""Unit tests for the per-agent probe: parsers, auth detection, and the
fallback to the static catalog.

Run from the repo root:  python3 -m unittest discover -s tests/swarm_python
"""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[2] / "template" / "swarmforge" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from swarm_python import agent_probe  # noqa: E402
from swarm_python.agent_probe import (  # noqa: E402
    AUTH_MISSING,
    AUTH_NOT_INSTALLED,
    AUTH_OK,
    AUTH_UNKNOWN,
    SOURCE_LIVE,
    SOURCE_STATIC,
    RunResult,
)

STATIC = [("anthropic", "Anthropic", "sonnet", "Sonnet")]


def fake_runner(responses):
    """responses: {first-args tuple: RunResult | None}. Unknown -> None."""

    def run(argv, timeout):
        key = tuple(argv[1:])
        return responses.get(key)

    return run


def which_all(name):
    return f"/usr/bin/{name}"


def which_none(name):
    return None


class ParserTests(unittest.TestCase):
    def test_opencode_models_groups_pairs_and_skips_noise(self):
        text = "\x1b[0m\nanthropic/claude-sonnet-4\nopencode-go/kimi-k3\nnot a model\nbad\n/x\nanthropic/claude-sonnet-4\n"
        self.assertEqual(
            agent_probe.parse_opencode_models(text),
            [("anthropic", "claude-sonnet-4"), ("opencode-go", "kimi-k3")],
        )

    def test_opencode_models_keeps_slashes_in_model_id(self):
        self.assertEqual(
            agent_probe.parse_opencode_models("openrouter/meta/llama-4\n"),
            [("openrouter", "meta/llama-4")],
        )

    def test_opencode_auth_strips_ansi(self):
        text = (
            "\x1b[0m\n\x1b[90m\u250c\x1b[39m  Credentials\n\x1b[90m\u2502\x1b[39m\n"
            "\x1b[34m\u25cf\x1b[39m  Anthropic \x1b[90mapi\n\x1b[90m\u2502\x1b[39m\n"
            "\x1b[34m\u25cf\x1b[39m  OpenCode Go \x1b[90mapi\n\x1b[90m\u2514\x1b[39m  2 credentials\n"
        )
        self.assertEqual(agent_probe.parse_opencode_auth(text), ["Anthropic", "OpenCode Go"])

    def test_opencode_unverified_marks_instead_of_hiding(self):
        pairs = [("anthropic", "a"), ("opencode-go", "b"), ("openai", "c"), ("opencode", "d")]
        unverified = agent_probe.unverified_opencode_providers(pairs, ["Anthropic", "OpenCode Go"])
        self.assertEqual(unverified, {"openai"})

    def test_opencode_filter_keeps_all_when_auth_unparseable(self):
        pairs = [("anthropic", "a"), ("openai", "c")]
        self.assertEqual(agent_probe.unverified_opencode_providers(pairs, []), set())
        self.assertEqual(agent_probe.unverified_opencode_providers(pairs, ["Something Else"]), set())

    def test_codex_models_reads_slug_and_display_name(self):
        text = json.dumps(
            {
                "models": [
                    {"slug": "gpt-a", "display_name": "GPT A", "visibility": "list"},
                    {"slug": "gpt-hidden", "visibility": "hide"},
                    {"slug": "gpt-a", "display_name": "dup"},
                    {"slug": "gpt-b"},
                ]
            }
        )
        self.assertEqual(
            agent_probe.parse_codex_models(text),
            [("gpt-a", "GPT A"), ("gpt-b", "gpt-b")],
        )

    def test_codex_models_accepts_bare_list_and_other_keys(self):
        text = json.dumps([{"id": "m1", "name": "Model 1"}, {"model": "m2"}, "junk"])
        self.assertEqual(
            agent_probe.parse_codex_models(text),
            [("m1", "Model 1"), ("m2", "m2")],
        )

    def test_codex_models_garbage_yields_nothing(self):
        for text in ("", "not json", "{}", '{"models": "x"}', "[1, 2]"):
            self.assertEqual(agent_probe.parse_codex_models(text), [], text)

    def test_pi_models_skips_header_and_messages(self):
        text = (
            "provider   model            context\n"
            "anthropic  claude-sonnet-4  200K\n"
            "openai     gpt-5            400K\n"
        )
        self.assertEqual(
            agent_probe.parse_pi_models(text),
            [("anthropic", "claude-sonnet-4"), ("openai", "gpt-5")],
        )

    def test_pi_models_no_models_message(self):
        text = "No models available. Use /login to log into a provider.\n  C:\\x\\docs\\providers.md\n"
        self.assertEqual(agent_probe.parse_pi_models(text), [])

    def test_kiro_models_json(self):
        text = json.dumps(
            {
                "models": [
                    {"model_name": "auto", "model_id": "auto"},
                    {"model_name": "Sonnet", "model_id": "claude-sonnet-5"},
                    {"model_name": "no id"},
                ],
                "default_model": "auto",
            }
        )
        self.assertEqual(
            agent_probe.parse_kiro_models(text),
            [("auto", "auto"), ("claude-sonnet-5", "Sonnet"), ("no id", "no id")],
        )

    def test_kiro_models_tolerates_trailing_text(self):
        text = '{"models":[{"model_id":"auto","model_name":"auto"}]}\n\nextra output'
        self.assertEqual(agent_probe.parse_kiro_models(text), [("auto", "auto")])

    def test_claude_auth_json(self):
        logged_in = '{"loggedIn": true, "authMethod": "claude.ai"}'
        self.assertEqual(agent_probe.parse_claude_auth(logged_in, 0), (AUTH_OK, "auth method: claude.ai"))
        logged_out = '{"loggedIn": false}'
        self.assertEqual(agent_probe.parse_claude_auth(logged_out, 1)[0], AUTH_MISSING)
        self.assertEqual(agent_probe.parse_claude_auth("garbage", 0)[0], AUTH_OK)
        self.assertEqual(agent_probe.parse_claude_auth("garbage", 1)[0], AUTH_MISSING)

    def test_kiro_whoami(self):
        out = '{"accountType":"IamIdentityCenter"}\n\nProfile:\nx'
        self.assertEqual(agent_probe.parse_kiro_whoami(out, 0)[0], AUTH_OK)
        self.assertEqual(agent_probe.parse_kiro_whoami("", 1)[0], AUTH_MISSING)
        self.assertEqual(agent_probe.parse_kiro_whoami("plain", 0)[0], AUTH_UNKNOWN)


class PiAuthTests(unittest.TestCase):
    def test_env_var_detected_without_leaking_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, detail = agent_probe.detect_pi_auth({"OPENAI_API_KEY": "sk-secret"}, Path(tmp))
        self.assertEqual(state, AUTH_OK)
        self.assertIn("env:OPENAI_API_KEY", detail)
        self.assertNotIn("sk-secret", detail)

    def test_auth_json_detected_without_leaking_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / ".pi" / "agent" / "auth.json"
            auth.parent.mkdir(parents=True)
            auth.write_text(json.dumps({"anthropic": {"key": "sk-secret"}}), encoding="utf-8")
            state, detail = agent_probe.detect_pi_auth({}, Path(tmp))
        self.assertEqual(state, AUTH_OK)
        self.assertIn("auth.json:anthropic", detail)
        self.assertNotIn("sk-secret", detail)

    def test_nothing_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(agent_probe.detect_pi_auth({}, Path(tmp))[0], AUTH_MISSING)

    def test_corrupt_auth_json_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / ".pi" / "agent" / "auth.json"
            auth.parent.mkdir(parents=True)
            auth.write_text("{not json", encoding="utf-8")
            self.assertEqual(agent_probe.detect_pi_auth({}, Path(tmp))[0], AUTH_MISSING)


class ProbeTierTests(unittest.TestCase):
    def probe(self, agent, responses, which=which_all, static=STATIC, env=None, home=None):
        return agent_probe.probe(
            agent, static, runner=fake_runner(responses), which=which, env=env or {}, home=home or Path("/nonexistent-home")
        )

    def test_binary_missing_falls_back_to_static(self):
        report = self.probe("codex", {}, which=which_none)
        self.assertFalse(report.installed)
        self.assertEqual(report.auth, AUTH_NOT_INSTALLED)
        self.assertEqual(report.source, SOURCE_STATIC)
        self.assertEqual(report.rows, tuple(STATIC))
        self.assertEqual(report.hint, "codex login")

    def test_live_listing_used_when_it_works(self):
        models = json.dumps({"models": [{"slug": "gpt-x", "display_name": "GPT X"}]})
        report = self.probe(
            "codex",
            {
                ("login", "status"): RunResult(0, "Logged in using ChatGPT"),
                ("debug", "models"): RunResult(0, models),
            },
        )
        self.assertEqual(report.auth, AUTH_OK)
        self.assertEqual(report.source, SOURCE_LIVE)
        self.assertEqual(report.rows, (("openai", "OpenAI", "gpt-x", "GPT X"),))

    def test_nonzero_exit_falls_back(self):
        report = self.probe(
            "codex",
            {
                ("login", "status"): RunResult(1, "", "Not logged in"),
                ("debug", "models"): RunResult(1, "", "boom"),
            },
        )
        self.assertEqual(report.auth, AUTH_MISSING)
        self.assertEqual(report.source, SOURCE_STATIC)
        self.assertEqual(report.rows, tuple(STATIC))

    def test_garbage_output_falls_back(self):
        report = self.probe(
            "kiro-cli",
            {
                ("whoami", "--format", "json"): RunResult(0, '{"accountType":"x"}'),
                ("chat", "--list-models", "--format", "json"): RunResult(0, "<html>nope</html>"),
            },
        )
        self.assertEqual(report.auth, AUTH_OK)
        self.assertEqual(report.source, SOURCE_STATIC)

    def test_timeout_or_unrunnable_command_falls_back(self):
        # The runner returns None when the command cannot run or times out.
        report = self.probe("opencode", {})
        self.assertEqual(report.auth, AUTH_UNKNOWN)
        self.assertEqual(report.source, SOURCE_STATIC)
        self.assertEqual(report.rows, tuple(STATIC))

    def test_run_command_converts_timeout_and_oserror_to_none(self):
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertIsNone(agent_probe.run_command(["x"], 1))
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError()):
            self.assertIsNone(agent_probe.run_command(["x"], 1))

    def test_probe_exception_never_escapes(self):
        def boom(argv, timeout):
            raise RuntimeError("unexpected")

        report = agent_probe.probe("codex", STATIC, runner=boom, which=which_all, env={}, home=Path("/x"))
        self.assertEqual(report.source, SOURCE_STATIC)
        self.assertEqual(report.auth, AUTH_UNKNOWN)

    def test_claude_has_no_listing_and_uses_static(self):
        report = self.probe("claude", {("auth", "status"): RunResult(0, '{"loggedIn": true, "authMethod": "claude.ai"}')})
        self.assertEqual(report.auth, AUTH_OK)
        self.assertEqual(report.source, SOURCE_STATIC)
        self.assertEqual(report.rows, tuple(STATIC))

    def test_copilot_and_grok_auth_unknown_static(self):
        for agent in ("copilot", "grok"):
            report = self.probe(agent, {})
            self.assertEqual(report.auth, AUTH_UNKNOWN)
            self.assertEqual(report.source, SOURCE_STATIC)

    def test_opencode_live_listing_marks_unverified_and_hides_nothing(self):
        auth = "\x1b[34m\u25cf\x1b[39m  OpenCode Go \x1b[90mapi\n"
        report = self.probe(
            "opencode",
            {
                ("auth", "list"): RunResult(0, auth),
                ("models",): RunResult(0, "openai/gpt-5\nopencode-go/kimi-k3\n"),
            },
        )
        self.assertEqual(report.source, SOURCE_LIVE)
        self.assertEqual(
            report.rows,
            (
                ("opencode-go", "OpenCode Go", "kimi-k3", "kimi-k3"),
                ("openai", "OpenAI (no verificado)", "gpt-5", "gpt-5"),
            ),
        )

    def test_codex_auth_never_echoes_key_fragment(self):
        for text, expected in (
            ("Logged in using an API key - sk-proj-***xyz", "API key"),
            ("Logged in using ChatGPT", "ChatGPT"),
            ("Logged in as sk-proj-***xyz", "logged in"),
        ):
            report = self.probe("codex", {("login", "status"): RunResult(0, text)})
            self.assertEqual(report.auth, AUTH_OK)
            self.assertEqual(report.detail, expected)
            self.assertNotIn("sk-", report.detail)
            self.assertNotIn("sk-", agent_probe.report_line(report))
        missing = self.probe("codex", {("login", "status"): RunResult(1, "Not logged in sk-proj-***xyz")})
        self.assertEqual(missing.auth, AUTH_MISSING)
        self.assertNotIn("sk-", missing.detail)

    def test_claude_auth_method_free_text_is_dropped(self):
        text = '{"loggedIn": true, "authMethod": "key sk-ant-***abc"}'
        self.assertEqual(agent_probe.parse_claude_auth(text, 0), (AUTH_OK, ""))

    def test_pi_listing_and_env_auth(self):
        report = self.probe(
            "pi",
            {("--list-models",): RunResult(0, "provider model\nopenai gpt-5\n")},
            env={"OPENAI_API_KEY": "x"},
        )
        self.assertEqual(report.auth, AUTH_OK)
        self.assertEqual(report.rows, (("openai", "OpenAI", "gpt-5", "gpt-5"),))

    def test_unsupported_agent(self):
        report = self.probe("nope", {})
        self.assertFalse(report.installed)
        self.assertEqual(report.rows, tuple(STATIC))


class OutputContractTests(unittest.TestCase):
    def test_report_line_has_six_columns_and_no_empty_fields(self):
        report = agent_probe.AgentReport("pi", True, AUTH_MISSING, SOURCE_STATIC, "", "", ())
        fields = agent_probe.report_line(report).split("\t")
        self.assertEqual(len(fields), 6)
        self.assertNotIn("", fields)

    def test_catalog_rows_are_four_columns_without_tabs(self):
        report = agent_probe.AgentReport("x", True, AUTH_OK, SOURCE_LIVE, rows=(("p", "P\tlabel", "m", "M\nx"),))
        self.assertEqual(agent_probe.catalog_rows(report), [("p", "P label", "m", "M x")])

    def test_static_rows_loaded_per_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tsv = Path(tmp) / "m.tsv"
            tsv.write_text(
                "agent\tprovider\tprovider_label\tmodel_id\tmodel_label\n"
                "claude\tanthropic\tAnthropic\topus\tOpus\n"
                "codex\topenai\tOpenAI\tgpt\tGPT\n",
                encoding="utf-8",
            )
            self.assertEqual(
                agent_probe.load_static_rows("claude", tsv),
                [("anthropic", "Anthropic", "opus", "Opus")],
            )
        self.assertEqual(agent_probe.load_static_rows("claude", "/missing.tsv"), [])


class SubprocessTests(unittest.TestCase):
    def test_timeout_kills_process_tree_and_returns_promptly(self):
        # The child spawns a grandchild that inherits the pipes and outlives
        # it, like a Windows .cmd shim; subprocess.run would block on it.
        code = (
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "time.sleep(60)\n"
        )
        started = time.monotonic()
        result = agent_probe.run_command([sys.executable, "-c", code], 2)
        elapsed = time.monotonic() - started
        self.assertIsNone(result)
        self.assertLess(elapsed, 15)

    def test_run_command_returns_output(self):
        result = agent_probe.run_command([sys.executable, "-c", "print('hi')"], 20)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "hi"))

    def test_run_command_missing_binary_is_none(self):
        self.assertIsNone(agent_probe.run_command(["/definitely/not/here"], 5))


class LfOutputTests(unittest.TestCase):
    def test_main_emits_lf_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tsv = Path(tmp) / "m.tsv"
            tsv.write_text(
                "agent\tprovider\tprovider_label\tmodel_id\tmodel_label\n"
                "nope\tp\tP\tm\tM\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "swarm_python" / "agent_probe.py"),
                 "--agent", "nope", "--catalog", "--report", "--static-catalog", str(tsv)],
                capture_output=True,
                timeout=60,
            )
        self.assertEqual(completed.stdout, b"p\tP\tm\tM\n")
        self.assertNotIn(b"\r", completed.stderr)
        self.assertTrue(completed.stderr.endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
