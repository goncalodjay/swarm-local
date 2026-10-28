"""Unit tests for the Hermes provider probe: what Hermes can actually run.

Run from the repo root:  python3 -m unittest discover -s tests/swarm_python
"""

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "template" / "swarmforge" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from swarm_python.hermes_probe import (  # noqa: E402
    STATE_FAILED,
    STATE_OK,
    STATE_SUSPECT,
    STATE_VERIFIED,
    StaticCatalog,
    catalog_rows,
    parse_auth_list,
    report_rows,
)

HEALTHY = """openai-codex (1 credentials):
  #1  device_code          oauth   id=b8efe8 priority=0 device_code ←

"""

# The pool tries this one first and its last attempt failed. The marker is a
# record of that attempt, not the credential's health.
STALE_FAILURE = """opencode-go (2 credentials):
  #1  api-key-2            api_key id=debb0b priority=0 manual auth failed (401) (re-auth may be required) ←
  #2  api-key-1            api_key id=a68d7a priority=1 manual

"""

DEAD_ACTIVE = """opencode-go (2 credentials):
  #1  api-key-1            api_key id=a68d7a priority=0 manual auth failed (401) (re-auth may be required) ←
  #2  api-key-2            api_key id=debb0b priority=1 manual

"""


class ParseAuthListTest(unittest.TestCase):
    def test_a_working_credential_is_ok(self):
        providers = parse_auth_list(HEALTHY)
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0].name, "openai-codex")
        self.assertEqual(providers[0].state, STATE_OK)
        self.assertEqual(providers[0].last_failure, "")

    def test_the_active_credential_is_the_one_the_pool_uses(self):
        provider = parse_auth_list(DEAD_ACTIVE)[0]
        self.assertEqual(provider.active_credential.label, "api-key-1")
        self.assertEqual(provider.active_credential.id, "a68d7a")
        self.assertEqual(provider.active_credential.priority, 0)

    def test_a_recorded_failure_makes_the_provider_suspect(self):
        provider = parse_auth_list(DEAD_ACTIVE)[0]
        self.assertEqual(provider.state, STATE_SUSPECT)
        self.assertIn("auth failed", provider.last_failure)

    def test_a_recorded_failure_on_another_credential_does_not_count(self):
        provider = parse_auth_list(STALE_FAILURE)[0]
        # The dead key is not the one the pool will use.
        self.assertEqual(provider.active_credential.label, "api-key-2")
        self.assertEqual(provider.state, STATE_SUSPECT)

    def test_without_a_marker_the_first_by_priority_is_used(self):
        provider = parse_auth_list(
            "opencode-go (2 credentials):\n"
            "  #1  api-key-1  api_key id=aaa priority=3 manual\n"
            "  #2  api-key-2  api_key id=bbb priority=1 manual\n"
        )[0]
        self.assertEqual(provider.active_credential.label, "api-key-2")

    def test_the_status_is_read_after_the_priority_field(self):
        provider = parse_auth_list(DEAD_ACTIVE)[0]
        self.assertNotIn("api-key-1", provider.last_failure)
        self.assertNotIn("id=a68d7a", provider.last_failure)

    def test_no_output_yields_no_providers(self):
        self.assertEqual(parse_auth_list(""), [])


class StaticCatalogTest(unittest.TestCase):
    def test_it_reads_the_hermes_rows_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider-models.tsv"
            path.write_text(
                "agent\tprovider\tprovider_label\tmodel_id\tmodel_label\n"
                "hermes\topencode-go\tOpenCode Go\tminimax-m3\tMiniMax M3\n"
                "opencode\topencode-go\tOpenCode Go\tother\tOther\n",
                encoding="utf-8",
            )
            catalog = StaticCatalog.from_tsv(path)
        self.assertEqual(catalog.label_for("opencode-go"), "OpenCode Go")
        self.assertEqual(catalog.models_for("opencode-go"), (("minimax-m3", "MiniMax M3"),))
        self.assertEqual(catalog.models_for("unknown"), ())

    def test_a_known_provider_gets_hermes_display_name(self):
        catalog = StaticCatalog({}, {})
        self.assertEqual(catalog.label_for("openai-codex"), "ChatGPT or Codex Subscription")
        self.assertEqual(catalog.label_for("who-knows"), "who-knows")


class CatalogRowsTest(unittest.TestCase):
    def report(self, state=STATE_OK):
        return {
            "providers": [
                {
                    "name": "openai-codex",
                    "label": "ChatGPT or Codex Subscription",
                    "state": state,
                    "detail": "",
                    "credential": "device_code (b8efe8)",
                    "usable": True,
                    "models": [("gpt-6-astra", "gpt-6-astra")],
                },
                {
                    "name": "opencode-go",
                    "label": "OpenCode Go",
                    "state": STATE_FAILED,
                    "detail": "HTTP 401",
                    "credential": "api-key-1 (a68d7a)",
                    "usable": False,
                    "models": [("minimax-m3", "minimax-m3")],
                },
            ]
        }

    def test_a_failed_provider_never_reaches_the_catalog(self):
        rows = catalog_rows(self.report())
        self.assertEqual(
            rows,
            [("openai-codex", "ChatGPT or Codex Subscription", "gpt-6-astra", "gpt-6-astra")],
        )

    def test_a_suspect_provider_is_still_offered(self):
        # The recorded failure is stale more often than not: without a live
        # check the provider must stay reachable.
        rows = catalog_rows(self.report(state=STATE_SUSPECT))
        self.assertEqual(len(rows), 1)

    def test_the_report_carries_the_active_credential(self):
        rows = report_rows(self.report())
        self.assertEqual(rows[1][4], "api-key-1 (a68d7a)")
        self.assertEqual(rows[0][1], STATE_OK)


if __name__ == "__main__":
    unittest.main()
