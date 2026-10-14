
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
import hashlib
import json
import re
import sqlite3

import pandas as pd
import streamlit as st
from rapidfuzz import fuzz, process


st.set_page_config(
    page_title="LeadProof | Lead Quality Workspace",
    page_icon="✓",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem; padding-bottom: 3rem; max-width: 1440px;}
    [data-testid="stMetric"] {
        background: var(--secondary-background-color);
        padding: 18px;
        border-radius: 12px;
        border: 1px solid rgba(128,128,128,.18);
    }
    .hero {
        padding: 24px 26px;
        border: 1px solid rgba(128,128,128,.25);
        border-radius: 16px;
        margin-bottom: 22px;
    }
    .muted {color: var(--text-color); opacity: .7;}
    </style>
    """,
    unsafe_allow_html=True,
)


def clean_text(value):
    """Trim surrounding whitespace without changing the underlying meaning."""
    if not isinstance(value, str):
        return value
    return value.strip()


def find_column(columns, keywords):
    """Suggest a likely column based on its header."""
    for column in columns:
        header = str(column).strip().lower()
        if any(word in header for word in keywords):
            return column
    return None


def normalize_header(value):
    """Normalize a header for stable matching across uploads."""
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


def make_header_signature(columns):
    """Fingerprint the normalized header structure, not uploaded records."""
    normalized = [normalize_header(column) for column in columns]
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def upload_content_signature(content):
    """Fingerprint file bytes so cached results match the exact uploaded file."""
    return hashlib.sha256(content).hexdigest()


def processing_config_signature(config):
    """Fingerprint active settings so results cannot outlive their configuration."""
    payload = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


MAPPING_DB_PATH = (
    Path(__file__).resolve().parent / ".leadproof_mappings.sqlite3"
)


def load_mapping_preferences(signature):
    """Load header-specific preferences, falling back safely if unavailable."""
    cache = st.session_state.setdefault("leadproof_mapping_cache", {})
    if signature in cache:
        return cache[signature]

    connection = None
    try:
        connection = sqlite3.connect(MAPPING_DB_PATH, timeout=1)
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mapping_preferences (
                    header_signature TEXT PRIMARY KEY,
                    mappings_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            row = connection.execute(
                "SELECT mappings_json FROM mapping_preferences "
                "WHERE header_signature = ?",
                (signature,),
            ).fetchone()

        mappings = json.loads(row[0]) if row else None
        cache[signature] = mappings
        return mappings
    except (sqlite3.Error, OSError, json.JSONDecodeError):
        return cache.get(signature)
    finally:
        if connection is not None:
            connection.close()


def save_mapping_preferences(signature, mappings):
    """Save column choices only; never save uploaded row data."""
    cache = st.session_state.setdefault("leadproof_mapping_cache", {})
    cache[signature] = mappings

    connection = None
    try:
        connection = sqlite3.connect(MAPPING_DB_PATH, timeout=1)
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mapping_preferences (
                    header_signature TEXT PRIMARY KEY,
                    mappings_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                INSERT INTO mapping_preferences
                    (header_signature, mappings_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(header_signature) DO UPDATE SET
                    mappings_json = excluded.mappings_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    signature,
                    json.dumps(mappings, ensure_ascii=False),
                ),
            )
        return True
    except (sqlite3.Error, OSError):
        return False
    finally:
        if connection is not None:
            connection.close()

def normalize_email(value):
    if not isinstance(value, str):
        return value
    return value.strip().lower()


def normalize_website(value):
    """Normalize website format only; this does not visit the website."""
    if not isinstance(value, str):
        return value

    value = value.strip()
    if not value:
        return value

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = "https://" + value

    return value


def website_format_valid(value):
    if not isinstance(value, str) or not value.strip():
        return False

    try:
        parsed = urlparse(value.strip())
        host = parsed.hostname

        return (
            parsed.scheme in {"http", "https"}
            and host is not None
            and "." in host
            and " " not in host
            and not host.startswith(".")
            and not host.endswith(".")
        )
    except (ValueError, TypeError):
        return False


def email_format_valid(value):
    if not isinstance(value, str) or not value.strip():
        return False

    return bool(
        re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
            r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+",
            value.strip(),
        )
    )


def phone_format_valid(value):
    if not isinstance(value, str) or not value.strip():
        return False

    digits = re.sub(r"\D", "", value)

    # A broad format check, not proof that a number exists.
    return 7 <= len(digits) <= 15


def normalize_phone(value):
    """Standardize presentation without guessing a country code or changing digits."""
    if not isinstance(value, str):
        return value
    value = value.strip()
    if not value:
        return value
    digits = re.sub(r"\D", "", value)
    if not digits:
        return value
    return ("+" if value.startswith("+") else "") + digits


def build_flag_breakdown(cleaned_df):
    """Count validation flags across records; one row may have multiple flags."""
    counts = {}
    if cleaned_df.empty or "LeadProof Flags" not in cleaned_df.columns:
        return counts
    for value in cleaned_df["LeadProof Flags"].fillna("").astype(str):
        for flag in filter(None, (part.strip() for part in value.split(";"))):
            counts[flag] = counts.get(flag, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold())))


def calculate_health_score(summary):
    """Percentage of processed records without active validation flags."""
    total = int(summary.get("processed_rows", 0))
    ready = int(summary.get("ready_rows", 0))
    return round((ready / total) * 100) if total else 0


def read_uploaded_file(uploaded_file):
    filename = uploaded_file.name.lower()
    content = uploaded_file.getvalue()

    if len(content) > 20 * 1024 * 1024:
        raise ValueError("File exceeds the 20 MB limit.")

    if filename.endswith(".csv"):
        try:
            return pd.read_csv(
                BytesIO(content),
                dtype=str,
                keep_default_na=False,
                encoding="utf-8-sig",
                on_bad_lines="error",
            )
        except UnicodeDecodeError:
            return pd.read_csv(
                BytesIO(content),
                dtype=str,
                keep_default_na=False,
                encoding="cp1252",
                on_bad_lines="error",
            )

    if filename.endswith(".xlsx"):
        return pd.read_excel(
            BytesIO(content),
            dtype=str,
            keep_default_na=False,
            engine="openpyxl",
        )

    raise ValueError("Unsupported file type. Upload a CSV or .xlsx file.")


def process_leads(
    source_df,
    email_col,
    phone_col,
    website_col,
    company_col,
    first_name_col,
    last_name_col,
    record_type,
    remove_blank_rows,
    validation_options=None,
    fuzzy_duplicates=False,
    fuzzy_threshold=90,
    custom_regex_enabled=False,
    custom_regex_col=None,
    custom_regex_pattern="",
    auto_fix_enabled=False,
):
    # All checks remain enabled by default; callers may override only selected checks.
    default_validation_options = {
        "required_fields": True,
        "missing_email": True,
        "email_format": True,
        "duplicate_emails": True,
        "phone_format": True,
        "missing_website": True,
        "website_format": True,
        "exact_duplicates": True,
    }
    default_validation_options.update(validation_options or {})
    validation_options = default_validation_options

    # Work on a copy. The uploaded data is never overwritten.
    df = source_df.copy()
    df.columns = [str(col).strip() for col in df.columns]

    if len(set(df.columns)) != len(df.columns):
        raise ValueError(
            "Two or more column headers become identical after trimming. "
            "Rename the duplicate headers and upload again."
        )

    # Trim cell values in every text column.
    for col in df.columns:
        df[col] = df[col].map(clean_text)

    auto_fix_notes = [[] for _ in range(len(df))]
    original_row_count = len(df)

    # Blank means every cell is empty after trimming.
    blank_mask = (
        df.astype(str)
        .apply(lambda column: column.str.strip().eq(""))
        .all(axis=1)
    )

    blank_count = int(blank_mask.sum())

    if remove_blank_rows:
        keep_mask = (~blank_mask).tolist()
        auto_fix_notes = [
            notes for notes, keep in zip(auto_fix_notes, keep_mask) if keep
        ]
        df = df.loc[~blank_mask].copy()

    # Keep existing safe email/website normalization. With opt-in auto-fixes,
    # record exactly which selected fields changed and standardize phone formatting
    # without guessing a country code.
    if email_col and email_col in df.columns:
        before = df[email_col].copy()
        df[email_col] = df[email_col].map(normalize_email)
        if auto_fix_enabled:
            for position, (old, new) in enumerate(zip(before.tolist(), df[email_col].tolist())):
                if old != new:
                    auto_fix_notes[position].append("Email trimmed/lowercased")

    if website_col and website_col in df.columns:
        before = df[website_col].copy()
        df[website_col] = df[website_col].map(normalize_website)
        if auto_fix_enabled:
            for position, (old, new) in enumerate(zip(before.tolist(), df[website_col].tolist())):
                if old != new:
                    old_text = str(old).strip()
                    if old_text and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", old_text):
                        auto_fix_notes[position].append("Website scheme added")
                    else:
                        auto_fix_notes[position].append("Website trimmed")

    if auto_fix_enabled and phone_col and phone_col in df.columns:
        before = df[phone_col].copy()
        df[phone_col] = df[phone_col].map(normalize_phone)
        for position, (old, new) in enumerate(zip(before.tolist(), df[phone_col].tolist())):
            if old != new:
                auto_fix_notes[position].append("Phone formatting standardized")

    flags_by_row = [[] for _ in range(len(df))]

    def add_flag(mask, message):
        for position, is_flagged in enumerate(mask.tolist()):
            if bool(is_flagged):
                flags_by_row[position].append(message)

    # Required information checks depend on the selected record type.
    if validation_options["required_fields"]:
        if record_type == "Business leads" and company_col and company_col in df.columns:
            missing = df[company_col].astype(str).str.strip().eq("")
            add_flag(missing, "Company name missing")

        if record_type == "Contact lists":
            if first_name_col and first_name_col in df.columns:
                missing = df[first_name_col].astype(str).str.strip().eq("")
                add_flag(missing, "First name missing")
            if last_name_col and last_name_col in df.columns:
                missing = df[last_name_col].astype(str).str.strip().eq("")
                add_flag(missing, "Last name missing")

    if email_col and email_col in df.columns:
        email_values = df[email_col].astype(str).str.strip()
        missing = email_values.eq("")
        invalid = (~missing) & (~email_values.map(email_format_valid))

        if validation_options["missing_email"]:
            add_flag(missing, "Email missing")
        if validation_options["email_format"]:
            add_flag(invalid, "Email format needs review")

        if validation_options["duplicate_emails"]:
            present = email_values.ne("")
            duplicates = present & email_values.duplicated(keep=False)
            add_flag(duplicates, "Email appears more than once")

    if phone_col and phone_col in df.columns and validation_options["phone_format"]:
        phone_values = df[phone_col].astype(str).str.strip()
        present = phone_values.ne("")
        valid = phone_values.map(phone_format_valid)
        add_flag(present & (~valid), "Phone format needs review")

    if website_col and website_col in df.columns:
        website_values = df[website_col].astype(str).str.strip()
        missing = website_values.eq("")
        invalid = (
            (~missing)
            & (~website_values.map(website_format_valid))
        )

        if validation_options["missing_website"]:
            add_flag(missing, "Website missing")
        if validation_options["website_format"]:
            add_flag(invalid, "Website format needs review")

    # Flag exact duplicates across all cleaned source columns.
    # Keep every record; do not automatically discard duplicates.
    if validation_options["exact_duplicates"] and len(df.columns):
        exact_duplicates = df.duplicated(
            subset=list(df.columns),
            keep=False,
        )
        add_flag(exact_duplicates, "Exact duplicate record")

    # Fuzzy matching is opt-in and bounded to avoid unexpectedly expensive runs.
    # It flags likely matches only; it never merges or deletes records.
    if fuzzy_duplicates:
        if len(df) > 3000:
            raise ValueError(
                "Likely-duplicate name checking is limited to 3,000 rows per run. "
                "Turn it off or upload a smaller batch."
            )

        if record_type == "Business leads":
            if not company_col or company_col not in df.columns:
                raise ValueError(
                    "Map a company name column to use likely-duplicate name checking."
                )
            name_values = df[company_col].astype(str).str.strip()
        elif record_type == "Contact lists":
            if (
                not first_name_col
                or not last_name_col
                or first_name_col not in df.columns
                or last_name_col not in df.columns
            ):
                raise ValueError(
                    "Map both first and last name columns to use likely-duplicate checking."
                )
            name_values = (
                df[first_name_col].astype(str).str.strip()
                + " "
                + df[last_name_col].astype(str).str.strip()
            ).str.strip()
        else:
            raise ValueError("Unsupported record type for likely-duplicate checking.")

        def normalize_match(value):
            return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

        normalized_names = name_values.map(normalize_match).tolist()
        candidates = sorted({value for value in normalized_names if value})
        likely_duplicate_positions = set()

        for position, value in enumerate(normalized_names):
            if not value:
                continue
            matches = process.extract(
                value,
                candidates,
                scorer=fuzz.ratio,
                score_cutoff=fuzzy_threshold,
                limit=10,
            )
            if any(
                candidate != value and score >= fuzzy_threshold
                for candidate, score, _ in matches
            ):
                likely_duplicate_positions.add(position)

        for position in sorted(likely_duplicate_positions):
            flags_by_row[position].append("Possible name duplicate")

    if custom_regex_enabled:
        if not custom_regex_col or custom_regex_col not in df.columns:
            raise ValueError("Choose a column for the custom regex check.")
        if not custom_regex_pattern or not custom_regex_pattern.strip():
            raise ValueError("Enter a regex pattern for the custom check.")
        try:
            compiled_pattern = re.compile(custom_regex_pattern)
        except re.error as exc:
            raise ValueError(f"Custom regex pattern is invalid: {exc}") from exc

        custom_values = df[custom_regex_col].astype(str).str.strip()
        present = custom_values.ne("")
        matches_pattern = custom_values.map(
            lambda value: bool(compiled_pattern.fullmatch(value))
        )
        add_flag(
            present & (~matches_pattern),
            f"Custom format needs review ({custom_regex_col})",
        )

    # Keep the auto-fix audit separate: a formatting change alone is not a validation failure.
    df["LeadProof Auto-Fixes"] = ["; ".join(notes) for notes in auto_fix_notes]

    # Add review results without removing or replacing original fields.
    df["LeadProof Status"] = [
        "Review needed" if row_flags else "Ready for review"
        for row_flags in flags_by_row
    ]

    df["LeadProof Flags"] = [
        "; ".join(row_flags) if row_flags else ""
        for row_flags in flags_by_row
    ]

    flagged_mask = df["LeadProof Flags"].ne("")
    flagged_df = df.loc[flagged_mask].copy()

    summary = {
        "uploaded_rows": original_row_count,
        "blank_rows": blank_count,
        "blank_rows_removed": (
            blank_count if remove_blank_rows else 0
        ),
        "processed_rows": len(df),
        "flagged_rows": int(flagged_mask.sum()),
        "ready_rows": int((~flagged_mask).sum()),
        "exact_duplicate_rows": int(
            df["LeadProof Flags"]
            .str.contains("Exact duplicate record", regex=False)
            .sum()
        ),
        "auto_fixed_rows": int(df["LeadProof Auto-Fixes"].ne("").sum()),
        "auto_fix_count": int(sum(len(notes) for notes in auto_fix_notes)),
    }
    summary["health_score"] = calculate_health_score(summary)
    summary["flag_breakdown"] = build_flag_breakdown(df)

    return df, flagged_df, summary


def build_excel(cleaned_df, flagged_df, summary):
    output = BytesIO()

    summary_rows = [
        {"Metric": key, "Value": value}
        for key, value in summary.items()
        if key != "flag_breakdown"
    ]
    summary_rows.append({"Metric": "Flag breakdown", "Value": ""})
    summary_rows.extend(
        {"Metric": f"Flag: {flag}", "Value": count}
        for flag, count in summary.get("flag_breakdown", {}).items()
    )
    summary_df = pd.DataFrame(summary_rows)

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        cleaned_df.to_excel(
            writer,
            sheet_name="Processed Leads",
            index=False,
        )
        flagged_df.to_excel(
            writer,
            sheet_name="Needs Review",
            index=False,
        )
        summary_df.to_excel(
            writer,
            sheet_name="Audit Summary",
            index=False,
        )

    output.seek(0)
    return output.getvalue()


# -------------------------
# Application interface
# -------------------------

with st.sidebar:
    st.markdown("## LeadProof")
    st.caption("Lead and contact data quality")
    st.divider()
    st.markdown("**Current version**")
    st.caption("V3 · Auto-fixes, inline review, and lead health analytics")
    st.info(
        "Website checks validate URL format only; they do not visit "
        "websites or confirm that a site is live."
    )

st.markdown(
    """
    <div class="hero">
        <p class="muted">LEAD DATA QUALITY</p>
        <h1 style="margin-bottom: 8px;">Make every lead easier to trust.</h1>
        <p style="margin-bottom: 0;">
            Clean business leads and contact lists, find duplicates, and flag records
            that need review. Your original upload stays untouched.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown("### 1. Upload your lead list")
uploaded_file = st.file_uploader(
    "Choose a CSV or Excel file",
    type=["csv", "xlsx"],
    help="Maximum file size: 20 MB. Processing happens in this app session.",
)

if uploaded_file is None:
    st.markdown("#### Not ready with a file?")
    st.caption(
        "You can start with a small sample spreadsheet to test the workflow."
    )

    sample_df = pd.DataFrame(
        [
            {
                "Company": "  Northstar Media  ",
                "Email": " SALES@NORTHSTAR.EXAMPLE ",
                "Phone": "+92 300 123 4567",
                "Website": "northstar.example",
            },
            {
                "Company": "Northstar Media",
                "Email": "sales@northstar.example",
                "Phone": "+92 300 123 4567",
                "Website": "northstar.example",
            },
            {
                "Company": "  Blue Oak Systems",
                "Email": "contact.example.com",
                "Phone": "12",
                "Website": "not a website",
            },
            {
                "Company": "",
                "Email": "",
                "Phone": "",
                "Website": "",
            },
        ]
    )

    st.dataframe(sample_df, use_container_width=True, hide_index=True)

    st.download_button(
        "Download sample CSV",
        data=sample_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="leadproof_sample.csv",
        mime="text/csv",
    )
    st.stop()

try:
    source_df = read_uploaded_file(uploaded_file)
except Exception as exc:
    st.error(f"Could not read this file: {exc}")
    st.stop()

if source_df.empty or len(source_df.columns) == 0:
    st.warning("This file has no usable rows or columns.")
    st.stop()

if len(source_df) > 100_000:
    st.error("This version supports up to 100,000 rows per upload.")
    st.stop()

# Clean headers for display and selection.
source_df.columns = [str(col).strip() for col in source_df.columns]

if len(set(source_df.columns)) != len(source_df.columns):
    st.error(
        "Column names are duplicated or become duplicates after trimming. "
        "Please rename them in Excel and upload the file again."
    )
    st.stop()

upload_signature = upload_content_signature(uploaded_file.getvalue())

st.success(
    f"Loaded **{len(source_df):,} rows** and "
    f"**{len(source_df.columns):,} columns**."
)

with st.expander("Preview uploaded data", expanded=True):
    st.dataframe(
        source_df.head(10),
        use_container_width=True,
        hide_index=True,
    )

st.markdown("### 2. Map your columns")
st.caption(
    "LeadProof suggests columns from their names. Check these selections "
    "before processing. Leave a field as None if it is not available."
)

record_type = st.radio(
    "What kind of data are you checking?",
    ["Business leads", "Contact lists"],
    horizontal=True,
    help="Contact lists do not require a company name, phone number, or website.",
)

columns = list(source_df.columns)
options = ["None"] + columns
header_signature = make_header_signature(columns)
saved_mappings = load_mapping_preferences(header_signature)

email_guess = find_column(columns, ["email", "e-mail", "mail"])
phone_guess = find_column(
    columns,
    ["phone", "mobile", "telephone", "tel", "cell", "whatsapp"],
)
website_guess = find_column(
    columns,
    ["website", "web site", "domain", "url", "company site"],
)
company_guess = find_column(
    columns,
    ["company", "business", "organization", "organisation", "account"],
)
first_name_guess = find_column(
    columns,
    ["first_name", "first name", "firstname", "given_name", "given name"],
)
last_name_guess = find_column(
    columns,
    ["last_name", "last name", "lastname", "family_name", "family name", "surname"],
)

def default_index(field, guess):
    if saved_mappings is not None and field in saved_mappings:
        saved_value = saved_mappings[field]

        # A saved None is intentional; don't replace it with a guess.
        if saved_value is None:
            return 0

        for index, option in enumerate(options):
            if (
                option != "None"
                and normalize_header(option) == saved_value
            ):
                return index

    return options.index(guess) if guess in options else 0


def mapping_value(choice):
    return None if choice == "None" else normalize_header(choice)

with st.form("mapping_form"):
    col1, col2 = st.columns(2)

    with col1:
        company_choice = st.selectbox(
            "Company name column",
            options,
            index=default_index("company", company_guess),
            key=f"mapping_company_{header_signature[:12]}",
        )
        email_choice = st.selectbox(
            "Email column",
            options,
            index=default_index("email", email_guess),
            key=f"mapping_email_{header_signature[:12]}",
        )
        first_name_choice = st.selectbox(
            "First name column",
            options,
            index=default_index("first_name", first_name_guess),
            key=f"mapping_first_name_{header_signature[:12]}",
        )

    with col2:
        phone_choice = st.selectbox(
            "Phone column",
            options,
            index=default_index("phone", phone_guess),
            key=f"mapping_phone_{header_signature[:12]}",
        )
        website_choice = st.selectbox(
            "Website column",
            options,
            index=default_index("website", website_guess),
            key=f"mapping_website_{header_signature[:12]}",
        )
        last_name_choice = st.selectbox(
            "Last name column",
            options,
            index=default_index("last_name", last_name_guess),
            key=f"mapping_last_name_{header_signature[:12]}",
        )

    st.markdown("**Choose which checks to run**")
    check_col1, check_col2, check_col3 = st.columns(3)

    with check_col1:
        check_required_fields = st.checkbox(
            "Required names/company",
            value=True,
            help="Flags missing company names for business leads, or missing first/last names for contact lists.",
        )
        check_missing_email = st.checkbox("Missing email", value=True)
        check_email_format = st.checkbox("Email format", value=True)

    with check_col2:
        check_duplicate_emails = st.checkbox("Duplicate emails", value=True)
        check_phone_format = st.checkbox("Phone format", value=True)
        check_missing_website = st.checkbox(
            "Missing website",
            value=(record_type == "Business leads"),
            key=(
                "check_missing_website_business"
                if record_type == "Business leads"
                else "check_missing_website_contacts"
            ),
            help="Off by default for contact lists because a website is optional.",
        )

    with check_col3:
        check_website_format = st.checkbox("Website format", value=True)
        check_exact_duplicates = st.checkbox("Exact duplicate records", value=True)
        check_fuzzy_duplicates = st.checkbox(
            "Likely duplicate names (slower)",
            value=False,
            help="Opt-in similarity check for company names or contact full names. Limited to 3,000 rows per run.",
        )

    fuzzy_threshold = st.slider(
        "Name similarity threshold",
        min_value=75,
        max_value=100,
        value=90,
        step=1,
        help="Higher values flag fewer, closer matches. This is a review hint, not proof of duplication.",
        disabled=not check_fuzzy_duplicates,
    )

    st.markdown("**Optional custom format rule**")
    check_custom_regex = st.checkbox(
        "Validate a column against my regex pattern",
        value=False,
        help="Useful for IDs, postal codes, account codes, or other formats. Non-empty values that do not fully match will be flagged.",
    )
    custom_regex_col = st.selectbox(
        "Column to validate",
        options,
        index=0,
        key=f"mapping_custom_regex_{header_signature[:12]}",
        disabled=not check_custom_regex,
    )
    custom_regex_pattern = st.text_input(
        "Regex pattern",
        value="",
        placeholder=r"Example: ^[A-Z]{2}-\d{4}$",
        help="Uses Python regular expressions and full-string matching.",
        disabled=not check_custom_regex,
    )

    validation_options = {
        "required_fields": check_required_fields,
        "missing_email": check_missing_email,
        "email_format": check_email_format,
        "duplicate_emails": check_duplicate_emails,
        "phone_format": check_phone_format,
        "missing_website": check_missing_website,
        "website_format": check_website_format,
        "exact_duplicates": check_exact_duplicates,
    }

    remove_blanks = st.checkbox(
        "Remove completely blank rows",
        value=True,
        help=(
            "Only rows where every cell is empty after trimming are removed. "
            "The number removed is recorded in the audit summary."
        ),
    )

    auto_fix_enabled = st.checkbox(
        "Apply safe auto-fixes and record changes",
        value=False,
        help=(
            "Standardizes selected phone values without guessing a country code, "
            "and records selected email/website normalization in the audit trail. "
            "Existing email and website normalization is preserved."
        ),
    )

    run_processing = st.form_submit_button(
        "Process lead list",
        type="primary",
        use_container_width=True,
    )

current_config_signature = processing_config_signature(
    {
        "record_type": record_type,
        "company": company_choice,
        "email": email_choice,
        "phone": phone_choice,
        "website": website_choice,
        "first_name": first_name_choice,
        "last_name": last_name_choice,
        "remove_blank_rows": remove_blanks,
        "auto_fix_enabled": auto_fix_enabled,
        "validation_options": validation_options,
        "fuzzy_duplicates": check_fuzzy_duplicates,
        "fuzzy_threshold": fuzzy_threshold,
        "custom_regex_enabled": check_custom_regex,
        "custom_regex_col": custom_regex_col,
        "custom_regex_pattern": custom_regex_pattern,
    }
)

if run_processing:
    # Clear previous output before attempting to process this upload again.
    st.session_state.pop("leadproof_result", None)

    email_col = None if email_choice == "None" else email_choice
    phone_col = None if phone_choice == "None" else phone_choice
    website_col = None if website_choice == "None" else website_choice
    company_col = None if company_choice == "None" else company_choice
    first_name_col = None if first_name_choice == "None" else first_name_choice
    last_name_col = None if last_name_choice == "None" else last_name_choice

    if record_type == "Contact lists":
        company_col = None

    try:
        cleaned_df, flagged_df, summary = process_leads(
            source_df=source_df,
            email_col=email_col,
            phone_col=phone_col,
            website_col=website_col,
            company_col=company_col,
            first_name_col=first_name_col,
            last_name_col=last_name_col,
            record_type=record_type,
            remove_blank_rows=remove_blanks,
            validation_options=validation_options,
            fuzzy_duplicates=check_fuzzy_duplicates,
            fuzzy_threshold=fuzzy_threshold,
            custom_regex_enabled=check_custom_regex,
            custom_regex_col=(
                None if custom_regex_col == "None" else custom_regex_col
            ),
            custom_regex_pattern=custom_regex_pattern,
            auto_fix_enabled=auto_fix_enabled,
        )

        mapping_saved = save_mapping_preferences(
            header_signature,
            {
                "company": mapping_value(company_choice),
                "email": mapping_value(email_choice),
                "first_name": mapping_value(first_name_choice),
                "phone": mapping_value(phone_choice),
                "website": mapping_value(website_choice),
                "last_name": mapping_value(last_name_choice),
            },
        )
        st.session_state["leadproof_mapping_save_status"] = mapping_saved

        st.session_state["leadproof_result"] = {
            "cleaned": cleaned_df,
            "flagged": flagged_df,
            "summary": summary,
            "filename": uploaded_file.name,
            "file_signature": upload_signature,
            "config_signature": current_config_signature,
            "record_type": record_type,
            "processing_args": {
                "email_col": email_col,
                "phone_col": phone_col,
                "website_col": website_col,
                "company_col": company_col,
                "first_name_col": first_name_col,
                "last_name_col": last_name_col,
                "record_type": record_type,
                "remove_blank_rows": remove_blanks,
                "validation_options": validation_options.copy(),
                "fuzzy_duplicates": check_fuzzy_duplicates,
                "fuzzy_threshold": fuzzy_threshold,
                "custom_regex_enabled": check_custom_regex,
                "custom_regex_col": None if custom_regex_col == "None" else custom_regex_col,
                "custom_regex_pattern": custom_regex_pattern,
                "auto_fix_enabled": auto_fix_enabled,
            },
        }
        if mapping_saved:
            st.caption("Column mapping preferences saved for this header structure.")
        else:
            st.caption(
                "Column mappings are remembered for this session, but "
                "persistent storage is unavailable."
            )
    except Exception as exc:
        st.error(f"Processing failed safely: {exc}")

result = st.session_state.get("leadproof_result")

if (
    result
    and result.get("file_signature") == upload_signature
    and result.get("config_signature") == current_config_signature
    and result.get("record_type") == record_type
):
    cleaned_df = result["cleaned"]
    flagged_df = result["flagged"]
    summary = result["summary"]

    st.divider()
    st.markdown("### 3. Results")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Processed rows", f"{summary['processed_rows']:,}")
    m2.metric("Ready for review", f"{summary['ready_rows']:,}")
    m3.metric("Flagged rows", f"{summary['flagged_rows']:,}")
    m4.metric("Lead health score", f"{summary['health_score']}/100")
    m5.metric("Auto-fixed rows", f"{summary['auto_fixed_rows']:,}")

    health_left, health_right = st.columns([1, 2])
    with health_left:
        st.progress(summary["health_score"] / 100, text=f"Lead health · {summary['health_score']}/100")
        st.caption("Score = records with no active validation flags ÷ processed records. A format pass is not proof a lead is genuine.")
    with health_right:
        breakdown = summary.get("flag_breakdown", {})
        if breakdown:
            st.markdown("**Flag breakdown**")
            chart_df = pd.DataFrame(
                [{"Issue type": label, "Records": count} for label, count in breakdown.items()]
            ).set_index("Issue type")
            st.bar_chart(chart_df)
        else:
            st.success("No validation flags to chart.")

    if summary["auto_fix_count"]:
        st.caption(
            f"Recorded {summary['auto_fix_count']:,} safe formatting changes across "
            f"{summary['auto_fixed_rows']:,} rows in the LeadProof Auto-Fixes column."
        )

    if summary["blank_rows_removed"]:
        st.caption(
            f"Removed {summary['blank_rows_removed']:,} completely blank "
            "rows. No nonblank records were automatically deleted."
        )

    tab1, tab2, tab3 = st.tabs(
        ["Processed leads", "Needs review", "Audit summary"]
    )

    with tab1:
        st.dataframe(
            cleaned_df,
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "Download processed CSV",
            data=cleaned_df.to_csv(index=False).encode("utf-8-sig"),
            file_name="leadproof_processed.csv",
            mime="text/csv",
            key="download_csv",
        )

    with tab2:
        if flagged_df.empty:
            st.success("No issues were flagged by the selected checks.")
        else:
            st.caption(
                "Edit cells directly below, then save to rerun the same validation "
                "checks across the updated list. Changes stay in this session until exported."
            )
            editable_columns = [
                col for col in cleaned_df.columns
                if col not in ("LeadProof Status", "LeadProof Flags", "LeadProof Auto-Fixes")
            ]
            editor_input = flagged_df.copy()
            edited_rows = st.data_editor(
                editor_input,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"leadproof_review_editor_{upload_signature[:12]}_{current_config_signature[:8]}_{result.get('editor_revision', 0)}",
                column_config={
                    "LeadProof Status": st.column_config.TextColumn(disabled=True),
                    "LeadProof Flags": st.column_config.TextColumn(disabled=True),
                    "LeadProof Auto-Fixes": st.column_config.TextColumn(disabled=True),
                },
            )
            if st.button("Save edits & recheck all rows", type="primary", key="save_review_edits"):
                try:
                    edited_base = cleaned_df.drop(
                        columns=["LeadProof Status", "LeadProof Flags", "LeadProof Auto-Fixes"],
                        errors="ignore",
                    ).copy()
                    for row_index in edited_rows.index:
                        if row_index in edited_base.index:
                            for col in editable_columns:
                                edited_base.at[row_index, col] = edited_rows.at[row_index, col]
                    prior_auto_fixes = cleaned_df.get(
                        "LeadProof Auto-Fixes", pd.Series("", index=cleaned_df.index)
                    )
                    rechecked_df, rechecked_flagged, rechecked_summary = process_leads(
                        source_df=edited_base,
                        **result["processing_args"],
                    )
                    if "LeadProof Auto-Fixes" in rechecked_df.columns:
                        for row_index in rechecked_df.index:
                            previous = str(prior_auto_fixes.get(row_index, "") or "")
                            current = str(rechecked_df.at[row_index, "LeadProof Auto-Fixes"] or "")
                            notes = list(dict.fromkeys(
                                [part.strip() for part in (previous + "; " + current).split(";") if part.strip()]
                            ))
                            rechecked_df.at[row_index, "LeadProof Auto-Fixes"] = "; ".join(notes)
                        rechecked_flagged = rechecked_df.loc[
                            rechecked_df["LeadProof Flags"].ne("")
                        ].copy()
                        rechecked_summary["auto_fixed_rows"] = int(
                            rechecked_df["LeadProof Auto-Fixes"].ne("").sum()
                        )
                        rechecked_summary["auto_fix_count"] = int(sum(
                            len([part for part in str(value).split(";") if part.strip()])
                            for value in rechecked_df["LeadProof Auto-Fixes"]
                        ))
                    result["cleaned"] = rechecked_df
                    result["flagged"] = rechecked_flagged
                    result["summary"] = rechecked_summary
                    result["user_edited"] = True
                    result["editor_revision"] = int(result.get("editor_revision", 0)) + 1
                    st.session_state["leadproof_result"] = result
                    st.success("Edits saved and validation rerun. Review the updated results above.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not apply edits safely: {exc}")

        st.download_button(
            "Download review CSV",
            data=flagged_df.to_csv(index=False).encode("utf-8-sig"),
            file_name="leadproof_needs_review.csv",
            mime="text/csv",
            key="download_review_csv",
        )

    with tab3:
        audit_rows = [
            {"Metric": key.replace("_", " ").title(), "Value": value}
            for key, value in summary.items()
            if key != "flag_breakdown"
        ]
        audit_rows.extend(
            {"Metric": f"Flag: {flag}", "Value": count}
            for flag, count in summary.get("flag_breakdown", {}).items()
        )
        st.dataframe(
            pd.DataFrame(audit_rows),
            use_container_width=True,
            hide_index=True,
        )

    excel_bytes = build_excel(cleaned_df, flagged_df, summary)

    st.download_button(
        "Download complete Excel report",
        data=excel_bytes,
        file_name="leadproof_report.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        type="primary",
        use_container_width=True,
    )

st.divider()
st.caption(
    "LeadProof V3 · Format checks are not proof of deliverability, "
    "phone ownership, website availability, or business legitimacy."
)