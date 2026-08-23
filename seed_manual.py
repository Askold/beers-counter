"""
Seed the beers.db database from manually compiled data.
Run from the beers-counter directory on the VPS:

    docker cp seed_manual.py beers-counter:/app/seed_manual.py
    docker exec -it beers-counter python seed_manual.py

WARNING: this wipes all existing data in the beers table first.
"""
import sys
sys.path.insert(0, ".")
import database

# Each entry: (fake_id, username, full_name, count)
# Fake IDs are negative so they never collide with real Telegram user IDs.
# Once a real user sends a circle video the bot will create a new row
# with their actual Telegram ID — you can then DELETE the fake row manually.
USERS = [
    (-1,  "vovarov",         "Вова Варов",           15),
    (-2,  "mavrin_b",        "Богдан",                20),
    (-3,  "Shtralex",        "Штракс",                 2),
    (-4,  "m1_robot",        "Стас",                   1),
    (-5,  "shum_sky",        "Артемий",                2),
    (-6,  "andrey_kozloff",  "Андрей козлов",          1),
    (-7,  "MarkGragSputnik", "Никита курюмов",         3),
    (-8,  "Gold_mock",       "Дмитрий бондаренко",     1),
    (-9,  None,              "Unknown",                7),
    (-10, "rozenkin",        "Денис Розенкин",         4),
    (-11, "ustino_v",        "Вова Устинов",           1),
    (-12, "mynameismud254",  "Платон Баталов",         4),
    (-13, "slowlearner1234", "Денис Иванников",        4),
    (-14, "thechute",        "Никита велиев",          2),
]


def main():
    database.init_db()
    from database import get_connection
    with get_connection() as conn:
        conn.execute("DELETE FROM beers")
        conn.execute("DELETE FROM video_log")
        for fake_id, username, full_name, count in USERS:
            conn.execute(
                "INSERT INTO beers (user_id, username, full_name, count) VALUES (?, ?, ?, ?)",
                (fake_id, username, full_name, count),
            )
        conn.commit()
    total = sum(u[3] for u in USERS)
    print(f"✅ Seeded {len(USERS)} users, {total} beers total.")
    print("Run /leaderboard in the bot to verify.")


if __name__ == "__main__":
    main()
