# job-tracker

Tracks your job applications by reading the emails companies send you. It adds new applications, updates their status, and shows you which ones are waiting on a reply.

It runs entirely on your own computer. It has read-only access to your Gmail, a local model reads each email through [Ollama](https://ollama.com), and nothing is sent to a cloud AI service. The database stores each email's subject, sender, date, classification and a Gmail link, but never its body.

## Set up

You need [Python 3.12](https://www.python.org/downloads/), [Git](https://git-scm.com/downloads), [Ollama](https://ollama.com/download), a Gmail account, and about 4 GB of free disk space for the model. On Windows, run these commands in PowerShell from the folder you want the project in.

1. **Download the project and install what it needs.**

   ```powershell
   git clone https://github.com/pak-plau/job-tracker.git
   cd job-tracker
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

   On macOS or Linux, activate it with `source .venv/bin/activate` instead. If PowerShell blocks the script, first run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

2. **Download the model.**

   ```powershell
   ollama pull gemma3:4b
   ```

3. **Connect Google.** A wizard walks you through creating your own private Google Cloud project. On Windows, Git comes with the Bash it needs.

   ```powershell
   & "C:\Program Files\Git\bin\bash.exe" scripts/setup-google.sh
   ```

   On macOS or Linux, run `bash scripts/setup-google.sh`.

4. **Run the first sync from a terminal**, so your browser can open for sign-in. Google will warn that the app isn't verified, because it's your own app. Choose Advanced, continue, and tick the box allowing read-only access.

   ```powershell
   python -m job_tracker sync
   ```

   The first sync reads the last 90 days, oldest first, at about 2-3 seconds per email with a GPU. That can take hours if you get a lot of job email. If it stops, run it again and it carries on where it left off. To go further back, add `--days 365`.

5. **Sync automatically every day** at 8:00 AM, catching up if your computer was off:

   ```powershell
   python -m job_tracker schedule
   ```

   On Windows this adds a Task Scheduler task. On macOS or Linux it prints a line to add to your crontab.

## Use it

Start the page, then open <http://127.0.0.1:8173>. It's only reachable from your own computer.

```powershell
python -m job_tracker serve
```

| Page | What it's for |
|---|---|
| **Applications** | Every application and its status, with anything waiting on your reply at the top |
| **Review List** | Emails the tracker wouldn't decide on. Attach each one to an application, create a new application, or dismiss it |
| **Discarded** | Job alerts and irrelevant emails. If one was filed by mistake, send it to the Review List |
| **Sync** | Sync now, and see progress and the last result |

The model makes mistakes, so check anything important in Gmail. Changes are recorded on each application's timeline, and you can correct them on its edit form.

## Sign in again every 7 days

Your Google app stays in Testing, because publishing it needs a website and privacy policy, so Google ends your sign-in after 7 days. When that happens, the page tells you. To sign in again, activate the virtual environment in the project folder as in step 1, then run:

```powershell
python -m job_tracker sync
```

No email is missed while you're signed out, because the next sync picks up from the last successful one.

## Your data

Credentials, your sign-in and the database are all in the `data/` folder, which git ignores. To start over, delete `data/tracker.db`. To disconnect Google, delete `data/token.json` and remove the app's access in your [Google account](https://myaccount.google.com/connections).

## For developers

```powershell
python -m unittest discover -s tests -t .
```

`GLOSSARY.md` defines the terms used in the code, and `docs/adr/` records major decisions. To compare models on your own emails, label some with `python -m evals.label`, then run `python -m evals.compare`. Those labeled emails stay in `data/`, so don't paste them anywhere.
