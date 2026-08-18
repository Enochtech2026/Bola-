from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from functools import wraps
import zipfile, io, csv, json
import os
import sys
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'instance')

# Fix Render's postgres:// URL to postgresql:// (required by SQLAlchemy 1.4+)
def fix_database_url(url):
    if url and url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return url

app = Flask(__name__, static_folder='static')
app.config['SQLALCHEMY_DATABASE_URI'] = fix_database_url(os.environ.get('DATABASE_URL', 'sqlite:///' + os.path.join(DB_PATH, 'books.db')))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
app.config['UPLOAD_FOLDER'] = os.path.join(DB_PATH, 'uploads')
app.config['ALLOWED_EXTENSIONS'] = {'pdf'}
app.config['SECURITY_PASSWORD_SALT'] = os.environ.get('SECURITY_PASSWORD_SALT', 'dev-salt-change-me')
# Optional mail config (SMTP). If not configured, reset link is shown in UI for demo.
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT') or 0)
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    author = db.Column(db.String(120), nullable=False)
    year = db.Column(db.Integer)
    isbn = db.Column(db.String(40))
    description = db.Column(db.Text)
    category = db.Column(db.String(40), default='general')
    filename = db.Column(db.String(300))

    def __repr__(self):
        return f'<Book {self.title} by {self.author}>'


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=True)
    matric_no = db.Column(db.String(80), unique=True, nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=True)
    department = db.Column(db.String(120), nullable=True)
    level = db.Column(db.String(40), nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    password_hash = db.Column(db.String(200), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_reset_token(self):
        s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
        return s.dumps({'user_id': self.id}, salt=app.config['SECURITY_PASSWORD_SALT'])

    @staticmethod
    def verify_reset_token(token, expiration=3600):
        s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
        try:
            data = s.loads(token, salt=app.config['SECURITY_PASSWORD_SALT'], max_age=expiration)
        except (BadSignature, SignatureExpired):
            return None
        user_id = data.get('user_id')
        if not user_id:
            return None
        return User.query.get(user_id)


_tables_created = False

@app.before_request
def ensure_tables_exist():
    global _tables_created
    if not _tables_created:
        db.create_all()
        _tables_created = True


@app.route('/')
def index():
    q = request.args.get('q', '').strip()
    category = request.args.get('category', '')
    query = Book.query
    if q:
        query = query.filter(
            (Book.title.ilike(f'%{q}%')) |
            (Book.author.ilike(f'%{q}%')) |
            (Book.isbn.ilike(f'%{q}%'))
        )
    if category:
        query = query.filter(Book.category == category)
    books = query.order_by(Book.title).all()
    return render_template('index.html', books=books, q=q, category=category)


@app.route('/book/<int:book_id>')
def view_book(book_id):
    book = Book.query.get_or_404(book_id)
    return render_template('view.html', book=book)


@app.route('/add', methods=['GET', 'POST'])
def add_book():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        author = request.form.get('author', '').strip()
        year = request.form.get('year') or None
        isbn = request.form.get('isbn', '').strip()
        description = request.form.get('description', '').strip()
        category = request.form.get('category') or 'general'
        file = request.files.get('file')
        filename = None
        if file and file.filename and allowed_file(file.filename):
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        if not title or not author:
            flash('Title and author are required.', 'danger')
            return redirect(url_for('add_book'))
        try:
            year_val = int(year) if year else None
        except ValueError:
            year_val = None
        book = Book(title=title, author=author, year=year_val, isbn=isbn, description=description, category=category, filename=filename)
        db.session.add(book)
        db.session.commit()
        flash('Book added.', 'success')
        return redirect(url_for('index'))
    return render_template('add_edit.html', action='Add', book=None)


@app.route('/edit/<int:book_id>', methods=['GET', 'POST'])
def edit_book(book_id):
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    book = Book.query.get_or_404(book_id)
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        author = request.form.get('author', '').strip()
        year = request.form.get('year') or None
        isbn = request.form.get('isbn', '').strip()
        description = request.form.get('description', '').strip()
        category = request.form.get('category') or 'general'
        file = request.files.get('file')
        if file and file.filename and allowed_file(file.filename):
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            book.filename = filename
        if not title or not author:
            flash('Title and author are required.', 'danger')
            return redirect(url_for('edit_book', book_id=book.id))
        try:
            year_val = int(year) if year else None
        except ValueError:
            year_val = None
        book.title = title
        book.author = author
        book.year = year_val
        book.isbn = isbn
        book.description = description
        book.category = category
        db.session.commit()
        flash('Book updated.', 'success')
        return redirect(url_for('view_book', book_id=book.id))
    return render_template('add_edit.html', action='Edit', book=book)


@app.route('/delete/<int:book_id>', methods=['POST'])
def delete_book(book_id):
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    book = Book.query.get_or_404(book_id)
    if book.filename:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], book.filename))
        except Exception:
            pass
    db.session.delete(book)
    db.session.commit()
    flash('Book deleted.', 'success')
    return redirect(url_for('index'))


@app.route('/uploads/<path:filename>')
def uploads(filename):
    return send_from_directory(
        app.config['UPLOAD_FOLDER'], 
        filename, 
        as_attachment=True,
        download_name=filename
    )


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip() or None
        matric_no = request.form.get('matric_no', '').strip() or None
        email = request.form.get('email', '').strip() or None
        department = request.form.get('department', '').strip() or None
        level = request.form.get('level', '').strip() or None
        password = request.form.get('password', '')
        if not (matric_no or username) or not password:
            flash('Matric No (or username) and password are required.', 'danger')
            return redirect(url_for('register'))
        # check conflicts
        if matric_no and User.query.filter_by(matric_no=matric_no).first():
            flash('Matric No already registered.', 'danger')
            return redirect(url_for('register'))
        if email and User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return redirect(url_for('register'))
        user = User(username=username, matric_no=matric_no, email=email, department=department, level=level)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash('Account created.', 'success')
        return redirect(url_for('index'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identifier = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = None
        if identifier:
            user = User.query.filter((User.matric_no == identifier) | (User.username == identifier) | (User.email == identifier)).first()
        if user and user.check_password(password):
            login_user(user)
            flash('Logged in.', 'success')
            return redirect(request.args.get('next') or url_for('index'))
        flash('Invalid credentials.', 'danger')
        return redirect(url_for('login'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    logout_user()
    flash('Logged out.', 'success')
    return redirect(url_for('login'))


@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        user = User.query.filter((User.username == identifier) | (User.email == identifier) | (User.matric_no == identifier)).first()
        if not user:
            flash('No account found with that username/email.', 'danger')
            return redirect(url_for('forgot_password'))
        # Generate token and send (or show) reset link
        token = user.get_reset_token()
        reset_url = url_for('reset_with_token', token=token, _external=True)
        # If SMTP configured, try to send. Otherwise show link in flash for demo purposes.
        if app.config.get('MAIL_SERVER'):
            try:
                import smtplib
                from email.message import EmailMessage
                msg = EmailMessage()
                msg['Subject'] = 'BL-Library password reset'
                msg['From'] = app.config.get('MAIL_USERNAME') or 'noreply@example.com'
                msg['To'] = user.email
                msg.set_content(f'Use this link to reset your BL-Library password:\n\n{reset_url}\n\nIf you did not request this, ignore.')
                s = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'])
                s.starttls()
                if app.config.get('MAIL_USERNAME'):
                    s.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
                s.send_message(msg)
                s.quit()
                flash('A password reset link has been sent to your email (check spam).', 'info')
            except Exception as e:
                flash('Failed to send email. Here is the reset link for demo: ' + reset_url, 'warning')
        else:
            flash('Password reset link (demo): ' + reset_url, 'info')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')


@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    # Keep legacy route for session-based resets (rare). Prefer token-based reset.
    flash('Please use the tokenized reset link sent to your email (or request a new one).', 'info')
    return redirect(url_for('forgot_password'))


@app.route('/reset/<token>', methods=['GET', 'POST'])
def reset_with_token(token):
    user = User.verify_reset_token(token)
    if not user:
        flash('Invalid or expired token. Please request a new password reset.', 'danger')
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        password = request.form.get('password', '')
        if not password or len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return redirect(url_for('reset_with_token', token=token))
        user.set_password(password)
        db.session.commit()
        flash('Password updated. You can now log in.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', token=token)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
            flash('Admin access required.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/admin/bulk_upload', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_bulk_upload():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or file.filename == '':
            flash('No file uploaded.', 'danger')
            return redirect(url_for('admin_bulk_upload'))
        fname = file.filename.lower()
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        # CSV processing
        if fname.endswith('.csv'):
            txt = file.stream.read().decode('utf-8')
            reader = csv.DictReader(io.StringIO(txt))
            added = 0
            for row in reader:
                title = row.get('title') or row.get('Title')
                author = row.get('author') or row.get('Author')
                if not title or not author:
                    continue
                year = row.get('year')
                try:
                    year_val = int(year) if year else None
                except Exception:
                    year_val = None
                book = Book(title=title.strip(), author=author.strip(), year=year_val, isbn=row.get('isbn') or '', description=row.get('description') or '', category=row.get('category') or 'general')
                db.session.add(book)
                added += 1
            db.session.commit()
            flash(f'Imported {added} books from CSV.', 'success')
            return redirect(url_for('index'))
        # ZIP processing: extract PDFs and create Book entries with filenames only
        if fname.endswith('.zip'):
            z = zipfile.ZipFile(file.stream)
            added = 0
            for info in z.infolist():
                if info.filename.lower().endswith('.pdf'):
                    data = z.read(info.filename)
                    safe_name = secure_filename(os.path.basename(info.filename))
                    target = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
                    with open(target, 'wb') as out:
                        out.write(data)
                    book = Book(title=safe_name, author='Unknown', filename=safe_name)
                    db.session.add(book)
                    added += 1
            db.session.commit()
            flash(f'Imported {added} PDFs from ZIP.', 'success')
            return redirect(url_for('index'))
        flash('Unsupported file type. Upload a CSV or ZIP.', 'danger')
        return redirect(url_for('admin_bulk_upload'))
    return render_template('admin_bulk.html')


def init_db(seed=False):
    os.makedirs(DB_PATH, exist_ok=True)
    db.create_all()
    if seed:
        seed_data()


def seed_data():
    if Book.query.first():
        return
    sample = [
        Book(title='The Pragmatic Programmer', author='Andrew Hunt', year=1999, isbn='9780201616224', description='Classic software engineering book.'),
        Book(title='Clean Code', author='Robert C. Martin', year=2008, isbn='9780132350884', description='Guidelines for writing clean code.'),
        Book(title='Introduction to Algorithms', author='Cormen, Leiserson, Rivest, Stein', year=2009, isbn='9780262033848', description='Comprehensive algorithms textbook.'),
    ]
    db.session.bulk_save_objects(sample)
    db.session.commit()
    # create a test student user
    if not User.query.filter_by(username='student').first():
        u = User(username='student', matric_no='STU001', email='student@example.com', department='Computer Science', level='200')
        u.set_password('password')
        db.session.add(u)
        db.session.commit()
    # create an admin user for bulk operations
    if not User.query.filter_by(username='admin').first():
        a = User(username='admin', matric_no='ADMIN001', email='admin@example.com', department='Library', level='--', is_admin=True)
        a.set_password('adminpass')
        db.session.add(a)
        db.session.commit()


@app.route('/api/books', methods=['GET'])
def api_get_books():
    books = Book.query.order_by(Book.title).all()
    out = []
    for b in books:
        out.append({'id': b.id, 'title': b.title, 'author': b.author, 'year': b.year, 'isbn': b.isbn, 'description': b.description, 'category': b.category, 'filename': b.filename})
    return jsonify(out)


@app.route('/api/books', methods=['POST'])
@login_required
def api_add_book():
    data = request.get_json() or {}
    title = data.get('title')
    author = data.get('author')
    if not title or not author:
        return jsonify({'error': 'title and author required'}), 400
    book = Book(title=title, author=author, year=data.get('year'), isbn=data.get('isbn'), description=data.get('description'), category=data.get('category') or 'general')
    db.session.add(book)
    db.session.commit()
    return jsonify({'ok': True, 'id': book.id}), 201


if __name__ == '__main__':
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == 'initdb':
            init_db(seed=False)
            print('Initialized database (no seed).')
            sys.exit(0)
        if cmd == 'seed':
            init_db(seed=True)
            print('Initialized database with seed data.')
            sys.exit(0)
    app.run(debug=True)
