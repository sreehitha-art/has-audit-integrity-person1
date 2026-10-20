from sqlalchemy import text
from app.database import engine

with engine.connect() as connection:
    result = connection.execute(text("SELECT current_database()"))
    print("Connected to database:", result.scalar())