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
- Opt-in safe auto-fixes with a per-row audit trail for email/website normalization and phone formatting. Phone cleanup preserves digits and an existing leading `+`; it does not guess a country code or claim E.164 validity.
- Edit flagged rows inline in the app, save changes, and rerun the same checks across the updated list before exporting.
- A Lead Health Score showing the percentage of processed rows with no active validation flags, plus a chart breaking down flag types.
- Export processed data and review lists as CSV, or an Excel report with Processed Leads, Needs Review, and an Audit Summary containing the health score and flag counts.
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

## Deploy to Streamlit Community Cloud

1. Push this repository to GitHub.
2. Open https://share.streamlit.io/ and create a new app.
3. Select this repository, the `master` branch, and `app.py` as the entry point.
4. Let Streamlit Cloud install dependencies from `requirements.txt`.
5. Verify CSV and Excel uploads, processing, and report downloads.

Column-mapping preferences use a local SQLite database and may not persist across hosted app restarts or redeployments.

## Important limitations

- Email checks validate syntax only; they do not confirm deliverability or mailbox ownership.
- Phone checks validate a broad digit-count format only; they do not confirm that a number is assigned or belongs to the listed contact.
- Website checks validate URL format only; they do not visit the website or confirm that it is live, safe, or legitimate.
- Fuzzy matching is a similarity hint, not proof that two records refer to the same entity.
- Custom regex rules only validate the pattern the user supplies; they do not prove the underlying data is correct.
- Completely blank rows can be removed when the user enables that option. Other records are flagged for review, not automatically deleted.
- The Lead Health Score is a simple, explainable completeness/validation indicator: the percentage of processed rows without active flags. It is not a measure of lead authenticity or business quality.
- Email and website normalization are part of the existing processing flow. The opt-in auto-fix setting adds phone formatting cleanup and records detected formatting changes in the `LeadProof Auto-Fixes` column. Phone formatting does not infer a country code, and no correction can guarantee the underlying value is accurate.
- Inline edits update the current session's results. Download the updated report to keep the edits; the original upload itself is not modified.
- Mapping preferences store column names and header signatures, not uploaded row data. The local SQLite file must be writable. Some hosted platforms use ephemeral filesystems, so saved preferences may not survive a redeploy or restart.
- Uploaded data is processed in the app session. Review your hosting provider's privacy and file-handling practices before uploading sensitive or personal data.

## Privacy

Only upload data you are authorized to process. Avoid uploading sensitive personal data unless you understand and accept the storage, access, and retention properties of your chosen hosting environment.
