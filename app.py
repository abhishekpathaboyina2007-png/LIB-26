from flask import Flask, render_template, request, redirect, session, flash
import sqlite3
from datetime import datetime, timedelta
import qrcode
from io import BytesIO
from flask import send_file
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from functools import wraps

app = Flask(__name__)
app.secret_key = "your-secret-key-change-this"

@app.context_processor
def inject_session():
    return dict(session=session)

# ── DB ──────────────────────────────────────────────
def db():
    conn = sqlite3.connect("library.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id       INTEGER PRIMARY KEY,
        name     TEXT,
        email    TEXT UNIQUE,
        password TEXT,
        role     TEXT DEFAULT 'member'
    );

    CREATE TABLE IF NOT EXISTS books(
        id               INTEGER PRIMARY KEY,
        title            TEXT,
        author           TEXT,
        genre            TEXT,
        total_copies     INTEGER DEFAULT 1,
        available_copies INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS transactions(
        id          INTEGER PRIMARY KEY,
        user_id     INTEGER,
        book_id     INTEGER,
        issue_date  TEXT,
        due_date    TEXT,
        return_date TEXT,
        fine        INTEGER DEFAULT 0,
        status      TEXT DEFAULT 'issued',
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(book_id) REFERENCES books(id)
    );
    """)
    conn.commit()

def seed():
    conn = db()
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
            [
                ("Admin",      "admin@lib.com", "123", "admin"),
                ("Librarian",  "lib@lib.com",   "123", "librarian"),
                ("Member",     "mem@lib.com",   "123", "member"),
            ]
        )
    if conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO books(title,author,genre,total_copies,available_copies) VALUES(?,?,?,?,?)",
            [
                ("Python Crash Course",    "Eric Matthes",  "Programming", 3, 3),
                ("Flask Web Development",  "Miguel Grinberg","Web",         2, 2),
                ("Database System Concepts","Navathe",       "Education",   4, 4),
                ("Clean Code",             "Robert Martin", "Programming", 2, 2),
                ("The Pragmatic Programmer","Dave Thomas",  "Programming", 3, 3),
            ]
        )
    conn.commit()

# ── AUTH DECORATORS ─────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'id' not in session:
            return redirect('/')
        return f(*args, **kwargs)
    return decorated

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('role') not in roles:
                return render_template("error.html", message="Access Denied"), 403
            return f(*args, **kwargs)
        return decorated
    return decorator

# ── AUTH ─────────────────────────────────────────────
@app.route('/', methods=['GET', 'POST'])
def login():
    if 'id' in session:
        return redirect('/' + session['role'])

    error = None
    if request.method == 'POST':
        user = db().execute(
            "SELECT * FROM users WHERE email=? AND password=?",
            (request.form['email'], request.form['password'])
        ).fetchone()

        if user:
            session['id']   = user['id']
            session['name'] = user['name']
            session['role'] = user['role']
            return redirect('/' + user['role'])
        else:
            error = "Invalid email or password."

    return render_template("login.html", error=error)


@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        name     = request.form['name'].strip()
        email    = request.form['email'].strip()
        password = request.form['password']

        if not name or not email or not password:
            error = "All fields are required."
        elif len(password) < 3:
            error = "Password must be at least 3 characters."
        else:
            try:
                conn = db()
                conn.execute(
                    "INSERT INTO users(name,email,password,role) VALUES(?,?,?,'member')",
                    (name, email, password)
                )
                conn.commit()
                return redirect('/')
            except sqlite3.IntegrityError:
                error = "An account with that email already exists."

    return render_template("register.html", error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ── ADMIN ─────────────────────────────────────────────
@app.route('/admin')
@login_required
@role_required('admin')
def admin():
    conn = db()
    users           = conn.execute("SELECT * FROM users").fetchall()
    books_count     = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    members_count   = conn.execute("SELECT COUNT(*) FROM users WHERE role='member'").fetchone()[0]
    issued_count    = conn.execute("SELECT COUNT(*) FROM transactions WHERE status='issued'").fetchone()[0]
    overdue_count   = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE status='issued' AND due_date < ?",
        (datetime.now().strftime('%Y-%m-%d'),)
    ).fetchone()[0]

    return render_template("admin_dashboard.html",
        users=users,
        books_count=books_count,
        members_count=members_count,
        transactions_count=issued_count,
        overdue_count=overdue_count
    )


@app.route('/admin/create_user', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def create_user():
    error = None
    if request.method == 'POST':
        try:
            conn = db()
            conn.execute(
                "INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
                (request.form['name'], request.form['email'],
                 request.form['password'], request.form['role'])
            )
            conn.commit()
            return redirect('/admin')
        except sqlite3.IntegrityError:
            error = "Email already exists."

    return render_template("create_user.html", error=error)


@app.route('/admin/delete_user/<int:user_id>')
@login_required
@role_required('admin')
def delete_user(user_id):
    if user_id == session.get('id'):
        return "Cannot delete yourself", 400
    conn = db()
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    return redirect('/admin')


@app.route('/admin/assign_role/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def assign_role(user_id):
    conn = db()
    conn.execute("UPDATE users SET role=? WHERE id=?",
                 (request.form['role'], user_id))
    conn.commit()
    return redirect('/admin')

# ── LIBRARIAN ─────────────────────────────────────────
@app.route('/librarian')
@login_required
@role_required('librarian', 'admin')
def librarian():
    conn = db()
    books = conn.execute("SELECT * FROM books").fetchall()
    tx    = conn.execute("""
        SELECT t.*, u.name user_name, b.title book_title
        FROM transactions t
        JOIN users u ON t.user_id = u.id
        JOIN books b ON t.book_id = b.id
        WHERE t.status = 'issued'
        ORDER BY t.due_date ASC
    """).fetchall()

    today    = datetime.now().strftime('%Y-%m-%d')
    overdue  = [t for t in tx if t['due_date'] < today]

    return render_template("librarian_dashboard.html",
        books=books, transactions=tx, overdue=overdue, today=today
    )


@app.route('/librarian/add_book', methods=['GET', 'POST'])
@login_required
@role_required('librarian', 'admin')
def add_book():
    error = None
    if request.method == 'POST':
        try:
            total = int(request.form['total_copies'])
            if total < 1:
                raise ValueError
            conn = db()
            conn.execute(
                "INSERT INTO books(title,author,genre,total_copies,available_copies) VALUES(?,?,?,?,?)",
                (request.form['title'].strip(), request.form['author'].strip(),
                 request.form['genre'].strip(), total, total)
            )
            conn.commit()
            return redirect('/librarian')
        except ValueError:
            error = "Copies must be a positive number."

    return render_template("add_book.html", error=error)


@app.route('/librarian/edit_book/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('librarian', 'admin')
def edit_book(id):
    conn = db()
    if request.method == 'POST':
        conn.execute(
            "UPDATE books SET title=?, author=?, genre=? WHERE id=?",
            (request.form['title'], request.form['author'],
             request.form['genre'], id)
        )
        conn.commit()
        return redirect('/librarian')

    book = conn.execute("SELECT * FROM books WHERE id=?", (id,)).fetchone()
    if not book:
        return render_template("error.html", message="Book not found"), 404
    return render_template("edit_book.html", book=book)


@app.route('/librarian/delete_book/<int:id>')
@login_required
@role_required('librarian', 'admin')
def delete_book(id):
    conn = db()
    # prevent deleting if copies are out
    active = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE book_id=? AND status='issued'", (id,)
    ).fetchone()[0]
    if active > 0:
        return render_template("error.html",
            message="Cannot delete — this book has active issues."), 400
    conn.execute("DELETE FROM books WHERE id=?", (id,))
    conn.commit()
    return redirect('/librarian')


@app.route('/librarian/issue_book', methods=['GET', 'POST'])
@login_required
@role_required('librarian', 'admin')
def issue_book():
    conn  = db()
    error = None

    if request.method == 'POST':
        user = conn.execute(
            "SELECT * FROM users WHERE email=?", (request.form['user_email'],)
        ).fetchone()
        book = conn.execute(
            "SELECT * FROM books WHERE id=?", (request.form['book_id'],)
        ).fetchone()

        if not user:
            error = "No member found with that email."
        elif not book:
            error = "Book not found."
        elif book['available_copies'] < 1:
            error = "No copies available for that book."
        else:
            issue = datetime.now()
            due   = issue + timedelta(days=7)
            conn.execute("""
                INSERT INTO transactions(user_id,book_id,issue_date,due_date,status)
                VALUES(?,?,?,?,'issued')
            """, (user['id'], book['id'],
                  issue.strftime('%Y-%m-%d'), due.strftime('%Y-%m-%d')))
            conn.execute(
                "UPDATE books SET available_copies=available_copies-1 WHERE id=?",
                (book['id'],)
            )
            conn.commit()
            return redirect('/librarian')

    books = conn.execute("SELECT * FROM books WHERE available_copies > 0").fetchall()
    return render_template("issue_book.html", books=books, error=error)


@app.route('/librarian/return_book/<int:id>')
@login_required
@role_required('librarian', 'admin')
def return_book(id):
    conn = db()
    t    = conn.execute("SELECT * FROM transactions WHERE id=?", (id,)).fetchone()

    if not t or t['status'] == 'returned':
        return render_template("error.html", message="Invalid transaction."), 400

    today = datetime.now()
    due   = datetime.strptime(t['due_date'], '%Y-%m-%d')
    fine  = max(0, (today - due).days * 5) if today > due else 0

    conn.execute("""
        UPDATE transactions
        SET return_date=?, status='returned', fine=?
        WHERE id=?
    """, (today.strftime('%Y-%m-%d'), fine, id))
    conn.execute(
        "UPDATE books SET available_copies=available_copies+1 WHERE id=?",
        (t['book_id'],)
    )
    conn.commit()
    return redirect('/librarian')

# ── MEMBER ────────────────────────────────────────────
@app.route('/member')
@login_required
@role_required('member')
def member():
    conn  = db()
    books = conn.execute("""
        SELECT t.*, b.title, b.author, b.genre
        FROM transactions t
        JOIN books b ON t.book_id = b.id
        WHERE t.user_id = ?
        ORDER BY t.issue_date DESC
    """, (session['id'],)).fetchall()
    return render_template("member_dashboard.html", borrowed_books=books)


@app.route('/member/search_books', methods=['GET', 'POST'])
@login_required
@role_required('member')
def search():
    if request.method == 'POST':
        q     = "%" + request.form.get('query', '').strip() + "%"
        books = db().execute("""
            SELECT * FROM books
            WHERE title LIKE ? OR author LIKE ? OR genre LIKE ?
            ORDER BY title ASC
        """, (q, q, q)).fetchall()
    else:
        books = db().execute(
            "SELECT * FROM books ORDER BY title ASC"
        ).fetchall()

    return render_template("search_books.html", books=books)

# ── RECEIPT ───────────────────────────────────────────
@app.route('/receipt/<int:tx_id>')
@login_required
def receipt(tx_id):
    conn = db()
    tx   = conn.execute("""
        SELECT t.*, u.name user_name, b.title book_title
        FROM transactions t
        JOIN users u ON t.user_id = u.id
        JOIN books b ON t.book_id = b.id
        WHERE t.id = ?
    """, (tx_id,)).fetchone()

    if not tx:
        return render_template("error.html", message="Receipt not found."), 404

    # Members can only view their own receipts
    if session['role'] == 'member' and tx['user_id'] != session['id']:
        return render_template("error.html", message="Access Denied."), 403

    return render_template("receipt.html", tx=tx)


@app.route('/receipt/pdf/<int:tx_id>')
@login_required
def receipt_pdf(tx_id):
    conn = db()
    tx   = conn.execute("""
        SELECT t.*, u.name user_name, b.title book_title
        FROM transactions t
        JOIN users u ON t.user_id = u.id
        JOIN books b ON t.book_id = b.id
        WHERE t.id = ?
    """, (tx_id,)).fetchone()

    if not tx:
        return "Receipt not found", 404

    # Build PDF
    pdf_io = BytesIO()
    doc    = SimpleDocTemplate(pdf_io, rightMargin=inch, leftMargin=inch,
                               topMargin=inch, bottomMargin=inch)
    styles = getSampleStyleSheet()
    story  = []

    story.append(Paragraph("📚 Library Receipt", styles['Title']))
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(f"<b>Member:</b>    {tx['user_name']}",  styles['Normal']))
    story.append(Paragraph(f"<b>Book:</b>       {tx['book_title']}", styles['Normal']))
    story.append(Paragraph(f"<b>Issue Date:</b> {tx['issue_date']}", styles['Normal']))
    story.append(Paragraph(f"<b>Due Date:</b>   {tx['due_date']}",   styles['Normal']))

    if tx['return_date']:
        story.append(Paragraph(f"<b>Returned:</b>  {tx['return_date']}", styles['Normal']))
        story.append(Paragraph(f"<b>Fine:</b>      ₹{tx['fine']}",       styles['Normal']))

    doc.build(story)
    pdf_io.seek(0)
    return send_file(pdf_io, as_attachment=True, download_name="receipt.pdf",
                     mimetype='application/pdf')

# ── ERROR PAGE ────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", message="Page not found."), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", message="Something went wrong."), 500

# ── RUN ───────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    seed()
    app.run(debug=True)