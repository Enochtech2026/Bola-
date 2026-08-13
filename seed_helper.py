from app import init_db, app

with app.app_context():
    init_db(seed=True)
    print('Database initialized and seeded')
