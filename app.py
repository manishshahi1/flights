from flask import Flask, render_template, request, jsonify
import pandas as pd
import requests
import os
import sqlite3
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime
import markdown  # Add at the top with other imports

load_dotenv()
app = Flask(__name__)
AVIATION_API_KEY = os.getenv("AVIATIONSTACK_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

DB_PATH = "flight_data.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS flights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            route TEXT,
            airline TEXT,
            flight TEXT,
            status TEXT,
            departure TEXT,
            arrival TEXT,
            dep_iata TEXT,
            arr_iata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def cache_flight_data(dep_iata):
    base_url = "http://api.aviationstack.com/v1/flights"
    url = f"{base_url}?access_key={AVIATION_API_KEY}&dep_iata={dep_iata}&limit=100"
    res = requests.get(url)
    flights = res.json().get("data", [])

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for flight in flights:
        try:
            cursor.execute("""
                INSERT INTO flights (date, route, airline, flight, status, departure, arrival, dep_iata, arr_iata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                flight["departure"]["scheduled"][:10] if flight["departure"]["scheduled"] else None,
                f"{flight['departure']['iata']}-{flight['arrival']['iata']}",
                flight["airline"]["name"],
                flight["flight"]["iata"],
                flight["flight_status"],
                flight["departure"]["airport"],
                flight["arrival"]["airport"],
                flight["departure"]["iata"],
                flight["arrival"]["iata"]
            ))
        except:
            continue

    conn.commit()
    conn.close()

def fetch_cached_data(dep_iata):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT date, route, airline, flight, status FROM flights WHERE dep_iata=? ORDER BY date DESC LIMIT 200", (dep_iata,))
    rows = cursor.fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["date", "route", "airline", "flight", "status"])

def generate_insight_text(df):
    if df.empty:
        return "No data to analyze."
    # Summarize data for the prompt
    top_routes = df['route'].value_counts().head(5).to_dict()
    top_airlines = df['airline'].value_counts().head(5).to_dict()
    total_flights = len(df)
    prompt = f'''
    Analyze the following flight data summary and give insights:
    - Busiest routes: {top_routes}
    - Most frequent airlines: {top_airlines}
    - Total flights: {total_flights}
    '''
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
    GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": GEMINI_API_KEY
    }
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }
    try:
        response = requests.post(GEMINI_API_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        # Parse the response as per Gemini API
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"Error calling Gemini API: {e}"

def normalize_airline(airline):
    if not airline:
        return "Unknown"
    airline = airline.strip()
    if airline in ["QantasLink", "Qantas"]:
        return "Qantas"
    if airline in ["Virgin Australia", "Virgin Australia "]:
        return "Virgin Australia"
    if airline in ["Rex Regional Express", "Regional Express"]:
        return "Rex Regional Express"
    return airline

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    city = request.form.get("city", "SYD")

    cache_flight_data(city)
    df = fetch_cached_data(city)

    if df.empty:
        return jsonify({"error": "No data available"})

    # Normalize airline names
    df["airline_normalized"] = df["airline"].apply(normalize_airline)
    top_routes = df["route"].value_counts().head(10)
    # Sort dates chronologically for top 10
    top_dates_series = df["date"].value_counts().head(10)
    top_dates_sorted = top_dates_series.sort_index()
    # All flights per day, sorted chronologically
    all_dates_series = df["date"].value_counts().sort_index()
    flights_per_day = all_dates_series.to_dict()
    # Pad with previous and next date if only one date exists
    if len(flights_per_day) == 1:
        only_date = list(flights_per_day.keys())[0]
        dt = pd.to_datetime(only_date)
        prev_date = (dt - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        next_date = (dt + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        flights_per_day = {prev_date: 0, only_date: flights_per_day[only_date], next_date: 0}
    print("Flights per day:", flights_per_day)  # 👈 Debug print
    # Top airlines
    top_airlines = df["airline_normalized"].value_counts().head(10).to_dict()
    insights = generate_insight_text(df)
    # Convert markdown to HTML
    insights_html = markdown.markdown(insights)

    return jsonify({
        "routes": top_routes.to_dict(),
        "dates": top_dates_sorted.to_dict(),
        "flights_per_day": flights_per_day,
        "top_airlines": top_airlines,
        "insights": insights_html
    })

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
