
from io import BytesIO
from urllib.parse import urlparse
import re

import pandas as pd
import streamlit as st


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
):
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

    original_row_count = len(df)

    # Blank means every cell is empty after trimming.
    blank_mask = (
        df.astype(str)
        .apply(lambda column: column.str.strip().eq(""))
        .all(axis=1)
    )

    blank_count = int(blank_mask.sum())

    if remove_blank_rows:
        df = df.loc[~blank_mask].copy()

    # Normalize only fields selected by the user.
    if email_col and email_col in df.columns:
        df[email_col] = df[email_col].map(normalize_email)

    if website_col and website_col in df.columns:
        df[website_col] = df[website_col].map(normalize_website)

    flags_by_row = [[] for _ in range(len(df))]

    def add_flag(mask, message):
        for position, is_flagged in enumerate(mask.tolist()):
            if bool(is_flagged):
                flags_by_row[position].append(message)

    # Required information checks depend on the selected record type.
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

        add_flag(missing, "Email missing")
        add_flag(invalid, "Email format needs review")

        present = email_values.ne("")
        duplicates = present & email_values.duplicated(keep=False)
        add_flag(duplicates, "Email appears more than once")

    if phone_col and phone_col in df.columns:
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

        add_flag(missing, "Website missing")
        add_flag(invalid, "Website format needs review")

    # Flag exact duplicates across all cleaned source columns.
    # Keep every record; do not automatically discard duplicates.
    if len(df.columns):
        exact_duplicates = df.duplicated(
            subset=list(df.columns),
            keep=False,
        )
        add_flag(
            exact_duplicates,
            "Exact duplicate record",
        )

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
    }

    return df, flagged_df, summary


def build_excel(cleaned_df, flagged_df, summary):
    output = BytesIO()

    summary_df = pd.DataFrame(
        [
            {"Metric": key, "Value": value}
            for key, value in summary.items()
        ]
    )

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
    st.caption("V1 · File processing and data quality")
    st.info(
        "Website checks in this version validate URL format only. "
        "Live website research will be added after the core workflow "
        "is tested."
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

def default_index(guess):
    return options.index(guess) if guess in options else 0

with st.form("mapping_form"):
    col1, col2 = st.columns(2)

    with col1:
        company_choice = st.selectbox(
            "Company name column",
            options,
            index=default_index(company_guess),
        )
        email_choice = st.selectbox(
            "Email column",
            options,
            index=default_index(email_guess),
        )
        first_name_choice = st.selectbox(
            "First name column",
            options,
            index=default_index(first_name_guess),
        )

    with col2:
        phone_choice = st.selectbox(
            "Phone column",
            options,
            index=default_index(phone_guess),
        )
        website_choice = st.selectbox(
            "Website column",
            options,
            index=default_index(website_guess),
        )
        last_name_choice = st.selectbox(
            "Last name column",
            options,
            index=default_index(last_name_guess),
        )

    remove_blanks = st.checkbox(
        "Remove completely blank rows",
        value=True,
        help=(
            "Only rows where every cell is empty after trimming are removed. "
            "The number removed is recorded in the audit summary."
        ),
    )

    run_processing = st.form_submit_button(
        "Process lead list",
        type="primary",
        use_container_width=True,
    )

if run_processing:
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
        )

        st.session_state["leadproof_result"] = {
            "cleaned": cleaned_df,
            "flagged": flagged_df,
            "summary": summary,
            "filename": uploaded_file.name,
            "record_type": record_type,
        }
    except Exception as exc:
        st.error(f"Processing failed safely: {exc}")

result = st.session_state.get("leadproof_result")

if (
    result
    and result["filename"] == uploaded_file.name
    and result.get("record_type") == record_type
):
    cleaned_df = result["cleaned"]
    flagged_df = result["flagged"]
    summary = result["summary"]

    st.divider()
    st.markdown("### 3. Results")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Processed rows", f"{summary['processed_rows']:,}")
    m2.metric("Ready for review", f"{summary['ready_rows']:,}")
    m3.metric("Flagged rows", f"{summary['flagged_rows']:,}")
    m4.metric("Exact duplicate rows", f"{summary['exact_duplicate_rows']:,}")

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
                "Flags indicate records to inspect. They do not automatically "
                "mean a business or contact is invalid."
            )
            st.dataframe(
                flagged_df,
                use_container_width=True,
                hide_index=True,
            )

        st.download_button(
            "Download review CSV",
            data=flagged_df.to_csv(index=False).encode("utf-8-sig"),
            file_name="leadproof_needs_review.csv",
            mime="text/csv",
            key="download_review_csv",
        )

    with tab3:
        st.dataframe(
            pd.DataFrame(
                [{"Metric": key, "Value": value}
                 for key, value in summary.items()]
            ),
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
    "LeadProof V1 · Format checks are not proof of deliverability, "
    "phone ownership, website availability, or business legitimacy."
)