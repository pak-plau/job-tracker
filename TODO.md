# To do

Last updated 2026-11-04. Run commands in PowerShell from the `job-tracker` folder.

To pick this back up with Claude Code, open it in this folder and say "continue from TODO.md".

## Your next steps

- [ ] **Finish connecting Google.** Skip this if you've already finished the wizard and saved your credentials.
  ```powershell
  & "C:\Program Files\Git\bin\bash.exe" scripts/setup-google.sh
  ```
  The Google app stays in Testing, so add your Gmail address as a test user. Don't try to publish it.

- [ ] **Label about 30 emails.** You can quit with `q` and run it again to resume.
  ```powershell
  .venv\Scripts\python.exe -m evals.label
  ```
  At the first sign-in, choose Advanced and continue past the unverified app warning, then tick the box allowing email access.

  | Key | Use it for | Target |
  |---|---|---|
  | `c` | The company received your application | 5-8 |
  | `i` | Invitation to a new next step, including availability requests and assessments | 5 |
  | `m` | Request for missing information, documents, or to finish a form | 3-5 |
  | `r` | The company isn't moving forward | 5 |
  | `o` | Job offer | Any you have |
  | `u` | About your application, but you can't tell what it means | 1-3 |
  | `j` | Newsletter or alert listing open roles you could apply to | 3-5 |
  | `n` | Anything else, including other newsletters and interview reminders | About 7 |
  | `s` | Skip, leaving it out of the test | |
  | `q` | Save and quit | |

  If you're short on one type, search further back:
  ```powershell
  .venv\Scripts\python.exe -m evals.label --days 365 --query unfortunately
  ```

- [ ] **Compare the models.** It shows nothing until it finishes.
  ```powershell
  .venv\Scripts\python.exe -m evals.compare | Select-String 'correct,|^Best'
  ```

- [ ] **Paste the score lines to Claude.** Never paste email subjects or text, because chat content goes to Anthropic.

## Claude's next steps

- [x] Count how many labeled emails hit the 8,000-character limit: 1 of 49, so the limit stays.
- [x] Pick the default model from the scores: `gemma3:4b`, at 42/49 correct, 2 wrong status and 5 lost. All 5 lost are irrelevant emails filed as `job_alert`, which are discarded either way.
- [x] Stop confirmations being filed as `job_alert`: job site emails saying an application was sent didn't fit the `confirmation` definition, which is now wider.
- [ ] Test more interview emails and any offer emails: the 49 labeled emails include 2 interviews and no offers.
- [x] Delete `gemma3:1b` and `granite4.2:3b` from Ollama.
- [ ] Delete `qwen3.5:2b` from Ollama and `compare.py` once offers are tested, if `gemma3:4b` still wins.

## Still to build

1. [x] Model test: Gmail connection, labeling and comparison scripts
2. [ ] Classification and matching rules, with tests that use made-up emails
3. [ ] Database and `sync`
4. [ ] Web page
5. [ ] Setup guide for people cloning the repo

Step 1 is committed and pushed on the `model-test` branch.

## Agreed design

See `GLOSSARY.md` for terms and `docs/adr/0001-local-only.md` for why this runs locally.

- **Setup:** Python and SQLite, with one private copy per person. Credentials, test emails and the database stay in the git-ignored `data/` folder.
- **Gmail:** `gmail.readonly` only, with stored links to emails and no labels. The app stays in Testing, so sign-in expires every 7 days. The page must say so clearly, and sync must resume without missing mail.
- **Model:** Ollama, with temperature 0, thinking off and a fixed JSON format. Categories are confirmation, interview, info_request, rejection, offer, unclear, job_alert and irrelevant. A job_alert email stays out of the Applications table and the Review List.
- **Statuses:** Applied, Interviewing, Rejected and Offer. Forward moves are automatic, contradictions of a final status go to the Review List, and repeats are only logged.
- **Matching:**
  - The company name comes from the email's content and is cleaned up before comparing.
  - A named role must match an open application.
  - With no role named, it only matches if the company has exactly one open application.
  - Anything else goes to the Review List.
  - A duplicate confirmation on an open application is ignored, and one matching only a Rejected application creates a new application.
- **Review List:** Unclear Emails, emails matching several applications, and interview, information request, rejection or offer emails with no match. Actions are attach, create application and dismiss.
- **Information Requests:** the status doesn't change. Instead, the application gets a Needs Reply marker until you clear it. If the application is Rejected or at Offer, the email goes to the Review List.
- **Change Log:** a permanent entry for every change, with old and new values, when, who, and the email that caused it. No undo button.
- **Stored email data:** subject, sender, date, classification and Gmail link, but no bodies.
- **First run:** goes back 90 days, oldest first, with an option to go further. A processed email is never reclassified, and only counts as processed once its result is saved.
- **Sync:**
  - Runs daily at 8:00 AM with catch-up, with a warning after 48 hours without a successful sync.
  - Can also be started with a `sync` command or a Sync now button that warns it can take minutes and shows progress.
  - Only one sync can run at a time, however it was started, and a crashed sync can't block later ones.
  - It runs as a separate process, shows plain-language errors, and has no Cancel button.
- **Web page:** Flask with server-generated pages, started with `serve`. It's local access only, rejects changes from other sites, and always shows email text as plain text.
- **Views:**
  - Applications, with a "Needs your reply" section pinned on top, oldest first
  - Application detail, with the timeline, Gmail links and an edit form
  - Review List, with information requests at the top
  - Discarded emails, with a button to send one to the Review List
- **Top bar:** every page shows the number of emails needing a reply, including those on the Review List.
- **Removing an application:** hides it and can be undone.
- **Left out for now:** charts, stats, exports, deadline detection, desktop notifications and Gmail labels.
