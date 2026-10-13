# LeadProof

LeadProof is a small data-quality workspace for business leads and contact lists.

## Features

- Upload CSV and Excel files.
- Map columns for names, email, phone, website, and company.
- Flag common data-quality issues for human review.
- Identify exact duplicate rows and repeated email addresses.
- Export processed records and review lists as CSV.
- Export an Excel report with Processed Leads, Needs Review, and Audit Summary sheets.

## Run locally

Python 3.10 or newer is recommended.

1. Create and activate a virtual environment.
2. Install dependencies:
   `python -m pip install -r requirements.txt`
3. Start the app:
   `python -m streamlit run app.py`

## Current limitations

- Website checks validate URL format only; they do not confirm that a website is live or legitimate.
- Email checks do not confirm deliverability.
- Phone checks do not confirm ownership.
- Scores and date formats are not fully validated.
- Flags are intended to support human review, not certify data as accurate.

## Privacy

Process only data you are authorized to use. Review your hosting provider's privacy and file-handling practices before uploading real business or personal data to a hosted deployment.
