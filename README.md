# Humar# 🎓 AI Lecture Library

A simple web app where a teacher uploads lectures (videos, PDFs, PowerPoints, Word files, notes) and students open **one link** to search, filter, watch and download them. It works on Android phones and computers.

Built with **Python + Streamlit**, with **Supabase** for the database and file storage.

---

## 1. How it works (architecture in simple English)

```
 Students / Admin (phone or computer browser)
            │
            ▼
   Streamlit app  (app.py)            ← hosted free on Streamlit Community Cloud
      │              │
      │              └── Supabase Storage (private bucket)   ← your video / PDF / PPT files
      ▼
   Supabase Database (PostgreSQL)                            ← title, course, semester, teacher,
                                                               visibility, and which files belong to it
```

* **Streamlit app** – the website. It only contains code. It has no memory of its own.
* **Supabase Database** – stores the lecture information (title, course, semester, …) and the list of files.
* **Supabase Storage** – stores the real files. The bucket is **private**: students never get a permanent file address. The app creates a temporary link (valid 1 hour) only after checking that the student is allowed to open the lecture.
* **Secrets** – passwords and keys live in Streamlit's Secrets box (or a local `secrets.toml` that is never uploaded to GitHub), never in the code.

### Why not SQLite and local folders?
Streamlit Community Cloud **erases everything saved on its disk whenever the app restarts or sleeps**. A SQLite file or an `uploads/` folder would lose all your lectures. Because the database and files live in Supabase (outside the app), **your lectures survive restarts, sleeping and redeploys.**

### Who can see what

| Visitor | Public lectures | Restricted lectures | Upload / edit / delete |
|---|---|---|---|
| Student with the link | ✅ | ❌ (hidden) | ❌ |
| Student who types the **student access code** | ✅ | ✅ | ❌ |
| Admin (logged in) | ✅ | ✅ | ✅ |

Each lecture also has a **"Students may download files"** checkbox.

---

## 2. Storage solution and its limits (please read)

| Item | What to expect (Supabase free plan – check [supabase.com/pricing](https://supabase.com/pricing) for current numbers) |
|---|---|
| Total file storage | About 1 GB |
| Maximum size of **one** file | About **50 MB** |
| Database size | About 500 MB (plenty for lecture text) |
| Inactivity | Free projects can be **paused after about a week with no activity**. Open the app or the Supabase dashboard now and then, and click "Restore" if it is paused. Your data is kept. |
| App sleeping | Streamlit Community Cloud puts apps to sleep when nobody visits. The first visitor clicks "Wake up" and waits a minute. No data is lost. |

**What this means for videos**

* A 50 MB video is only a few minutes of good-quality lecture. **Full-length lecture videos are usually much bigger.**
* For long videos use the **"paste a link"** option in the upload form: upload the video to **YouTube (set to *Unlisted*)** or **Google Drive (share as "Anyone with the link")**, then paste the link. The app plays YouTube links and Google Drive links on the lecture page. This is free, and large videos stream smoothly.
* If you want to store big videos in the app itself, upgrade Supabase (Pro plan raises the limits) and set `MAX_UPLOAD_MB` in your secrets to a larger number. Streamlit's upload box is capped by `maxUploadSize` in `.streamlit/config.toml` (set to 200 MB; raise it if needed). Uploads of very large files through a Streamlit page can be slow or time out, so a link is the more reliable choice.
* Limits of "download disabled": the app hides the download buttons, but a technically skilled student could still save a video that is streaming in their browser. It stops casual downloading, not determined copying. Truly confidential material should not be uploaded to a public service.

---

## 3. Project files

| File | Purpose |
|---|---|
| `app.py` | The whole Streamlit application |
| `requirements.txt` | Python libraries to install |
| `schema.sql` | Creates the database tables and the storage bucket (run once) |
| `.streamlit/config.toml` | Streamlit settings (upload size, theme) |
| `.streamlit/secrets.toml.example` | Template for your secrets (copy it, don't edit it) |
| `.gitignore` | Stops secrets and junk from being uploaded to GitHub |
| `tests/test_app_logic.py` | Automatic tests |

---

## 4. Step 1 – Create the Supabase project (database + storage)

1. Go to <https://supabase.com> and sign up (free). Click **New project**, choose a name and a strong database password, pick the region closest to your students, and wait until it is ready.
2. In the left menu open **SQL Editor → New query**. Open `schema.sql` from this project, copy everything into the editor and click **Run**. You should see "Success". This creates the tables and a private bucket named `lectures`.
3. Open **Project Settings → API** (or **API Keys**) and copy:
   * **Project URL** → this is `SUPABASE_URL`
   * the **`service_role` key** (the "secret" key, *not* the `anon` public key) → this is `SUPABASE_SERVICE_KEY`

> ⚠️ The `service_role` key has full access to your project. Only ever put it in Streamlit Secrets / `secrets.toml`. Never paste it into `app.py`, GitHub, WhatsApp or screenshots. If it leaks, reset it in the Supabase dashboard.

---

## 5. Step 2 – Run it on your own computer

You need Python 3.10 or newer.

```bash
# 1. Open a terminal inside the project folder, then create a virtual environment
python -m venv venv

# 2. Activate it
#    Windows:
venv\Scripts\activate
#    Mac / Linux:
source venv/bin/activate

# 3. Install the libraries
pip install -r requirements.txt

# 4. Create your secrets file
#    Windows:   copy .streamlit\secrets.toml.example .streamlit\secrets.toml
#    Mac/Linux: cp .streamlit/secrets.toml.example .streamlit/secrets.toml
#    Now open .streamlit/secrets.toml in a text editor and fill in the real values.

# 5. Start the app
streamlit run app.py
```

Your browser opens at <http://localhost:8501>.

### Configuration reference (secrets / environment variables)

| Name | Required | Meaning |
|---|---|---|
| `SUPABASE_URL` | ✅ | Your Supabase project URL |
| `SUPABASE_SERVICE_KEY` | ✅ | The service_role key |
| `ADMIN_PASSWORD` | ✅ | Admin password. Use a long random one (12+ characters) |
| `ADMIN_USERNAME` | optional | Defaults to `admin` |
| `STUDENT_ACCESS_CODE` | for restricted lectures | The code you give your students |
| `APP_URL` | optional | Your public app address; shown as a copy-paste link |
| `MAX_UPLOAD_MB` | optional | Largest single upload, default `50` |

The app reads Streamlit secrets first, then ordinary environment variables with the same names (for example `export ADMIN_PASSWORD=...`).

---

## 6. Step 3 – Test it

### Automatic tests (no internet or Supabase needed)
```bash
python -m unittest discover -s tests -v
```
These check search, filters, file validation, upload with rollback on failure, edit, delete, restricted-lecture rules and that no keys are hard-coded.

### Manual checklist (do this once with the real app)

1. **Setup screen** – start the app with no secrets: it should show a "not configured" message, not crash.
2. **Login** – open **Admin Login**. A wrong password shows an error; 5 wrong attempts lock the form for 1 minute. The correct one opens the Dashboard.
3. **Privacy** – log out. **Dashboard** and **Upload Lecture** must disappear from the menu.
4. **Upload** – upload a small PDF + a short MP4 (under 50 MB) with all details. Try also a `.exe` file and a file over the limit: you should get a clear red message and nothing should be saved.
5. **Link upload** – create a lecture with only a YouTube (unlisted) link.
6. **Student view** – open the app in a private/incognito window: search by title, filter by course/semester/topic, open the lecture, play the video, view and download the PDF.
7. **Restricted** – mark a lecture *Restricted*. In the incognito window it must not appear. Enter the student access code in the sidebar and it appears.
8. **Downloads off** – untick "Students may download files" and check that the Download buttons are gone for students.
9. **Edit / delete** – rename a lecture, remove one file, add another, then delete a lecture. In Supabase → **Storage → lectures** confirm the files are gone too.
10. **Restart test** – on Streamlit Cloud choose **Reboot app**; all lectures must still be there.
11. **Phone** – open the link on an Android phone and repeat steps 6–7.

---

## 7. Step 4 – Put the code on GitHub

1. Create a free account at <https://github.com>.
2. Click **+ → New repository**. Name it `ai-lecture-library`. Choose **Private** (Streamlit Cloud can still deploy private repositories) or Public. **Do not** tick "Add a README". Click **Create repository**.
3. Install Git from <https://git-scm.com/downloads> if you don't have it.
4. In a terminal inside the project folder:

```bash
git init
git add .
git status          # CHECK: .streamlit/secrets.toml must NOT be in the list
git commit -m "First version of AI Lecture Library"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/ai-lecture-library.git
git push -u origin main
```

   GitHub may ask you to sign in. If it asks for a password, use a **Personal Access Token** (GitHub → Settings → Developer settings → Personal access tokens) or sign in through the browser window that opens.

5. Refresh the repository page: you should see `app.py`, `requirements.txt`, `README.md`, etc.

> No Git? On the repository page click **Add file → Upload files**, drag in all files and folders (including `.streamlit/config.toml` and `.streamlit/secrets.toml.example`), and click **Commit changes**. Never upload your real `secrets.toml`.

> If you ever commit a secret by mistake, treat it as leaked: change the password / reset the Supabase key immediately.

---

## 8. Step 5 – Deploy on Streamlit Community Cloud

1. Go to <https://share.streamlit.io> and sign in with GitHub. Allow access to your repository when asked.
2. Click **Create app** → **Deploy a public app from GitHub** (or "Yup, I have an app").
3. Choose your repository, branch `main`, and main file path `app.py`.
4. Optionally choose your own **App URL** (for example `ai-lectures-yourname`). This becomes your link: `https://ai-lectures-yourname.streamlit.app`.
5. Click **Advanced settings**. Pick Python 3.11 or 3.12, and in the **Secrets** box paste (with your real values):

```toml
SUPABASE_URL = "https://YOUR-PROJECT-ID.supabase.co"
SUPABASE_SERVICE_KEY = "your-service-role-key"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "your-long-random-password"
STUDENT_ACCESS_CODE = "your-student-code"
APP_URL = "https://ai-lectures-yourname.streamlit.app"
MAX_UPLOAD_MB = "50"
```

6. Click **Deploy**. Wait a few minutes while it installs the libraries.
7. To change secrets later: open your app → **⋮ / Manage app → Settings → Secrets**, edit, save (the app restarts).

---

## 9. Step 6 – Share the link with students

* The **public URL** is the address in the browser, like `https://ai-lectures-yourname.streamlit.app`. It is also shown on the app's Home page under **"Share this library"** once `APP_URL` is set.
* Send that single link by WhatsApp, email or your university portal.
* To share **one specific lecture**, open it and copy the *"Link to this lecture"* shown at the bottom of the lecture page (`.../?lecture=5`).
* For restricted lectures, also give students the **student access code**. They type it in the sidebar (on a phone, tap the **>** arrow at the top left to open the sidebar).

---

## 10. Everyday use

* **Add a lecture:** Admin Login → Upload Lecture → fill in the details → add files or a link → Save.
* **Fix a mistake:** Dashboard → choose the lecture → change details, remove or add files → Save changes.
* **Delete:** Dashboard → choose the lecture → tick the confirmation → Delete.
* **Backups:** In Supabase you can download your tables as CSV (Table Editor → Export). Keep your original videos on your own computer or drive too.

---

## 11. Troubleshooting

| Problem | Fix |
|---|---|
| "The app is not configured yet" | A required secret is missing. Check the names exactly as in the table above. |
| "Could not connect to the database" | Wrong `SUPABASE_URL`/key, you didn't run `schema.sql`, or the Supabase project is paused (restore it). |
| Upload fails: "Bucket not found" | Run `schema.sql` again, or create a **private** bucket named `lectures` in Supabase → Storage. |
| Upload fails: file too large / "exceeded the maximum allowed size" | Supabase's per-file limit (50 MB on the free plan). Use the YouTube / Google Drive link option. |
| Video does not play | Use MP4 (H.264) or WebM. `.mov`/`.avi`/`.mkv` are not supported by most phone browsers. |
| Download / view links stop working after a while | Links last 1 hour for security. Reload the lecture page. |
| Changes in GitHub don't appear | Streamlit redeploys automatically after a push; or use **Reboot app**. |
| Forgot admin password | Change `ADMIN_PASSWORD` in the Secrets box. |

---

## 12. Security notes

* No passwords or keys are in the source code; they come from secrets / environment variables.
* Admin pages check the login on every run, and all write operations are only reachable from those pages.
* Files are in a private bucket and are opened only through short-lived signed links.
* Both database tables have Row Level Security switched on with no public policies, so the public `anon` key cannot read them.
* Login attempts are slowed down and locked for a minute after 5 failures. This is basic protection, so choose a long password.
* The student access code is one shared code. Change it in Secrets whenever you want to remove access for everyone (for example at the end of a semester).a-sath
