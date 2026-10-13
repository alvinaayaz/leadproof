# LeadProof

LeadProof is a lightweight data-quality workspace for business leads and contact lists. It helps clean formatting, surface questionable records, and export results for human review. It does not certify that a lead is real or reachable.

## Features

- Upload CSV and Excel (.xlsx) files, up to 20 MB and 100,000 rows.
- Map company, contact name, email, phone, and website columns.
- Remember column mappings for matching header structures on the same writable installation.
- Choose which standard validation checks to run:
  - Missing required company/contact names
  - Missing email or website values
  - Email and website format checks
  - Broad phone-number format checks
  - Repeated email addresses and exact duplicate rows
- Optional fuzzy name matching for likely duplicate company/contact names, with an adjustable similarity threshold. This check is limited to 3,000 rows per run and never deletes or merges records.
- Optional custom regex validation for IDs, postal codes, and other user-defined formats.
- Export processed data and review lists as CSV, or an Excel report with Processed Leads, Needs Review, and Audit Summary sheets.
- Automated unit tests run through GitHub Actions on pushes and pull requests.

## Run locally

Python 3.10 or newer is recommended.

1. Create and activate a virtual environment.
2. Install dependencies:
   `python -m pip install -r requirements.txt`
3. Start the app:
   `python -m streamlit run app.py`

Run automated checks locally with:

`python -m unittest discover -s tests -v`

## Important limitations

- Email checks validate syntax only; they do not confirm deliverability or mailbox ownership.
- Phone checks validate a broad digit-count format only; they do not confirm that a number is assigned or belongs to the listed contact.
- Website checks validate URL format only; they do not visit the website or confirm that it is live, safe, or legitimate.
- Fuzzy matching is a similarity hint, not proof that two records refer to the same entity.
- Custom regex rules only validate the pattern the user supplies; they do not prove the underlying data is correct.
- Completely blank rows can be removed when the user enables that option. Other records are flagged for review, not automatically deleted.
- Mapping preferences store column names and header signatures, not uploaded row data. The local SQLite file must be writable. Some hosted platforms use ephemeral filesystems, so saved preferences may not survive a redeploy or restart.
- Uploaded data is processed in the app session. Review your hosting provider's privacy and file-handling practices before uploading sensitive or personal data.

## Privacy

Only upload data you are authorized to process. Avoid uploading sensitive personal data unless you understand and accept the storage, access, and retention properties of your chosen hosting environment.
