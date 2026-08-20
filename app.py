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

# Cloudinary setup (for persistent file storage)
try:
    import cloudinary
    import cloudinary.uploader
    import cloudinary.api
    CLOUDINARY_ENABLED = bool(os.environ.get("CLOUDINARY_CLOUD_NAME"))
    if CLOUDINARY_ENABLED:
        cloudinary.config(
            cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
            api_key=os.environ.get("CLOUDINARY_API_KEY"),
            api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
            secure=True
        )
except ImportError:
    CLOUDINARY_ENABLED = False

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
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or __import__('secrets').token_hex(32)
app.config['UPLOAD_FOLDER'] = os.path.join(DB_PATH, 'uploads')
app.config['ALLOWED_EXTENSIONS'] = {'pdf'}
app.config['SECURITY_PASSWORD_SALT'] = os.environ.get('SECURITY_PASSWORD_SALT', 'bl-library-salt-2024')
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


def upload_file_to_storage(file):
    """Upload file to Cloudinary if enabled, else save locally. Returns filename/URL."""
    if CLOUDINARY_ENABLED:
        result = cloudinary.uploader.upload(
            file,
            resource_type='raw',
            folder='bl_library',
            public_id=secure_filename(file.filename).rsplit('.', 1)[0],
            overwrite=True,
            use_filename=True
        )
        return result.get('secure_url')
    else:
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return filename



def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
            flash('Admin access required.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


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


class Borrow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    book_id = db.Column(db.Integer, db.ForeignKey('book.id'), nullable=False)
    borrowed_at = db.Column(db.DateTime, default=__import__('datetime').datetime.utcnow)
    due_date = db.Column(db.DateTime)
    returned_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User', backref='borrows')
    book = db.relationship('Book', backref='borrows')

    @property
    def is_overdue(self):
        import datetime
        if self.returned_at:
            return False
        if self.due_date and __import__('datetime').datetime.utcnow() > self.due_date:
            return True
        return False


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
        os.makedirs(DB_PATH, exist_ok=True)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        db.create_all()
        # No auto-seeding of books - admin adds books manually
        seed_data()  # Only creates admin user now
        # Auto-create admin account if it doesn't exist
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                matric_no='ADMIN001',
                email='admin@example.com',
                department='Library',
                level='--',
                is_admin=True
            )
            admin.set_password('adminpass')
            db.session.add(admin)
            db.session.commit()
        _tables_created = True


@app.route('/')
def index():
    q = request.args.get('q', '').strip()
    category = request.args.get('category', '').strip()
    query = Book.query
    if q:
        query = query.filter(
            (Book.title.ilike(f'%{q}%')) |
            (Book.author.ilike(f'%{q}%')) |
            (Book.description.ilike(f'%{q}%')) |
            (Book.category.ilike(f'%{q}%')) |
            (Book.isbn.ilike(f'%{q}%'))
        )
    if category and category.lower() != 'all':
        query = query.filter(Book.category.ilike(category))
    books = query.order_by(Book.title).all()
    # Get distinct categories for filter buttons
    raw_categories = db.session.query(Book.category).distinct().all()
    categories = sorted(list({c[0] for c in raw_categories if c[0]}))
    total_count = Book.query.count()
    return render_template('index.html', books=books, q=q, category=category, categories=categories, total_count=total_count)


@app.route('/book/<int:book_id>')
def view_book(book_id):
    book = Book.query.get_or_404(book_id)
    active_borrow = None
    if current_user.is_authenticated:
        active_borrow = Borrow.query.filter_by(
            user_id=current_user.id,
            book_id=book_id,
            returned_at=None
        ).first()
    return render_template('view.html', book=book, active_borrow=active_borrow)


@app.route('/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_book():
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
            filename = upload_file_to_storage(file)
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
@login_required
@admin_required
def edit_book(book_id):
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
            book.filename = upload_file_to_storage(file)
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
@login_required
@admin_required
def delete_book(book_id):
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
@login_required
def uploads(filename):
    # Option 2: Student must have an active borrow to download
    # Find the book by filename
    book = Book.query.filter_by(filename=filename).first()
    if book:
        active_borrow = Borrow.query.filter_by(
            user_id=current_user.id,
            book_id=book.id,
            returned_at=None
        ).first()
        if not active_borrow and not current_user.is_admin:
            flash('You must borrow this book before you can download it.', 'warning')
            return redirect(url_for('view_book', book_id=book.id))
    # If filename is a full Cloudinary URL, redirect directly
    if filename.startswith('http://') or filename.startswith('https://'):
        return redirect(filename)
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.isfile(upload_path):
        flash('File not found. The admin may not have uploaded this PDF yet.', 'warning')
        return redirect(url_for('index'))
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
        # Accept field named 'username' or 'identifier' from the form
        identifier = (request.form.get('username') or request.form.get('identifier') or '').strip()
        password = request.form.get('password', '')
        user = None
        if identifier:
            user = User.query.filter(
                (User.matric_no == identifier) |
                (User.username == identifier) |
                (User.email == identifier)
            ).first()
        if user and user.check_password(password):
            login_user(user)
            flash('Logged in successfully.', 'success')
            return redirect(request.args.get('next') or url_for('index'))
        flash('Invalid credentials. Please check your Matric No, username, or email and try again.', 'danger')
        return redirect(url_for('login'))
    return render_template('login.html')


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated and current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        identifier = (request.form.get('username') or '').strip()
        password = request.form.get('password', '')
        user = None
        if identifier:
            user = User.query.filter(
                (User.username == identifier) |
                (User.email == identifier) |
                (User.matric_no == identifier)
            ).first()
        if user and user.is_admin and user.check_password(password):
            login_user(user)
            flash('Welcome, Admin!', 'success')
            return redirect(url_for('admin_dashboard'))
        flash('Invalid admin credentials.', 'danger')
        return redirect(url_for('admin_login'))
    return render_template('admin_login.html')


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
        return redirect(url_for('forgot_password'))
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
    # No sample books - admin will add books manually
    sample = []
    for s in sample:
        existing = Book.query.filter_by(title=s.title).first()
        if existing:
            if s.filename and not existing.filename:
                existing.filename = s.filename
            if s.category and (not existing.category or existing.category.lower() == 'general'):
                existing.category = s.category
            if s.description and not existing.description:
                existing.description = s.description
            if s.author and not existing.author:
                existing.author = s.author
            if s.year and not existing.year:
                existing.year = s.year
            if s.isbn and not existing.isbn:
                existing.isbn = s.isbn
        else:
            db.session.add(s)
    db.session.commit()
    # create admin user
    if not User.query.filter_by(username='admin').first():
        a = User(username='admin', matric_no='ADMIN001', email='admin@example.com', department='Library', level='--', is_admin=True)
        a.set_password('adminpass')
        db.session.add(a)
        db.session.commit()


@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    import datetime
    total_books = Book.query.count()
    total_users = User.query.filter_by(is_admin=False).count()
    total_borrowed = Borrow.query.filter_by(returned_at=None).count()
    total_overdue = Borrow.query.filter(
        Borrow.returned_at == None,
        Borrow.due_date < datetime.datetime.utcnow()
    ).count()
    recent_users = User.query.filter_by(is_admin=False).order_by(User.id.desc()).limit(10).all()
    active_borrows = Borrow.query.filter_by(returned_at=None).order_by(Borrow.borrowed_at.desc()).limit(10).all()
    return render_template('admin_dashboard.html',
        total_books=total_books,
        total_users=total_users,
        total_borrowed=total_borrowed,
        total_overdue=total_overdue,
        recent_users=recent_users,
        active_borrows=active_borrows
    )


@app.route('/admin/borrow/<int:book_id>', methods=['POST'])
@login_required
def borrow_book(book_id):
    import datetime
    book = Book.query.get_or_404(book_id)
    existing = Borrow.query.filter_by(user_id=current_user.id, book_id=book_id, returned_at=None).first()
    if existing:
        flash('You have already borrowed this book.', 'warning')
        return redirect(url_for('view_book', book_id=book_id))
    due = datetime.datetime.utcnow() + datetime.timedelta(days=14)
    borrow = Borrow(user_id=current_user.id, book_id=book_id, due_date=due)
    db.session.add(borrow)
    db.session.commit()
    flash(f'You borrowed "{book.title}". Due in 14 days.', 'success')
    return redirect(url_for('view_book', book_id=book_id))


@app.route('/admin/return/<int:borrow_id>', methods=['POST'])
@login_required
@admin_required
def return_book(borrow_id):
    import datetime
    borrow = Borrow.query.get_or_404(borrow_id)
    borrow.returned_at = datetime.datetime.utcnow()
    db.session.commit()
    flash('Book marked as returned.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/my/return/<int:borrow_id>', methods=['POST'])
@login_required
def student_return_book(borrow_id):
    import datetime
    borrow = Borrow.query.get_or_404(borrow_id)
    if borrow.user_id != current_user.id:
        flash('You cannot return a book you did not borrow.', 'danger')
        return redirect(url_for('my_books'))
    borrow.returned_at = datetime.datetime.utcnow()
    db.session.commit()
    flash(f'"{borrow.book.title}" has been returned successfully.', 'success')
    return redirect(url_for('my_books'))


@app.route('/my/books')
@login_required
def my_books():
    import datetime
    active = Borrow.query.filter_by(user_id=current_user.id, returned_at=None).order_by(Borrow.borrowed_at.desc()).all()
    history = Borrow.query.filter(Borrow.user_id == current_user.id, Borrow.returned_at != None).order_by(Borrow.returned_at.desc()).all()
    return render_template('my_books.html', active=active, history=history, now=datetime.datetime.utcnow())


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
            with app.app_context():
                init_db(seed=False)
            print('Initialized database (no seed).')
            sys.exit(0)
        if cmd == 'seed':
            with app.app_context():
                init_db(seed=True)
            print('Initialized database with seed data.')
            sys.exit(0)
    app.run(debug=True)
