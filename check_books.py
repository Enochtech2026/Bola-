from app import app, db, Book

with app.app_context():
    books = Book.query.all()
    print("\n📚 All Books in Library:")
    print("-" * 60)
    for book in books:
        pdf_status = "✓ DOWNLOADABLE" if book.filename else "✗ No PDF"
        print(f"{book.title:40} {pdf_status}")
    print("-" * 60)
    
    # Count downloadable books
    downloadable = Book.query.filter(Book.filename != None, Book.filename != '').count()
    total = Book.query.count()
    print(f"\nDownloadable: {downloadable}/{total} books\n")
