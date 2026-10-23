"""
AI Lecture Library - Online Lecture Management System
=====================================================
A beginner-friendly Streamlit app to upload, organise and share university lectures.

Where things are stored (so nothing is lost when the app restarts):
  * Lecture information  -> Supabase database (PostgreSQL)
  * Videos / PDFs / PPTs -> Supabase Storage (private bucket)

All passwords and keys are read from Streamlit secrets or environment variables.
Nothing secret is written in this file.
"""

import hmac
import html
import os
import re
import time
import uuid
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components

# --------------------------------------------------------------------------
# 1. Settings
# --------------------------------------------------------------------------
BUCKET = "lectures"  # name of the private storage bucket (created by schema.sql)

VIDEO_EXTENSIONS = {"mp4", "webm", "m4v"}
DOCUMENT_EXTENSIONS = {"pdf", "ppt", "pptx", "doc", "docx", "txt", "md"}
ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS | DOCUMENT_EXTENSIONS

MIME_TYPES = {
    "mp4": "video/mp4",
    "webm": "video/webm",
    "m4v": "video/mp4",
    "pdf": "application/pdf",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
}

SEMESTERS = [str(i) for i in range(1, 13)]
VISIBILITY_LABELS = {
    "public": "Public - anyone with the link can open it",
    "restricted": "Restricted - only students who enter the access code",
}
NEW_OPTION = "➕ Add new..."
ALL = "All"

SIGNED_URL_SECONDS = 3600  # private file links stop working after 1 hour
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCK_SECONDS = 60

PUBLIC_PAGES = ["Home", "All Lectures", "Lecture Details", "Admin Login"]
ADMIN_PAGES = ["Home", "All Lectures", "Lecture Details", "Dashboard", "Upload Lecture"]


class UploadError(Exception):
    """Raised when a file or link cannot be saved. The message is shown to the admin."""


# --------------------------------------------------------------------------
# 2. Small helper functions (no Streamlit screens here)
# --------------------------------------------------------------------------
def get_secret(name, default=None):
    """Read a secret from Streamlit secrets first, then from environment variables."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass  # no secrets file (e.g. running with environment variables only)
    return os.environ.get(name, default)


def get_max_upload_mb():
    try:
        return max(1, int(get_secret("MAX_UPLOAD_MB", "50")))
    except ValueError:
        return 50


def missing_config():
    """Return the names of required settings that have not been provided."""
    required = ["SUPABASE_URL", "SUPABASE_SERVICE_KEY", "ADMIN_PASSWORD"]
    return [name for name in required if not get_secret(name)]


def file_ext(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def safe_filename(filename):
    """Storage keys must be simple ASCII text, so replace anything unusual."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
    return name[-80:] or "file"


def human_size(num_bytes):
    if not num_bytes:
        return "size unknown"
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def is_valid_url(url):
    return bool(re.match(r"^https?://[^\s]+$", url.strip()))


def friendly_error(error):
    text = str(error).strip() or error.__class__.__name__
    return text[:300]


def unique_values(lectures, field):
    """Sorted unique non-empty values of one field (used for filter drop-downs)."""
    values = {(lec.get(field) or "").strip() for lec in lectures}
    values.discard("")
    return sorted(values, key=lambda v: (not v.isdigit(), int(v) if v.isdigit() else 0, v.lower()))


def filter_lectures(lectures, query="", department=ALL, course=ALL, semester=ALL, topic=ALL):
    """Return the lectures matching the search text and the chosen filters."""
    words = query.lower().split()
    result = []
    for lec in lectures:
        if department != ALL and lec.get("department") != department:
            continue
        if course != ALL and lec.get("course") != course:
            continue
        if semester != ALL and lec.get("semester") != semester:
            continue
        if topic != ALL and lec.get("topic") != topic:
            continue
        searchable = " ".join(
            str(lec.get(field) or "")
            for field in ("title", "course", "department", "topic", "teacher", "description")
        ).lower()
        if all(word in searchable for word in words):
            result.append(lec)
    return result


def validate_fields(fields):
    """Check the lecture form. Returns a list of error messages (empty = OK)."""
    errors = []
    labels = {
        "title": "Lecture title",
        "department": "Department",
        "course": "Course / subject",
        "teacher": "Teacher name",
    }
    for key, label in labels.items():
        if not fields.get(key, "").strip():
            errors.append(f"{label} is required.")
    if not fields.get("semester"):
        errors.append("Semester is required.")
    return errors


def validate_uploads(files):
    """Check file types and sizes before anything is uploaded."""
    errors = []
    limit_mb = get_max_upload_mb()
    for f in files or []:
        ext = file_ext(f.name)
        if ext not in ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
            errors.append(f"'{f.name}' is not a supported file type. Allowed types: {allowed}.")
        elif f.size > limit_mb * 1024 * 1024:
            errors.append(
                f"'{f.name}' is {human_size(f.size)}, which is over the {limit_mb} MB limit. "
                "For bigger videos, upload to YouTube (unlisted) or Google Drive and paste the link."
            )
        elif f.size == 0:
            errors.append(f"'{f.name}' is empty.")
    return errors


def validate_link(link):
    link = (link or "").strip()
    if link and not is_valid_url(link):
        return ["The video link must start with http:// or https://"]
    return []


# --------------------------------------------------------------------------
# 3. Session helpers (login state)
# --------------------------------------------------------------------------
def is_admin():
    return bool(st.session_state.get("is_admin", False))


def student_has_access():
    return bool(st.session_state.get("student_ok", False))


def can_view(lecture):
    """Public lectures: everyone. Restricted: admin or students who entered the code."""
    if lecture.get("visibility", "public") == "public":
        return True
    return is_admin() or student_has_access()


def codes_match(entered, expected):
    """Compare secrets in a way that does not leak information through timing."""
    return hmac.compare_digest(entered.encode("utf-8"), expected.encode("utf-8"))


# --------------------------------------------------------------------------
# 4. Database and storage (Supabase)
# --------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_client():
    from supabase import create_client  # imported here so errors show a friendly message

    return create_client(get_secret("SUPABASE_URL"), get_secret("SUPABASE_SERVICE_KEY"))


@st.cache_data(ttl=30, show_spinner=False)
def load_lectures():
    """All lectures with their files, newest first."""
    response = (
        get_client()
        .table("lectures")
        .select("*, lecture_files(*)")
        .order("created_at", desc=True)
        .execute()
    )
    return response.data or []


def refresh_lectures():
    load_lectures.clear()


@st.cache_data(ttl=3000, show_spinner=False)
def _signed_url(path, download_name=""):
    """Create a temporary link to a private file (cached so videos do not reload)."""
    storage = get_client().storage.from_(BUCKET)
    if download_name:
        result = storage.create_signed_url(path, SIGNED_URL_SECONDS, {"download": download_name})
    else:
        result = storage.create_signed_url(path, SIGNED_URL_SECONDS)
    url = result.get("signedURL") or result.get("signedUrl")
    if not url:
        raise RuntimeError("Storage did not return a link.")
    if not url.startswith("http"):
        url = get_secret("SUPABASE_URL").rstrip("/") + "/storage/v1" + url
    return url


def get_file_url(path, download_name=""):
    """Return a temporary link or None if it could not be created."""
    try:
        return _signed_url(path, download_name)
    except Exception:
        if download_name:  # some library versions do not support the download option
            try:
                return _signed_url(path)
            except Exception:
                return None
        return None


def remove_storage_paths(paths):
    """Delete files from storage. Raises an exception if it fails."""
    paths = [p for p in paths if p]
    if paths:
        get_client().storage.from_(BUCKET).remove(paths)


def add_files_to_lecture(lecture_id, files, link):
    """
    Upload files (and/or save an external link) for one lecture.
    If anything fails, files uploaded in this call are removed and UploadError is raised,
    so you never end up with half-saved lectures.
    """
    client = get_client()
    uploaded_paths = []
    rows = []
    current = ""
    try:
        for f in files or []:
            current = f.name
            ext = file_ext(f.name)
            path = f"{lecture_id}/{uuid.uuid4().hex[:8]}_{safe_filename(f.name)}"
            mime = MIME_TYPES.get(ext, "application/octet-stream")
            client.storage.from_(BUCKET).upload(path, f.getvalue(), {"content-type": mime})
            uploaded_paths.append(path)
            rows.append(
                {
                    "lecture_id": lecture_id,
                    "kind": "video" if ext in VIDEO_EXTENSIONS else "document",
                    "file_name": f.name,
                    "storage_path": path,
                    "file_size": f.size,
                    "content_type": mime,
                }
            )
        link = (link or "").strip()
        if link:
            current = "video link"
            rows.append(
                {
                    "lecture_id": lecture_id,
                    "kind": "link",
                    "file_name": "External video link",
                    "external_url": link,
                }
            )
        if rows:
            client.table("lecture_files").insert(rows).execute()
    except Exception as error:
        try:
            remove_storage_paths(uploaded_paths)
        except Exception:
            pass
        raise UploadError(f"Could not save '{current}': {friendly_error(error)}") from error
    return rows


def lecture_payload(fields):
    return {
        "title": fields["title"].strip(),
        "department": fields["department"].strip(),
        "course": fields["course"].strip(),
        "semester": fields["semester"],
        "topic": fields.get("topic", "").strip(),
        "teacher": fields["teacher"].strip(),
        "description": fields.get("description", "").strip(),
        "notes": fields.get("notes", "").strip(),
        "visibility": fields["visibility"],
        "allow_download": bool(fields["allow_download"]),
    }


def create_lecture(fields, files, link):
    """Create a lecture row and attach its files. Returns the new lecture id."""
    client = get_client()
    response = client.table("lectures").insert(lecture_payload(fields)).execute()
    lecture_id = response.data[0]["id"]
    try:
        add_files_to_lecture(lecture_id, files, link)
    except UploadError:
        client.table("lectures").delete().eq("id", lecture_id).execute()  # roll back
        raise
    refresh_lectures()
    return lecture_id


def update_lecture(lecture, fields, new_files, new_link, remove_file_ids):
    """Save edits: add new files first (so a failure changes nothing), then update, then remove."""
    client = get_client()
    add_files_to_lecture(lecture["id"], new_files, new_link)
    payload = lecture_payload(fields)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    client.table("lectures").update(payload).eq("id", lecture["id"]).execute()
    if remove_file_ids:
        doomed = [f for f in lecture.get("lecture_files", []) if f["id"] in remove_file_ids]
        remove_storage_paths([f.get("storage_path") for f in doomed])
        client.table("lecture_files").delete().in_("id", list(remove_file_ids)).execute()
    refresh_lectures()


def delete_lecture(lecture):
    """Delete the stored files first, then the database rows."""
    paths = [f.get("storage_path") for f in lecture.get("lecture_files", [])]
    remove_storage_paths(paths)
    get_client().table("lectures").delete().eq("id", lecture["id"]).execute()
    refresh_lectures()


# --------------------------------------------------------------------------
# 5. Navigation helpers
# --------------------------------------------------------------------------
def go(page):
    """Switch page from inside a page body (applied at the top of the next run)."""
    st.session_state["pending_nav"] = page
    st.rerun()


def open_lecture(lecture_id):
    """Button callback: open the details page for one lecture."""
    st.session_state["selected_lecture"] = lecture_id
    st.session_state["nav"] = "Lecture Details"
    st.query_params["lecture"] = str(lecture_id)


def logout():
    """Button callback."""
    st.session_state["is_admin"] = False
    st.session_state["nav"] = "Home"


def flash(kind, message):
    st.session_state["flash"] = (kind, message)


def show_flash():
    item = st.session_state.pop("flash", None)
    if item:
        kind, message = item
        (st.success if kind == "success" else st.error)(message)


def require_admin():
    if not is_admin():
        st.error("This page is for the admin only. Please log in first.")
        return False
    return True


# --------------------------------------------------------------------------
# 6. Reusable screen pieces
# --------------------------------------------------------------------------
def inject_css():
    st.markdown(
        """
        <style>
        .block-container { padding-top: 2rem; max-width: 900px; }
        @media (max-width: 640px) {
            .block-container { padding-left: 1rem; padding-right: 1rem; }
        }
        iframe { max-width: 100%; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def student_unlock_form(key):
    """Small form where students type the access code for restricted lectures."""
    expected = get_secret("STUDENT_ACCESS_CODE")
    if not expected:
        st.caption("Restricted lectures are not available (no student access code is set).")
        return
    with st.form(f"unlock_{key}"):
        code = st.text_input("Student access code", type="password")
        submitted = st.form_submit_button("Unlock")
    if submitted:
        if code and codes_match(code.strip(), expected):
            st.session_state["student_ok"] = True
            st.rerun()
        else:
            st.error("Wrong access code. Please check it and try again.")


def lecture_card(lecture, where):
    """A short summary box with an 'Open lecture' button."""
    with st.container(border=True):
        lock = "🔒 " if lecture.get("visibility") == "restricted" else ""
        st.markdown(f"#### {lock}{lecture['title']}")
        st.caption(
            f"{lecture['course']} · {lecture['department']} · "
            f"Semester {lecture['semester']} · {lecture['teacher']}"
        )
        if lecture.get("topic"):
            st.write(f"**Topic:** {lecture['topic']}")
        count = len(lecture.get("lecture_files", []))
        st.caption(f"{count} file(s)" + (" + notes" if lecture.get("notes") else ""))
        st.button(
            "Open lecture",
            key=f"open_{where}_{lecture['id']}",
            on_click=open_lecture,
            args=(lecture["id"],),
        )


def render_external_link(url):
    """Show a YouTube / Google Drive / direct video link nicely, plus an 'open' button."""
    lower = url.lower()
    drive = re.search(r"drive\.google\.com/file/d/([\w-]+)", url)
    if drive:
        embed = f"https://drive.google.com/file/d/{drive.group(1)}/preview"
        components.html(
            f'<iframe src="{html.escape(embed)}" width="100%" height="400" '
            'style="border:0" allow="autoplay" allowfullscreen></iframe>',
            height=410,
        )
    elif "youtube.com" in lower or "youtu.be" in lower or lower.endswith((".mp4", ".webm")):
        st.video(url)
    st.link_button("Open video link", url)


def render_video(file_row):
    st.markdown(f"**🎬 {file_row['file_name']}** · {human_size(file_row.get('file_size'))}")
    url = get_file_url(file_row["storage_path"])
    if url:
        st.video(url)
    else:
        st.warning("The video link could not be created. Please try again in a moment.")


def render_document(file_row, lecture):
    name = file_row["file_name"]
    st.markdown(f"**📄 {name}** · {human_size(file_row.get('file_size'))}")
    view_url = get_file_url(file_row["storage_path"])
    if not view_url:
        st.warning("The file link could not be created. Please try again in a moment.")
        return
    may_download = bool(lecture.get("allow_download", True)) or is_admin()
    cols = st.columns(2)
    if file_ext(name) == "pdf":
        cols[0].link_button("👁 View", view_url)
        if may_download:
            cols[1].link_button("⬇ Download", get_file_url(file_row["storage_path"], name) or view_url)
        with st.expander("Preview on this page"):
            components.html(
                f'<iframe src="{html.escape(view_url)}" width="100%" height="600" '
                'style="border:0"></iframe>',
                height=610,
            )
    elif may_download:
        cols[0].link_button("⬇ Download", get_file_url(file_row["storage_path"], name) or view_url)
    else:
        st.caption("The teacher has turned off downloads for this file.")


def pick_or_new(label, options, key, current=""):
    """A drop-down of existing values with an 'Add new...' choice (avoids typos in filters)."""
    choices = list(options)
    if current and current not in choices:
        choices.append(current)
    choices.append(NEW_OPTION)
    index = choices.index(current) if current in choices else 0
    choice = st.selectbox(label, choices, index=index, key=f"{key}_select")
    if choice == NEW_OPTION:
        return st.text_input(f"New {label.lower().rstrip(' *')}", key=f"{key}_new").strip()
    return choice


def lecture_fields(lectures, key, defaults=None):
    """The form fields shared by 'Upload Lecture' and 'Edit'. Returns a dict of the values."""
    d = defaults or {}
    fields = {}
    fields["title"] = st.text_input("Lecture title *", value=d.get("title", ""), key=f"{key}_title")
    col1, col2 = st.columns(2)
    with col1:
        fields["department"] = pick_or_new(
            "Department *", unique_values(lectures, "department"), f"{key}_dept", d.get("department", "")
        )
    with col2:
        fields["course"] = pick_or_new(
            "Course / subject *", unique_values(lectures, "course"), f"{key}_course", d.get("course", "")
        )
    col3, col4 = st.columns(2)
    with col3:
        sem_index = SEMESTERS.index(d["semester"]) if d.get("semester") in SEMESTERS else 0
        fields["semester"] = st.selectbox("Semester *", SEMESTERS, index=sem_index, key=f"{key}_sem")
    with col4:
        fields["topic"] = st.text_input("Topic", value=d.get("topic", ""), key=f"{key}_topic")
    fields["teacher"] = st.text_input("Teacher name *", value=d.get("teacher", ""), key=f"{key}_teacher")
    fields["description"] = st.text_area(
        "Description", value=d.get("description", ""), key=f"{key}_desc", height=100
    )
    fields["notes"] = st.text_area(
        "Class notes (optional, you can use Markdown)", value=d.get("notes", ""), key=f"{key}_notes", height=150
    )
    vis_keys = list(VISIBILITY_LABELS)
    vis_index = vis_keys.index(d["visibility"]) if d.get("visibility") in vis_keys else 0
    fields["visibility"] = st.radio(
        "Who can open this lecture?",
        vis_keys,
        index=vis_index,
        format_func=lambda k: VISIBILITY_LABELS[k],
        key=f"{key}_vis",
    )
    fields["allow_download"] = st.checkbox(
        "Students may download files", value=d.get("allow_download", True), key=f"{key}_dl"
    )
    return fields


# --------------------------------------------------------------------------
# 7. Pages
# --------------------------------------------------------------------------
def page_home(lectures):
    st.title("🎓 AI Lecture Library")
    st.write("All university lectures in one place: videos, slides, documents and notes.")

    visible = [lec for lec in lectures if can_view(lec)]
    col1, col2, col3 = st.columns(3)
    col1.metric("Lectures", len(visible))
    col2.metric("Courses", len(unique_values(visible, "course")))
    col3.metric("Teachers", len(unique_values(visible, "teacher")))

    st.subheader("Latest lectures")
    if not visible:
        st.info("No lectures are available yet.")
    for lec in visible[:5]:
        lecture_card(lec, "home")

    app_url = (get_secret("APP_URL") or "").rstrip("/")
    with st.expander("📎 Share this library with students"):
        if app_url:
            st.write("Send this single link to your students:")
            st.code(app_url, language=None)
        else:
            st.write("Copy the address from your browser's address bar and send it to students.")
            st.caption("Tip: set APP_URL in your secrets to show the link here.")


def page_all_lectures(lectures):
    st.title("All Lectures")
    visible = [lec for lec in lectures if can_view(lec)]

    query = st.text_input("🔍 Search by title, course, topic or teacher", key="search_box")
    col1, col2 = st.columns(2)
    with col1:
        department = st.selectbox("Department", [ALL] + unique_values(visible, "department"), key="f_dept")
        semester = st.selectbox("Semester", [ALL] + unique_values(visible, "semester"), key="f_sem")
    with col2:
        course = st.selectbox("Course", [ALL] + unique_values(visible, "course"), key="f_course")
        topic = st.selectbox("Topic", [ALL] + unique_values(visible, "topic"), key="f_topic")

    results = filter_lectures(visible, query, department, course, semester, topic)
    st.caption(f"{len(results)} lecture(s) found")
    if not results:
        st.info("No lectures match your search. Try removing a filter.")
    for lec in results:
        lecture_card(lec, "list")


def page_details(lectures):
    st.title("Lecture Details")
    lecture_id = st.session_state.get("selected_lecture")
    if lecture_id is None:
        st.info("Pick a lecture from **All Lectures** to see its details here.")
        return
    lecture = next((lec for lec in lectures if lec["id"] == lecture_id), None)
    if lecture is None:
        st.error("This lecture was not found. It may have been deleted.")
        return

    if not can_view(lecture):
        st.warning("🔒 This lecture is restricted. Enter the student access code to open it.")
        student_unlock_form("details")
        return

    lock = "🔒 " if lecture.get("visibility") == "restricted" else ""
    st.header(f"{lock}{lecture['title']}")
    st.caption(
        f"{lecture['course']} · {lecture['department']} · Semester {lecture['semester']} · "
        f"Teacher: {lecture['teacher']}"
    )
    if lecture.get("topic"):
        st.write(f"**Topic:** {lecture['topic']}")
    if lecture.get("description"):
        st.write(lecture["description"])

    files = lecture.get("lecture_files", [])
    videos = [f for f in files if f["kind"] == "video"]
    links = [f for f in files if f["kind"] == "link"]
    documents = [f for f in files if f["kind"] == "document"]

    for f in videos:
        render_video(f)
    for f in links:
        render_external_link(f["external_url"])
    if documents:
        st.subheader("Slides and documents")
        for f in documents:
            render_document(f, lecture)
    if lecture.get("notes"):
        st.subheader("Class notes")
        st.markdown(lecture["notes"])
    if not files and not lecture.get("notes"):
        st.info("No files have been added to this lecture yet.")

    st.divider()
    app_url = (get_secret("APP_URL") or "").rstrip("/")
    if app_url:
        st.caption("Link to this lecture:")
        st.code(f"{app_url}/?lecture={lecture['id']}", language=None)
    else:
        st.caption("Copy this page's address from your browser to share this lecture.")


def page_login():
    st.title("🔐 Admin Login")
    if is_admin():
        st.success("You are logged in.")
        return

    locked_until = st.session_state.get("lock_until", 0)
    if time.time() < locked_until:
        wait = int(locked_until - time.time()) + 1
        st.error(f"Too many wrong attempts. Please wait {wait} seconds and try again.")
        return

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        expected_user = get_secret("ADMIN_USERNAME", "admin")
        expected_pass = get_secret("ADMIN_PASSWORD", "")
        user_ok = codes_match(username.strip(), expected_user)
        pass_ok = codes_match(password, expected_pass)
        if user_ok and pass_ok and expected_pass:
            st.session_state["is_admin"] = True
            st.session_state["failed_logins"] = 0
            go("Dashboard")
        else:
            failures = st.session_state.get("failed_logins", 0) + 1
            st.session_state["failed_logins"] = failures
            time.sleep(1)  # slows down password guessing
            if failures >= MAX_LOGIN_ATTEMPTS:
                st.session_state["lock_until"] = time.time() + LOGIN_LOCK_SECONDS
                st.session_state["failed_logins"] = 0
            st.error("Wrong username or password.")


def page_dashboard(lectures):
    st.title("📊 Admin Dashboard")
    if not require_admin():
        return
    show_flash()

    total_files = sum(len(lec.get("lecture_files", [])) for lec in lectures)
    restricted = sum(1 for lec in lectures if lec.get("visibility") == "restricted")
    col1, col2, col3 = st.columns(3)
    col1.metric("Lectures", len(lectures))
    col2.metric("Restricted", restricted)
    col3.metric("Files", total_files)

    if not lectures:
        st.info("No lectures yet. Use **Upload Lecture** to add the first one.")
        return

    rows = [
        {
            "ID": lec["id"],
            "Title": lec["title"],
            "Department": lec["department"],
            "Course": lec["course"],
            "Sem": lec["semester"],
            "Teacher": lec["teacher"],
            "Access": lec["visibility"],
            "Files": len(lec.get("lecture_files", [])),
            "Added": str(lec.get("created_at", ""))[:10],
        }
        for lec in lectures
    ]
    st.dataframe(rows, hide_index=True)

    st.subheader("Edit or delete a lecture")
    by_id = {lec["id"]: lec for lec in lectures}
    chosen_id = st.selectbox(
        "Choose a lecture",
        list(by_id),
        format_func=lambda i: f"#{i} - {by_id[i]['title']}",
    )
    lecture = by_id[chosen_id]
    edit_round = st.session_state.get("edit_round", 0)
    key = f"edit{chosen_id}_{edit_round}"

    fields = lecture_fields(lectures, key, lecture)

    st.markdown("**Current files**")
    existing = lecture.get("lecture_files", [])
    if not existing:
        st.caption("No files attached.")
    remove_ids = st.multiselect(
        "Tick files to remove",
        [f["id"] for f in existing],
        format_func=lambda i: next(
            (f["file_name"] or f.get("external_url", "") for f in existing if f["id"] == i), str(i)
        ),
        key=f"{key}_remove",
    )
    new_files = st.file_uploader("Add more files", accept_multiple_files=True, key=f"{key}_files")
    new_link = st.text_input("Add a video link (YouTube / Google Drive)", key=f"{key}_link")

    if st.button("💾 Save changes", type="primary", key=f"{key}_save"):
        errors = validate_fields(fields) + validate_uploads(new_files) + validate_link(new_link)
        if errors:
            for message in errors:
                st.error(message)
        else:
            saved = False
            with st.spinner("Saving..."):
                try:
                    update_lecture(lecture, fields, new_files, new_link, set(remove_ids))
                    saved = True
                except UploadError as error:
                    st.error(str(error))
                except Exception as error:
                    st.error(f"Could not save changes: {friendly_error(error)}")
            if saved:
                st.session_state["edit_round"] = edit_round + 1
                flash("success", "Lecture updated.")
                st.rerun()

    st.divider()
    st.markdown("**Delete this lecture**")
    st.caption("This permanently deletes the lecture and all of its uploaded files.")
    confirm = st.checkbox("Yes, I want to delete this lecture permanently", key=f"{key}_confirm")
    if st.button("🗑 Delete lecture", key=f"{key}_delete", disabled=not confirm):
        deleted = False
        try:
            delete_lecture(lecture)
            deleted = True
        except Exception as error:
            st.error(f"Could not delete the lecture: {friendly_error(error)}")
        if deleted:
            if st.session_state.get("selected_lecture") == chosen_id:
                st.session_state["selected_lecture"] = None
            flash("success", "Lecture deleted.")
            st.rerun()


def page_upload(lectures):
    st.title("⬆ Upload Lecture")
    if not require_admin():
        return
    show_flash()

    st.caption(
        f"Allowed files: {', '.join(sorted(ALLOWED_EXTENSIONS))} · "
        f"maximum {get_max_upload_mb()} MB per file."
    )
    round_no = st.session_state.get("upload_round", 0)
    key = f"upload{round_no}"  # a new key after each save gives an empty form

    fields = lecture_fields(lectures, key)

    st.subheader("Files")
    files = st.file_uploader(
        "Videos, PDFs, PowerPoint, Word documents or notes", accept_multiple_files=True, key=f"{key}_files"
    )
    link = st.text_input(
        "Or paste a link for a large video (YouTube unlisted / Google Drive)", key=f"{key}_link"
    )

    if st.button("Save lecture", type="primary", key=f"{key}_save"):
        errors = validate_fields(fields) + validate_uploads(files) + validate_link(link)
        if not files and not link.strip() and not fields["notes"].strip():
            errors.append("Add at least one file, a video link, or some class notes.")
        if errors:
            for message in errors:
                st.error(message)
            return
        created = False
        with st.spinner("Uploading... large files can take a few minutes."):
            try:
                create_lecture(fields, files, link)
                created = True
            except UploadError as error:
                st.error(f"Upload failed. Nothing was saved. {error}")
            except Exception as error:
                st.error(f"Upload failed. Nothing was saved. {friendly_error(error)}")
        if created:
            st.session_state["upload_round"] = round_no + 1
            flash("success", "Lecture saved. You can add another one below.")
            st.rerun()


# --------------------------------------------------------------------------
# 8. Main program
# --------------------------------------------------------------------------
def show_setup_help(missing):
    st.title("🎓 AI Lecture Library")
    st.error("The app is not configured yet. Missing settings: " + ", ".join(missing))
    st.markdown(
        "Add them in **Streamlit Cloud → App → Settings → Secrets**, or for local use in "
        "`.streamlit/secrets.toml`. See `README.md` and `.streamlit/secrets.toml.example`."
    )


def init_state():
    """Run once per browser session. Opens a lecture directly if the link contains ?lecture=ID."""
    if "nav" in st.session_state:
        return
    st.session_state["nav"] = "Home"
    st.session_state["selected_lecture"] = None
    wanted = st.query_params.get("lecture")
    if wanted and str(wanted).isdigit():
        st.session_state["selected_lecture"] = int(wanted)
        st.session_state["nav"] = "Lecture Details"


def main():
    st.set_page_config(page_title="AI Lecture Library", page_icon="🎓", layout="centered")
    inject_css()

    missing = missing_config()
    if missing:
        show_setup_help(missing)
        return

    init_state()
    if "pending_nav" in st.session_state:
        st.session_state["nav"] = st.session_state.pop("pending_nav")

    pages = ADMIN_PAGES if is_admin() else PUBLIC_PAGES
    if st.session_state["nav"] not in pages:
        st.session_state["nav"] = "Home"

    with st.sidebar:
        st.markdown("## 🎓 AI Lecture Library")
        st.radio("Go to", pages, key="nav")
        st.divider()
        if is_admin():
            st.caption("Logged in as admin")
            st.button("Log out", on_click=logout)
        elif student_has_access():
            st.caption("✅ Student access unlocked")
        else:
            with st.expander("🔑 Student access code"):
                student_unlock_form("sidebar")

    try:
        lectures = load_lectures()
    except Exception as error:
        st.error(
            "Could not connect to the database. Check SUPABASE_URL and SUPABASE_SERVICE_KEY, "
            "and make sure you ran schema.sql. Details: " + friendly_error(error)
        )
        return

    page = st.session_state["nav"]
    if page == "Home":
        page_home(lectures)
    elif page == "All Lectures":
        page_all_lectures(lectures)
    elif page == "Lecture Details":
        page_details(lectures)
    elif page == "Admin Login":
        page_login()
    elif page == "Dashboard":
        page_dashboard(lectures)
    elif page == "Upload Lecture":
        page_upload(lectures)


if __name__ == "__main__":
    main()
