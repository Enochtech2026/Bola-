# E-library (Flask)

Minimal E-library web application built with Flask and SQLite.

Quick start

1. Create a virtual environment and activate it.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

2. Install dependencies

```bash
pip install -r requirements.txt
```

3. Initialize the database (with sample data):

```bash
python app.py seed
```

4. Run the app

```bash
python app.py
```

Open http://127.0.0.1:5000 in your browser.

Files of interest

- `app.py` - main Flask app and models
- `templates/` - Jinja2 templates
- `static/` - css and assets
- `instance/books.db` - SQLite database (created on init)

Additional SPA (HTML/CSS/JS) submission

- A standalone single-page app implementation is included at `spa/`.
- To run the SPA: open [spa/index.html](spa/index.html) in a browser. No server required.
- SPA features: add/edit/delete books, search, import/export JSON, LocalStorage persistence, responsive UI.

Submission checklist

- [ ] Include `spa/` folder when packaging (contains `index.html`, `static/`, `README.md`).
- [ ] Include `app.py` and `requirements.txt` if you need server-backed demo.
- [ ] Provide sample data: run `python app.py seed` for Flask or open SPA to see seeded books.
- [ ] Add a short project report (purpose, tech stack, features, how to run) if required by your instructor.

If you want, I can create a zip of the `spa/` folder and/or the whole project ready for submission.

Auth and uploads

- The Flask app now supports user registration and login (`/register`, `/login`).
- Uploaded PDFs are stored in `instance/uploads/` and served to authenticated users.
- Seed user for testing: username `student` / password `password` (created when running `python app.py seed`).
- Seed user for testing: matric `STU001`, username `student` / password `password` (created when running `python app.py seed`).
- Admin user for bulk operations: username `admin` / password `adminpass` (created when running `python app.py seed`).

Password reset (tokenized)

- Request a password reset at `/forgot_password` by entering your matric number or email. The app will generate a time-limited token and either send an email (if SMTP is configured via environment variables) or show the reset link in the UI for demo purposes.
- The reset link looks like `/reset/<token>` and allows you to set a new password.
