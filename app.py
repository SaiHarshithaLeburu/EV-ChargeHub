
import os
from datetime import date, datetime

import mysql.connector
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "local-development-key")


def get_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "ev_charging_db"),
    )


def initialize_database():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(150) NOT NULL UNIQUE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stations (
            station_id INT AUTO_INCREMENT PRIMARY KEY,
            station_name VARCHAR(120) NOT NULL,
            location VARCHAR(200) NOT NULL,
            price_per_kwh DECIMAL(8,2) NOT NULL DEFAULT 15.00
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chargers (
            charger_id INT AUTO_INCREMENT PRIMARY KEY,
            station_id INT NOT NULL,
            charger_type VARCHAR(50) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'Available',
            FOREIGN KEY (station_id)
                REFERENCES stations(station_id)
                ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            booking_id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            charger_id INT NOT NULL,
            booking_date DATE NOT NULL,
            booking_time TIME NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'Booked',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (charger_id) REFERENCES chargers(charger_id),
            UNIQUE KEY unique_charger_slot
                (charger_id, booking_date, booking_time)
        )
    """)

    cursor.execute("SELECT COUNT(*) FROM stations")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("""
            INSERT INTO stations
                (station_name, location, price_per_kwh)
            VALUES (%s, %s, %s)
        """, [
            ("GreenPoint Charging Hub", "Hyderabad - Madhapur", 15.00),
            ("VoltWay EV Station", "Hyderabad - Gachibowli", 18.00),
            ("EcoCharge Central", "Hyderabad - Hitech City", 16.00),
        ])

    cursor.execute("SELECT COUNT(*) FROM chargers")
    if cursor.fetchone()[0] == 0:
        cursor.execute("SELECT station_id FROM stations")
        for (station_id,) in cursor.fetchall():
            cursor.executemany("""
                INSERT INTO chargers (station_id, charger_type)
                VALUES (%s, %s)
            """, [(station_id, "AC"), (station_id, "DC Fast")])

    db.commit()
    cursor.close()
    db.close()


@app.route("/")
def index():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT s.station_id, s.station_name, s.location,
               s.price_per_kwh, COUNT(c.charger_id) AS total_chargers
        FROM stations s
        LEFT JOIN chargers c ON s.station_id = c.station_id
        GROUP BY s.station_id, s.station_name,
                 s.location, s.price_per_kwh
        ORDER BY s.station_id
    """)
    stations = cursor.fetchall()

    cursor.execute("""
        SELECT b.booking_id, u.name, u.email, s.station_name,
               c.charger_type, b.booking_date, b.booking_time, b.status
        FROM bookings b
        JOIN users u ON b.user_id = u.user_id
        JOIN chargers c ON b.charger_id = c.charger_id
        JOIN stations s ON c.station_id = s.station_id
        ORDER BY b.booking_id DESC LIMIT 50
    """)
    bookings = cursor.fetchall()

    cursor.execute("""
        SELECT c.charger_id, c.charger_type, s.station_id,
               s.station_name, s.location
        FROM chargers c
        JOIN stations s ON c.station_id = s.station_id
        ORDER BY s.station_name, c.charger_id
    """)
    chargers = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "index.html",
        stations=stations,
        bookings=bookings,
        chargers=chargers,
        today=date.today().isoformat()
    )


@app.route("/book", methods=["POST"])
def book():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    charger_id = request.form.get("charger_id", type=int)
    booking_date = request.form.get("booking_date", "")
    booking_time = request.form.get("booking_time", "")

    if not all([name, email, charger_id, booking_date, booking_time]):
        flash("Please fill in every booking field.", "error")
        return redirect(url_for("index"))

    try:
        slot = datetime.strptime(
            f"{booking_date} {booking_time}", "%Y-%m-%d %H:%M"
        )
    except ValueError:
        flash("Please select a valid date and time.", "error")
        return redirect(url_for("index"))

    if slot <= datetime.now():
        flash("Choose a future date and time.", "error")
        return redirect(url_for("index"))

    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("""
            INSERT INTO users (name, email) VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE name = %s
        """, (name, email, name))

        cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
        user_id = cursor.fetchone()[0]

        cursor.execute("""
            INSERT INTO bookings
                (user_id, charger_id, booking_date, booking_time)
            VALUES (%s, %s, %s, %s)
        """, (user_id, charger_id, booking_date, booking_time))

        db.commit()
        flash("Booking created successfully!", "success")

    except mysql.connector.IntegrityError:
        db.rollback()
        flash("This charger already has a booking at that date and time.", "error")
    except mysql.connector.Error:
        db.rollback()
        app.logger.exception("Booking failed")
        flash("Could not save the booking. Please try again.", "error")
    finally:
        cursor.close()
        db.close()

    return redirect(url_for("index"))


@app.route("/station/add", methods=["POST"])
def add_station():
    name = request.form.get("station_name", "").strip()
    location = request.form.get("location", "").strip()
    charger_type = request.form.get("charger_type", "AC")
    price = request.form.get("price_per_kwh", type=float)

    if not name or not location or price is None or price <= 0:
        flash("Enter valid station details.", "error")
        return redirect(url_for("index"))

    if charger_type not in ("AC", "DC Fast"):
        flash("Select a valid charger type.", "error")
        return redirect(url_for("index"))

    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("""
            INSERT INTO stations (station_name, location, price_per_kwh)
            VALUES (%s, %s, %s)
        """, (name, location, price))

        cursor.execute("""
            INSERT INTO chargers (station_id, charger_type)
            VALUES (%s, %s)
        """, (cursor.lastrowid, charger_type))

        db.commit()
        flash("Station added successfully!", "success")
    except mysql.connector.Error:
        db.rollback()
        app.logger.exception("Adding station failed")
        flash("Could not add the station.", "error")
    finally:
        cursor.close()
        db.close()

    return redirect(url_for("index"))


@app.route("/booking/<int:booking_id>/cancel", methods=["POST"])
def cancel_booking(booking_id):
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("""
            UPDATE bookings SET status = 'Cancelled'
            WHERE booking_id = %s AND status = 'Booked'
        """, (booking_id,))
        db.commit()

        if cursor.rowcount:
            flash("Booking cancelled.", "success")
        else:
            flash("Booking not found or already cancelled.", "error")
    except mysql.connector.Error:
        db.rollback()
        app.logger.exception("Cancelling booking failed")
        flash("Could not cancel the booking.", "error")
    finally:
        cursor.close()
        db.close()

    return redirect(url_for("index"))


initialize_database()

if __name__ == "__main__":
    app.run(debug=True)
