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
        if not Book.query.first():
            seed_data()
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
    return render_template('view.html', book=book)


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
    # If filename is a full Cloudinary URL, redirect directly
    if filename.startswith('http://') or filename.startswith('https://'):
        return redirect(filename)
    # Check static/pdfs first (for seeded books), then uploads folder
    static_pdf = os.path.join(BASE_DIR, 'static', 'pdfs', filename)
    if os.path.isfile(static_pdf):
        return send_from_directory(
            os.path.join(BASE_DIR, 'static', 'pdfs'),
            filename,
            as_attachment=True,
            download_name=filename
        )
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
    sample = [
        # Base Tech Books
        Book(title='Clean Code', author='Robert C. Martin', year=2008, isbn='9780132350884', description='A handbook of agile software craftsmanship with best practices for writing clean, readable, and maintainable code.', category='School', filename='clean_code.pdf'),
        Book(title='The Pragmatic Programmer', author='Andrew Hunt', year=1999, isbn='9780201616224', description='Classic guide to software craftsmanship, personal responsibility, and career development for developers.', category='School', filename='pragmatic_programmer.pdf'),
        Book(title='Introduction to Algorithms', author='Cormen, Leiserson, Rivest, Stein', year=2009, isbn='9780262033848', description='The premier comprehensive textbook on computer algorithms and data structures.', category='School', filename='intro_algorithms.pdf'),
        # School
        Book(title='Introduction to Calculus', author='James Stewart', year=2020, isbn='9781285740621', description='A comprehensive guide to differential and integral calculus for beginners.', category='School', filename='Introduction_to_Calculus.pdf'),
        Book(title='Principles of Economics', author='N. Gregory Mankiw', year=2021, isbn='9780357038314', description='Foundational concepts in micro and macroeconomics.', category='School', filename='Principles_of_Economics.pdf'),
        Book(title='Biology: Life on Earth', author='Teresa Audesirk', year=2019, isbn='9780134611617', description='Exploring the diversity of life from cells to ecosystems.', category='School', filename='Biology_Life_on_Earth.pdf'),
        Book(title='Organic Chemistry', author='David Klein', year=2022, isbn='9781119659594', description='A student-centered approach to organic chemistry reactions.', category='School', filename='Organic_Chemistry.pdf'),
        Book(title='Physics for Scientists', author='Raymond A. Serway', year=2018, isbn='9781337553292', description='Classical and modern physics with engineering applications.', category='School', filename='Physics_for_Scientists.pdf'),
        Book(title='Engineering Mathematics', author='K.A. Stroud', year=2020, isbn='9781352010275', description='Essential mathematics for engineering and science students.', category='School', filename='Engineering_Mathematics.pdf'),
        Book(title='Fundamentals of Accounting', author='Belverd Needles', year=2021, isbn='9781337690843', description='Principles of financial and managerial accounting.', category='School', filename='Fundamentals_of_Accounting.pdf'),
        Book(title='World History: Patterns of Civilization', author='Marvin Perry', year=2019, isbn='9780134085579', description='A survey of world civilizations from ancient to modern times.', category='School', filename='World_History_Patterns_of_Civilization.pdf'),
        Book(title='Introduction to Psychology', author='James Kalat', year=2022, isbn='9780357363195', description='Core concepts in psychology from neuroscience to social behavior.', category='School', filename='Introduction_to_Psychology.pdf'),
        Book(title='College Algebra and Trigonometry', author='Margaret Lial', year=2020, isbn='9780135894293', description='Algebraic functions and trigonometric identities for college students.', category='School', filename='College_Algebra_and_Trigonometry.pdf'),
        Book(title='Environmental Science', author='G. Tyler Miller', year=2021, isbn='9781337569613', description='Understanding environmental issues and sustainable solutions.', category='School', filename='Environmental_Science.pdf'),
        Book(title='Principles of Anatomy and Physiology', author='Gerard Tortora', year=2020, isbn='9781119664543', description='Comprehensive study of human body structure and function.', category='School', filename='Principles_of_Anatomy_and_Physiology.pdf'),
        Book(title='Linear Algebra and Its Applications', author='David Lay', year=2019, isbn='9780134022697', description='Vector spaces, matrices, and linear transformations.', category='School', filename='Linear_Algebra_and_Its_Applications.pdf'),
        Book(title='Introduction to Sociology', author='Anthony Giddens', year=2021, isbn='9780393676853', description='Sociological perspectives on society, culture, and institutions.', category='School', filename='Introduction_to_Sociology.pdf'),
        Book(title='Discrete Mathematics', author='Kenneth Rosen', year=2019, isbn='9780073383095', description='Logic, sets, relations, graphs, and combinatorics.', category='School', filename='Discrete_Mathematics.pdf'),
        Book(title='Principles of Marketing', author='Philip Kotler', year=2022, isbn='9780135768617', description='Modern marketing strategies and consumer behavior.', category='School', filename='Principles_of_Marketing.pdf'),
        Book(title='Data Structures and Algorithms', author='Michael Goodrich', year=2020, isbn='9781118771334', description='Fundamental data structures and algorithm design techniques.', category='School', filename='Data_Structures_and_Algorithms.pdf'),
        Book(title='Introduction to Political Science', author='Robert Garner', year=2018, isbn='9780198704386', description='Political systems, ideologies, and governance structures.', category='School', filename='Introduction_to_Political_Science.pdf'),
        Book(title='Statistics for Business and Economics', author='James McClave', year=2021, isbn='9780134505824', description='Statistical methods applied to business decision making.', category='School', filename='Statistics_for_Business_and_Economics.pdf'),
        Book(title='Mechanical Engineering Principles', author='John Bird', year=2020, isbn='9780367421861', description='Core mechanical engineering concepts and applications.', category='School', filename='Mechanical_Engineering_Principles.pdf'),
        # Fiction
        Book(title='The Last Horizon', author='Sarah Mitchell', year=2023, isbn='9780000000001', description='A gripping sci-fi adventure about humanity\'s journey beyond the stars.', category='Fiction', filename='The_Last_Horizon.pdf'),
        Book(title='Whispers in the Dark', author='Michael Torres', year=2022, isbn='9780000000002', description='A psychological thriller set in a small coastal town with dark secrets.', category='Fiction', filename='Whispers_in_the_Dark.pdf'),
        Book(title='The Emerald Crown', author='Amara Johnson', year=2023, isbn='9780000000003', description='An epic fantasy tale of kingdoms, magic, and an ancient prophecy.', category='Fiction', filename='The_Emerald_Crown.pdf'),
        Book(title='Letters from Tomorrow', author='David Chen', year=2021, isbn='9780000000004', description='A time-bending love story that transcends the boundaries of reality.', category='Fiction', filename='Letters_from_Tomorrow.pdf'),
        Book(title='The Silent Witness', author='Rachel Adams', year=2022, isbn='9780000000005', description='A courtroom drama where the truth is more complex than it appears.', category='Fiction', filename='The_Silent_Witness.pdf'),
        Book(title='Beneath the Willow Tree', author='Emily Watson', year=2020, isbn='9780000000006', description='A heartwarming story of family, loss, and redemption in rural America.', category='Fiction', filename='Beneath_the_Willow_Tree.pdf'),
        Book(title='The Quantum Paradox', author='James Nolan', year=2023, isbn='9780000000007', description='A physicist discovers parallel universes and must choose which reality to save.', category='Fiction', filename='The_Quantum_Paradox.pdf'),
        Book(title='Shadows of the Past', author='Grace Okafor', year=2021, isbn='9780000000008', description='A detective novel uncovering cold cases linked to a powerful family.', category='Fiction', filename='Shadows_of_the_Past.pdf'),
        Book(title='The Wanderer\'s Guide', author='Thomas Reed', year=2022, isbn='9780000000009', description='An adventure novel following a traveler across uncharted territories.', category='Fiction', filename='The_Wanderers_Guide.pdf'),
        Book(title='Midnight Express', author='Olivia Grant', year=2023, isbn='9780000000010', description='A mystery thriller aboard a luxury train crossing Europe.', category='Fiction', filename='Midnight_Express.pdf'),
        Book(title='The Forgotten Kingdom', author='Nathan Brooks', year=2020, isbn='9780000000011', description='Archaeologists discover a lost civilization with terrifying secrets.', category='Fiction', filename='The_Forgotten_Kingdom.pdf'),
        Book(title='Dancing with Fire', author='Linda Park', year=2021, isbn='9780000000012', description='A passionate romance set against the backdrop of a wildfire crisis.', category='Fiction', filename='Dancing_with_Fire.pdf'),
        Book(title='The Iron Mask', author='Robert Crane', year=2022, isbn='9780000000013', description='A historical fiction set during the French Revolution.', category='Fiction', filename='The_Iron_Mask.pdf'),
        Book(title='Echoes of Silence', author='Maria Santos', year=2023, isbn='9780000000014', description='A haunting tale of a woman confronting her past in a remote village.', category='Fiction', filename='Echoes_of_Silence.pdf'),
        Book(title='The Dragon\'s Heir', author='Peter Lang', year=2021, isbn='9780000000015', description='A young prince must embrace his dragon heritage to save his realm.', category='Fiction', filename='The_Dragons_Heir.pdf'),
        Book(title='City of Glass', author='Angela Moore', year=2022, isbn='9780000000016', description='A dystopian novel about a transparent society where privacy is extinct.', category='Fiction', filename='City_of_Glass.pdf'),
        Book(title='The Moonlit Path', author='Helen Carter', year=2020, isbn='9780000000017', description='A lyrical novel about self-discovery along the Camino de Santiago.', category='Fiction', filename='The_Moonlit_Path.pdf'),
        Book(title='Broken Chains', author='Marcus Williams', year=2023, isbn='9780000000018', description='Three generations confront their shared legacy of resistance and courage.', category='Fiction', filename='Broken_Chains.pdf'),
        Book(title='The Alchemist\'s Daughter', author='Sofia Rivera', year=2021, isbn='9780000000019', description='In Renaissance Italy, a young woman defies convention to pursue science.', category='Fiction', filename='The_Alchemists_Daughter.pdf'),
        Book(title='Storm Chaser', author='Kevin Blake', year=2022, isbn='9780000000020', description='A meteorologist\'s obsession with a deadly hurricane puts lives at risk.', category='Fiction', filename='Storm_Chaser.pdf'),
        Book(title='The Glass Menagerie', author='Hannah Lee', year=2020, isbn='9780000000021', description='A coming-of-age story set in post-war London.', category='Fiction', filename='The_Glass_Menagerie.pdf'),
        Book(title='Neon Dreams', author='Jason Kim', year=2023, isbn='9780000000022', description='A cyberpunk thriller in a neon-lit megacity of 2087.', category='Fiction', filename='Neon_Dreams.pdf'),
        Book(title='The Painted Bird', author='Clara Bennett', year=2021, isbn='9780000000023', description='A war novel seen through the eyes of a displaced child.', category='Fiction', filename='The_Painted_Bird.pdf'),
        Book(title='Rivers of Gold', author='Daniel Foster', year=2022, isbn='9780000000024', description='An epic saga of the California Gold Rush era.', category='Fiction', filename='Rivers_of_Gold.pdf'),
        Book(title='The Clockwork Heart', author='Priya Sharma', year=2023, isbn='9780000000025', description='A steampunk adventure about an inventor and her mechanical creations.', category='Fiction', filename='The_Clockwork_Heart.pdf'),
        # General
        Book(title='Sapiens: A Brief History of Humankind', author='Yuval Noah Harari', year=2015, isbn='9780062316097', description='The story of how Homo sapiens came to dominate the world.', category='General', filename='Sapiens_A_Brief_History_of_Humankind.pdf'),
        Book(title='The Art of War', author='Sun Tzu', year=2002, isbn='9781590302255', description='Ancient Chinese treatise on military strategy and leadership.', category='General', filename='The_Art_of_War.pdf'),
        Book(title='Thinking, Fast and Slow', author='Daniel Kahneman', year=2011, isbn='9780374533557', description='Exploring the two systems that drive the way we think.', category='General', filename='Thinking_Fast_and_Slow.pdf'),
        Book(title='The 48 Laws of Power', author='Robert Greene', year=2000, isbn='9780140280197', description='Timeless strategies for gaining and maintaining power.', category='General', filename='The_48_Laws_of_Power.pdf'),
        Book(title='Atomic Habits', author='James Clear', year=2018, isbn='9780735211292', description='An easy and proven way to build good habits and break bad ones.', category='General', filename='Atomic_Habits.pdf'),
        Book(title='Educated: A Memoir', author='Tara Westover', year=2018, isbn='9780399590504', description='A woman raised in a survivalist family pursues education.', category='General', filename='Educated_A_Memoir.pdf'),
        Book(title='The Alchemist', author='Paulo Coelho', year=1993, isbn='9780062315007', description='A philosophical story about following your dreams.', category='General', filename='The_Alchemist.pdf'),
        Book(title='Becoming', author='Michelle Obama', year=2018, isbn='9781524763138', description='The memoir of the former First Lady of the United States.', category='General', filename='Becoming.pdf'),
        Book(title='Rich Dad Poor Dad', author='Robert Kiyosaki', year=2000, isbn='9781612680194', description='What the rich teach their kids about money.', category='General', filename='Rich_Dad_Poor_Dad.pdf'),
        Book(title='The Power of Now', author='Eckhart Tolle', year=2004, isbn='9781577314806', description='A guide to spiritual enlightenment and living in the present.', category='General', filename='The_Power_of_Now.pdf'),
        Book(title='How to Win Friends and Influence People', author='Dale Carnegie', year=1998, isbn='9780671027032', description='Classic guide to interpersonal skills and communication.', category='General', filename='How_to_Win_Friends_and_Influence_People.pdf'),
        Book(title='The Subtle Art of Not Giving a F*ck', author='Mark Manson', year=2016, isbn='9780062457714', description='A counterintuitive approach to living a good life.', category='General', filename='The_Subtle_Art_of_Not_Giving_a_Fck.pdf'),
        Book(title='Man\'s Search for Meaning', author='Viktor Frankl', year=2006, isbn='9780807014295', description='A Holocaust survivor\'s exploration of finding purpose in suffering.', category='General', filename='Mans_Search_for_Meaning.pdf'),
        Book(title='The Diary of a Young Girl', author='Anne Frank', year=1993, isbn='9780553296983', description='The wartime diary of a Jewish girl hiding from the Nazis.', category='General', filename='The_Diary_of_a_Young_Girl.pdf'),
        Book(title='Outliers: The Story of Success', author='Malcolm Gladwell', year=2008, isbn='9780316017930', description='What makes high-achievers different from everyone else.', category='General', filename='Outliers_The_Story_of_Success.pdf'),
        Book(title='A Brief History of Time', author='Stephen Hawking', year=1998, isbn='9780553380163', description='Exploring the mysteries of the universe from the Big Bang to black holes.', category='General', filename='A_Brief_History_of_Time.pdf'),
        Book(title='The Lean Startup', author='Eric Ries', year=2011, isbn='9780307887894', description='How constant innovation creates radically successful businesses.', category='General', filename='The_Lean_Startup.pdf'),
        Book(title='Born a Crime', author='Trevor Noah', year=2016, isbn='9780399588174', description='Stories from a South African childhood during apartheid.', category='General', filename='Born_a_Crime.pdf'),
        Book(title='The Immortal Life of Henrietta Lacks', author='Rebecca Skloot', year=2010, isbn='9781400052189', description='The story of the woman behind the HeLa cell line.', category='General', filename='The_Immortal_Life_of_Henrietta_Lacks.pdf'),
        Book(title='Freakonomics', author='Steven Levitt', year=2006, isbn='9780060731335', description='A rogue economist explores the hidden side of everything.', category='General', filename='Freakonomics.pdf'),
        # Science
        Book(title='Cosmos', author='Carl Sagan', year=2013, isbn='9780345539434', description='A journey through the universe exploring space and time.', category='Science', filename='Cosmos.pdf'),
        Book(title='The Gene: An Intimate History', author='Siddhartha Mukherjee', year=2016, isbn='9781476738482', description='The story of the gene and the future of the human genome.', category='Science', filename='The_Gene_An_Intimate_History.pdf'),
        Book(title='Astrophysics for People in a Hurry', author='Neil deGrasse Tyson', year=2017, isbn='9780393609394', description='Essential concepts of astrophysics made accessible.', category='Science', filename='Astrophysics_for_People_in_a_Hurry.pdf'),
        Book(title='The Innovators', author='Walter Isaacson', year=2014, isbn='9781476708706', description='How a group of hackers, geniuses, and geeks created the digital revolution.', category='Science', filename='The_Innovators.pdf'),
        Book(title='Silent Spring', author='Rachel Carson', year=2002, isbn='9780618249060', description='The groundbreaking book that launched the environmental movement.', category='Science', filename='Silent_Spring.pdf'),
        Book(title='The Selfish Gene', author='Richard Dawkins', year=2006, isbn='9780199291151', description='Evolution from the perspective of the gene.', category='Science', filename='The_Selfish_Gene.pdf'),
        Book(title='AI Superpowers', author='Kai-Fu Lee', year=2018, isbn='9781328977878', description='China, Silicon Valley, and the new world order of artificial intelligence.', category='Science', filename='AI_Superpowers.pdf'),
        Book(title='The Structure of Scientific Revolutions', author='Thomas Kuhn', year=2012, isbn='9780226458120', description='How scientific paradigms shift and transform knowledge.', category='Science', filename='The_Structure_of_Scientific_Revolutions.pdf'),
        Book(title='Homo Deus', author='Yuval Noah Harari', year=2017, isbn='9780062464316', description='A brief history of tomorrow and humanity\'s future.', category='Science', filename='Homo_Deus.pdf'),
        Book(title='The Elegant Universe', author='Brian Greene', year=2003, isbn='9780393338102', description='String theory and the hidden dimensions of the universe.', category='Science', filename='The_Elegant_Universe.pdf'),
        Book(title='Code: The Hidden Language', author='Charles Petzold', year=2000, isbn='9780735611313', description='How computers and the internet work from the ground up.', category='Science', filename='Code_The_Hidden_Language.pdf'),
        Book(title='The Origin of Species', author='Charles Darwin', year=2003, isbn='9780451529015', description='Darwin\'s foundational work on evolution by natural selection.', category='Science', filename='The_Origin_of_Species.pdf'),
        Book(title='Breath', author='James Nestor', year=2020, isbn='9780735213616', description='The new science of a lost art of breathing.', category='Science', filename='Breath.pdf'),
        Book(title='Quantum Computing for Everyone', author='Chris Bernhardt', year=2019, isbn='9780262039253', description='An accessible introduction to quantum computing.', category='Science', filename='Quantum_Computing_for_Everyone.pdf'),
        Book(title='The Hidden Life of Trees', author='Peter Wohlleben', year=2016, isbn='9781771642484', description='What trees feel, how they communicate, and their hidden networks.', category='Science', filename='The_Hidden_Life_of_Trees.pdf'),
        # Self-Help
        Book(title='The 7 Habits of Highly Effective People', author='Stephen Covey', year=2004, isbn='9780743269513', description='A holistic approach to personal and professional effectiveness.', category='Self-Help', filename='The_7_Habits_of_Highly_Effective_People.pdf'),
        Book(title='Deep Work', author='Cal Newport', year=2016, isbn='9781455586691', description='Rules for focused success in a distracted world.', category='Self-Help', filename='Deep_Work.pdf'),
        Book(title='Zero to One', author='Peter Thiel', year=2014, isbn='9780804139298', description='Notes on startups and how to build the future.', category='Self-Help', filename='Zero_to_One.pdf'),
        Book(title='Good to Great', author='Jim Collins', year=2001, isbn='9780066620992', description='Why some companies make the leap and others don\'t.', category='Self-Help', filename='Good_to_Great.pdf'),
        Book(title='Mindset: The New Psychology of Success', author='Carol Dweck', year=2007, isbn='9780345472328', description='How the power of mindset can transform your life.', category='Self-Help', filename='Mindset_The_New_Psychology_of_Success.pdf'),
        Book(title='The Four Agreements', author='Don Miguel Ruiz', year=1997, isbn='9781878424310', description='A practical guide to personal freedom and self-limiting beliefs.', category='Self-Help', filename='The_Four_Agreements.pdf'),
        Book(title='Start with Why', author='Simon Sinek', year=2011, isbn='9781591846443', description='How great leaders inspire everyone to take action.', category='Self-Help', filename='Start_with_Why.pdf'),
        Book(title='The $100 Startup', author='Chris Guillebeau', year=2012, isbn='9780307951526', description='Reinvent the way you make a living and join the new rich.', category='Self-Help', filename='The_100_Startup.pdf'),
        Book(title='Grit: The Power of Passion', author='Angela Duckworth', year=2016, isbn='9781501111112', description='Why passion and resilience are the secrets to success.', category='Self-Help', filename='Grit_The_Power_of_Passion.pdf'),
        Book(title='The Obstacle Is the Way', author='Ryan Holiday', year=2014, isbn='9781591846352', description='The timeless art of turning trials into triumph using Stoic philosophy.', category='Self-Help', filename='The_Obstacle_Is_the_Way.pdf'),
        Book(title='Crucial Conversations', author='Kerry Patterson', year=2011, isbn='9780071771320', description='Tools for talking when stakes are high.', category='Self-Help', filename='Crucial_Conversations.pdf'),
        Book(title='Can\'t Hurt Me', author='David Goggins', year=2018, isbn='9781544507859', description='Master your mind and defy the odds.', category='Self-Help', filename='Cant_Hurt_Me.pdf'),
        Book(title='The Lean Entrepreneur', author='Brant Cooper', year=2013, isbn='9781118505373', description='How to create value in a lean startup environment.', category='Self-Help', filename='The_Lean_Entrepreneur.pdf'),
        Book(title='Essentialism: The Disciplined Pursuit of Less', author='Greg McKeown', year=2014, isbn='9780804137386', description='The way of the essentialist for a more meaningful life.', category='Self-Help', filename='Essentialism_The_Disciplined_Pursuit_of_Less.pdf'),
        Book(title='Mastery', author='Robert Greene', year=2012, isbn='9780143124177', description='The keys to success and long-term fulfillment.', category='Self-Help', filename='Mastery.pdf'),
        Book(title='Tools of Titans', author='Tim Ferriss', year=2016, isbn='9781328683786', description='Tactics, routines, and habits of billionaires, icons, and world-class performers.', category='Self-Help', filename='Tools_of_Titans.pdf'),
        Book(title='The Psychology of Money', author='Morgan Housel', year=2020, isbn='9780857197689', description='Timeless lessons on wealth, greed, and happiness.', category='Self-Help', filename='The_Psychology_of_Money.pdf'),
        Book(title='Purple Cow', author='Seth Godin', year=2009, isbn='9781591843177', description='Transform your business by being remarkable.', category='Self-Help', filename='Purple_Cow.pdf'),
        Book(title='Emotional Intelligence', author='Daniel Goleman', year=2005, isbn='9780553383713', description='Why it can matter more than IQ.', category='Self-Help', filename='Emotional_Intelligence.pdf'),
        Book(title='Who Moved My Cheese?', author='Spencer Johnson', year=1998, isbn='9780399144462', description='An amazing way to deal with change in your work and life.', category='Self-Help', filename='Who_Moved_My_Cheese.pdf'),
    ]
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
