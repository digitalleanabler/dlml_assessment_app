# DLML Collaborative Assessment

Streamlit proof of concept for a company-shared, data-driven survey. The Google Spreadsheet is the source of truth for survey configuration, users, current responses, and response history.

## Run locally

```powershell
cd dlml_assessment
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Without secrets the app starts in **developer demo mode**, seeded with two companies, three users, zero answers, and the required Q1/Q2/Q3 visibility behavior. Enter one of the seeded company/email pairs manually on the sign-in page. This fallback is intentionally not shared between browser sessions.

## Connect the production Sheet

1. In Google Cloud, enable the Google Sheets API and create a service account.
2. Share the target Sheet with the service account’s `client_email` as an Editor.
3. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and replace the placeholders with the downloaded service-account JSON fields.
4. Keep `secrets.toml` out of source control. On a host, enter the same values in its secret manager.

The workbook must have these exact worksheets and header rows: `Companies`, `Users`, `Questions`, `QuestionOptions`, `QuestionConditions`, `Responses`, and `ResponseHistory`. The column names must match `database design r01.docx`.

## Behaviour implemented

- The initial demonstration data contains two companies and three users; no questions are answered.
- Q1 and Q2 are initially visible. Q3 is active and appears only when Q2’s stored option value is `ALL`.
- If Q2 changes away from `ALL`, Q3 hides but its saved answer remains in `Responses`.
- Changing an answer creates a browser-session draft only. **Save responses** updates `Responses` and appends one `ResponseHistory` record for each changed value; the survey remains editable until it is submitted.
- Every authorised user of a company works on the same company response set. Reload shows other users’ saves.
- Submission changes `Companies.Status` to `Submitted` and makes the company’s survey read-only.

## Google sign-in

The current app checks the manually entered company name and email against the `Companies` and `Users` worksheets before access is granted. Before public deployment, replace manual email entry with a Google OAuth sign-in flow and use the authenticated email to perform the same `repo.user(email, company_id)` authorisation check. The Sheets service account is for backend access; it does not sign users in.
