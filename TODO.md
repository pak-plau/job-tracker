# To do

Last updated 2026-11-05. Run commands in PowerShell from the `job-tracker` folder.

To pick this back up with Claude Code, open it in this folder and say "continue from TODO.md".

## Your next steps

- [ ] **Run the first real sync.** It reads 1,161 emails from the last 90 days, which should take about 50 minutes. If it stops, run it again and it carries on.
  ```powershell
  .venv\Scripts\python.exe -m job_tracker sync
  ```

- [ ] **Look through the page.** Then open <http://127.0.0.1:8173>.
  ```powershell
  .venv\Scripts\python.exe -m job_tracker serve
  ```

- [ ] **Turn on the daily sync.** This adds a Windows Task Scheduler task, and it hasn't been tried on a real computer yet.
  ```powershell
  .venv\Scripts\python.exe -m job_tracker schedule
  ```

- [ ] **Label offers and more interviews,** then compare the models again. The 49 labeled emails include no offers and only 2 interviews.
  ```powershell
  .venv\Scripts\python.exe -m evals.label --days 365 --query "offer letter"
  .venv\Scripts\python.exe -m evals.label --days 365 --query interview
  .venv\Scripts\python.exe -m evals.compare | Select-String 'correct,|^Best'
  ```

  | Key | Use it for |
  |---|---|
  | `c` | The company or job site received or sent your application |
  | `i` | Invitation to a new next step, including availability requests and assessments |
  | `m` | Request for missing information, documents, or to finish a form |
  | `r` | The company isn't moving forward |
  | `o` | Job offer |
  | `u` | About your application, but you can't tell what it means |
  | `j` | Newsletter or alert listing open roles you could apply to |
  | `n` | Anything else, including other newsletters and interview reminders |
  | `s` | Skip, leaving it out of the test |
  | `q` | Save and quit |

- [ ] **Paste the score lines to Claude.** Never paste email subjects or text, because chat content goes to Anthropic.

## Claude's next steps

- [x] Count how many labeled emails hit the 8,000-character limit: 1 of 49, so the limit stays.
- [x] Pick the default model from the scores: `gemma3:4b`, at 42/49 correct, 2 wrong status and 5 lost. All 5 lost are irrelevant emails filed as `job_alert`, which are discarded either way.
- [x] Stop confirmations being filed as `job_alert`: job site emails saying an application was sent didn't fit the `confirmation` definition, which is now wider.
- [x] Delete `gemma3:1b` and `granite4.2:3b` from Ollama.
- [ ] Delete `qwen3.5:2b` from Ollama and `compare.py` once offers are tested, if `gemma3:4b` still wins.
- [ ] Check with the person that Open in Gmail links land on the right email. They've never been clicked on a real account.

## Still to build

1. [x] Model test: Gmail connection, labeling and comparison scripts
2. [x] Classification and matching rules, with tests that use made-up emails
3. [x] Database and `sync`
4. [x] Web page
5. [x] Setup guide for people cloning the repo

All five steps are committed and pushed on the `model-test` branch, which hasn't been merged to `main`.

## Agreed design

See `GLOSSARY.md` for terms and `docs/adr/0001-local-only.md` for why this runs locally.

- **Setup:** Python and SQLite, with one private copy per person. Credentials, test emails and the database stay in the git-ignored `data/` folder.
- **Gmail:** `gmail.readonly` only, with stored links to emails and no labels. The app stays in Testing, so sign-in expires every 7 days. The page must say so clearly, and sync must resume without missing mail.
- **Model:** Ollama, with temperature 0, thinking off and a fixed JSON format. Categories are confirmation, interview, info_request, rejection, offer, unclear, job_alert and irrelevant. A job_alert email stays out of the Applications table and the Review List, and is listed under Discarded emails instead.
- **Statuses:** Applied, Interviewing, Rejected and Offer. Forward moves are automatic, contradictions of a final status go to the Review List, and repeats are only logged.
- **Matching:**
  - The company name comes from the email's content and is cleaned up before comparing.
  - A named role must match an open application.
  - With no role named, it only matches if the company has exactly one open application.
  - Anything else goes to the Review List.
  - A duplicate confirmation on an open application is ignored, and one matching only a Rejected application creates a new application.
- **Review List:** Unclear Emails, emails matching several applications, and interview, information request, rejection or offer emails with no match. Actions are attach, create application and dismiss.
- **Information Requests:** the status doesn't change. Instead, the application gets a Needs Reply marker until you clear it. You can also set the marker yourself on the edit form, for a request the model missed, and this is logged in the Change Log. If the application is Rejected or at Offer, the email goes to the Review List.
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
  - Discarded emails, including job alerts, with a button to send one to the Review List
- **Top bar:** every page shows the number of emails needing a reply, including those on the Review List.
- **Removing an application:** hides it and can be undone.
- **Left out for now:** charts, stats, exports, deadline detection, desktop notifications and Gmail labels.

## Decisions made while building

The design didn't cover these, so they're open to change.

- **Unknown roles:** an email naming a role fills in an application whose role is still unknown, if exactly one fits. An exact role match comes first.
- **Confirmation after an Offer:** goes to the Review List, since only a Rejected application counts as reapplying.
- **Attaching from the Review List:** applies what the email says, even over a final status, because you chose it. The edit form can undo this by hand.
- **A second information request:** keeps the original waiting time.
- **Removed applications:** are never matched to new email.
- **Dismissed emails:** move to Discarded emails, so a mistaken dismissal can be sent back.
- **Unusable model answers:** go to the Review List as Unclear, because the same email would get the same answer forever and block every later one.
- **Daily schedule:** a task checks hourly and only syncs if none has succeeded since the latest 8:00 AM. This gives catch-up, and retries a failed sync, on any operating system.
