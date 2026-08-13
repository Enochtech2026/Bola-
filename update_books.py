#!/usr/bin/env python
"""Update books with PDF filenames"""

import os
import sys

# Ensure we're in the right directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

from app import app, db, Book

def main():
    with app.app_context():
        print("Updating books with PDF filenames...")
        
        updates = {
            'Clean Code': 'clean_code.pdf',
            'Introduction to Algorithms': 'intro_algorithms.pdf',
            'The Pragmatic Programmer': 'pragmatic_programmer.pdf',
        }
        
        for title, filename in updates.items():
            book = Book.query.filter_by(title=title).first()
            if book:
                book.filename = filename
                db.session.add(book)
                print(f"  ✓ {title} -> {filename}")
            else:
                print(f"  ✗ {title} NOT FOUND")
        
        db.session.commit()
        print("\nDatabase committed!")
        
        # Verify
        print("\nVerifying all books:")
        books = Book.query.all()
        for book in books:
            status = f"✓ PDF: {book.filename}" if book.filename else "✗ No PDF"
            print(f"  {book.title} [{status}]")

if __name__ == '__main__':
    main()
