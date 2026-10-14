import ast
import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from rapidfuzz import fuzz, process as rapid_process


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
TREE = ast.parse(APP_PATH.read_text(encoding="utf-8-sig"))
FUNCTION_NAMES = {
    "clean_text",
    "normalize_header",
    "make_header_signature",
    "load_mapping_preferences",
    "save_mapping_preferences",
    "normalize_email",
    "normalize_website",
    "normalize_phone",
    "build_flag_breakdown",
    "calculate_health_score",
    "website_format_valid",
    "email_format_valid",
    "phone_format_valid",
    "process_leads",
    "upload_content_signature",
    "processing_config_signature",
}
FUNCTION_NODES = [
    node for node in TREE.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name in FUNCTION_NAMES
]
FOUND_NAMES = {node.name for node in FUNCTION_NODES}
if FOUND_NAMES != FUNCTION_NAMES:
    raise RuntimeError(
        f"Could not locate app logic functions: {sorted(FUNCTION_NAMES - FOUND_NAMES)}"
    )

NAMESPACE = {
    "pd": pd,
    "re": re,
    "urlparse": urlparse,
    "hashlib": hashlib,
    "json": json,
    "sqlite3": sqlite3,
    "fuzz": fuzz,
    "process": rapid_process,
}
exec(
    compile(ast.Module(body=FUNCTION_NODES, type_ignores=[]), str(APP_PATH), "exec"),
    NAMESPACE,
)
process_leads = NAMESPACE["process_leads"]
make_header_signature = NAMESPACE["make_header_signature"]
upload_content_signature = NAMESPACE["upload_content_signature"]
processing_config_signature = NAMESPACE["processing_config_signature"]


ALL_CHECKS_OFF = {
    "required_fields": False,
    "missing_email": False,
    "email_format": False,
    "duplicate_emails": False,
    "phone_format": False,
    "missing_website": False,
    "website_format": False,
    "exact_duplicates": False,
}


def run_processing(frame, **overrides):
    arguments = {
        "source_df": frame,
        "email_col": None,
        "phone_col": None,
        "website_col": None,
        "company_col": None,
        "first_name_col": None,
        "last_name_col": None,
        "record_type": "Business leads",
        "remove_blank_rows": True,
        "validation_options": ALL_CHECKS_OFF.copy(),
        "fuzzy_duplicates": False,
        "fuzzy_threshold": 90,
    }
    arguments.update(overrides)
    return process_leads(**arguments)


class LeadProofLogicTests(unittest.TestCase):
    def test_phone_normalization_never_guesses_a_country_code(self):
        normalize_phone = NAMESPACE["normalize_phone"]
        self.assertEqual(normalize_phone(" +92 (300) 123-4567 "), "+923001234567")
        self.assertEqual(normalize_phone("0300-123-4567"), "03001234567")
        self.assertEqual(normalize_phone(""), "")

    def test_auto_fixes_are_audited_and_align_after_blank_rows_are_removed(self):
        frame = pd.DataFrame({
            "Company": ["Acme", "", "Northstar"],
            "Email": [" SALES@EXAMPLE.COM ", "", "contact@example.com"],
            "Phone": ["+92 (300) 123-4567", "", "0300-123-4567"],
            "Website": ["example.com", "", "https://northstar.example"],
        })
        cleaned, flagged, summary = run_processing(
            frame,
            company_col="Company",
            email_col="Email",
            phone_col="Phone",
            website_col="Website",
            validation_options=ALL_CHECKS_OFF,
            auto_fix_enabled=True,
            remove_blank_rows=True,
        )
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(cleaned.iloc[0]["Email"], "sales@example.com")
        self.assertEqual(cleaned.iloc[0]["Website"], "https://example.com")
        self.assertEqual(cleaned.iloc[0]["Phone"], "+923001234567")
        self.assertIn("Email trimmed/lowercased", cleaned.iloc[0]["LeadProof Auto-Fixes"])
        self.assertIn("Website scheme added", cleaned.iloc[0]["LeadProof Auto-Fixes"])
        self.assertIn("Phone formatting standardized", cleaned.iloc[0]["LeadProof Auto-Fixes"])
        self.assertEqual(summary["auto_fixed_rows"], 2)
        self.assertEqual(summary["health_score"], 100)
        self.assertEqual(summary["flag_breakdown"], {})
        self.assertTrue(flagged.empty)

    def test_health_score_and_flag_breakdown_use_row_flags(self):
        cleaned = pd.DataFrame({
            "LeadProof Flags": ["Email missing; Phone format needs review", "", "Email missing"]
        })
        self.assertEqual(NAMESPACE["calculate_health_score"]({
            "processed_rows": 3, "ready_rows": 1
        }), 33)
        self.assertEqual(NAMESPACE["build_flag_breakdown"](cleaned), {
            "Email missing": 2,
            "Phone format needs review": 1,
        })
        self.assertEqual(NAMESPACE["calculate_health_score"]({
            "processed_rows": 0, "ready_rows": 0
        }), 0)

    def test_upload_content_signature_changes_when_contents_change(self):
        self.assertNotEqual(
            upload_content_signature(b"first file"),
            upload_content_signature(b"second file"),
        )
        self.assertEqual(
            upload_content_signature(b"same file"),
            upload_content_signature(b"same file"),
        )

    def test_processing_config_signature_is_stable_and_changes_with_settings(self):
        base = {
            "record_type": "Business leads",
            "email_format": True,
            "threshold": 90,
        }
        reordered = {
            "threshold": 90,
            "email_format": True,
            "record_type": "Business leads",
        }
        changed = {**base, "threshold": 85}
        self.assertEqual(
            processing_config_signature(base),
            processing_config_signature(reordered),
        )
        self.assertNotEqual(
            processing_config_signature(base),
            processing_config_signature(changed),
        )

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        NAMESPACE["MAPPING_DB_PATH"] = Path(self.temp_dir.name) / "mappings.sqlite3"
        NAMESPACE["st"] = SimpleNamespace(session_state={})

    def test_mapping_preferences_survive_a_fresh_session_cache(self):
        signature = make_header_signature(["Company", "Email"])
        mappings = {
            "company": "company",
            "email": "email",
            "phone": None,
        }
        self.assertTrue(
            NAMESPACE["save_mapping_preferences"](signature, mappings)
        )

        # Simulate a new Streamlit session by clearing its in-memory cache.
        NAMESPACE["st"].session_state = {}
        self.assertEqual(
            NAMESPACE["load_mapping_preferences"](signature),
            mappings,
        )

    def test_header_signature_ignores_case_and_repeated_whitespace(self):
        self.assertEqual(
            make_header_signature(["First Name", "Email"]),
            make_header_signature([" first   name ", "EMAIL"]),
        )

    def test_email_format_check_can_be_disabled(self):
        frame = pd.DataFrame({"Company": ["Acme"], "Email": ["not-an-email"]})
        cleaned, flagged, _ = run_processing(
            frame,
            company_col="Company",
            email_col="Email",
            validation_options={"email_format": False},
        )
        self.assertEqual(cleaned.loc[0, "LeadProof Flags"], "")
        self.assertTrue(flagged.empty)

    def test_duplicate_email_check_can_be_disabled(self):
        frame = pd.DataFrame({
            "Company": ["Acme", "Northstar"],
            "Email": ["hello@example.com", "hello@example.com"],
        })
        cleaned, flagged, _ = run_processing(
            frame,
            company_col="Company",
            email_col="Email",
            validation_options={"duplicate_emails": False},
        )
        self.assertFalse(
            cleaned["LeadProof Flags"].str.contains(
                "Email appears more than once", regex=False
            ).any()
        )
        self.assertTrue(flagged.empty)

    def test_exact_duplicate_check_can_be_disabled(self):
        frame = pd.DataFrame({
            "Company": ["Acme", "Acme"],
            "Email": ["hello@example.com", "hello@example.com"],
        })
        cleaned, flagged, _ = run_processing(
            frame,
            company_col="Company",
            email_col="Email",
            validation_options=ALL_CHECKS_OFF,
        )
        self.assertFalse(
            cleaned["LeadProof Flags"].str.contains(
                "Exact duplicate record", regex=False
            ).any()
        )
        self.assertTrue(flagged.empty)

    def test_fuzzy_name_check_flags_similar_companies_without_deleting_rows(self):
        frame = pd.DataFrame({
            "Company": ["Northstar Media", "Northstar Meda", "Blue Oak"],
        })
        cleaned, flagged, summary = run_processing(
            frame,
            company_col="Company",
            validation_options=ALL_CHECKS_OFF,
            fuzzy_duplicates=True,
            fuzzy_threshold=85,
        )
        self.assertEqual(len(cleaned), 3)
        self.assertEqual(summary["processed_rows"], 3)
        self.assertTrue(
            cleaned.loc[0, "LeadProof Flags"].find("Possible name duplicate") >= 0
        )
        self.assertTrue(
            cleaned.loc[1, "LeadProof Flags"].find("Possible name duplicate") >= 0
        )
        self.assertFalse(
            cleaned.loc[2, "LeadProof Flags"].find("Possible name duplicate") >= 0
        )
        self.assertEqual(len(flagged), 2)

    def test_fuzzy_check_skips_when_company_name_column_is_not_mapped(self):
        frame = pd.DataFrame({
            "Email": ["valid@example.com", "also-valid@example.com"],
            "Phone": ["03001234567", "03007654321"],
        })
        cleaned, flagged, summary = run_processing(
            frame,
            email_col="Email",
            phone_col="Phone",
            validation_options={
                **ALL_CHECKS_OFF,
                "email_format": True,
            },
            fuzzy_duplicates=True,
        )
        self.assertEqual(summary["processed_rows"], 2)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(len(flagged), 0)
        self.assertFalse(
            cleaned["LeadProof Flags"].str.contains(
                "Possible name duplicate", regex=False
            ).any()
        )

    def test_fuzzy_check_refuses_oversized_batches(self):
        frame = pd.DataFrame({"Company": ["Acme"] * 3001})
        with self.assertRaisesRegex(ValueError, "limited to 3,000 rows"):
            run_processing(
                frame,
                company_col="Company",
                validation_options=ALL_CHECKS_OFF,
                fuzzy_duplicates=True,
            )

    def test_custom_regex_flags_only_nonmatching_nonempty_values(self):
        frame = pd.DataFrame({"Account ID": ["AB-1234", "wrong", ""]})
        cleaned, flagged, _ = run_processing(
            frame,
            validation_options=ALL_CHECKS_OFF,
            custom_regex_enabled=True,
            custom_regex_col="Account ID",
            custom_regex_pattern=r"^[A-Z]{2}-\d{4}$",
            remove_blank_rows=False,
        )
        self.assertEqual(len(cleaned), 3)
        self.assertEqual(len(flagged), 1)
        self.assertEqual(
            cleaned.loc[1, "LeadProof Flags"],
            "Custom format needs review (Account ID)",
        )
        self.assertEqual(cleaned.loc[2, "LeadProof Flags"], "")

    def test_custom_regex_rejects_invalid_pattern(self):
        frame = pd.DataFrame({"ID": ["abc"]})
        with self.assertRaisesRegex(ValueError, "Custom regex pattern is invalid"):
            run_processing(
                frame,
                validation_options=ALL_CHECKS_OFF,
                custom_regex_enabled=True,
                custom_regex_col="ID",
                custom_regex_pattern="[",
            )


if __name__ == "__main__":
    unittest.main()
