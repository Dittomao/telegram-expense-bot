import os
import json
import logging
import requests
import base64
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from flask import Flask
from threading import Thread

# --- WEB SERVER (Keep Alive) ---
app = Flask('')
@app.route('/')
def home(): return "I am alive!"
def run_http(): app.run(host='0.0.0.0', port=8080)
def keep_alive():
    t = Thread(target=run_http)
    t.start()

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SHEET_NAME = "Expenses"

# --- GOOGLE SHEETS SETUP ---
creds_json_str = os.environ.get("GOOGLE_CREDS")
creds_dict = json.loads(creds_json_str)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open(SHEET_NAME).sheet1

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    await update.message.reply_text(f"Processing voice note from {user.first_name}...")

    # 1. Download File
    file_id = update.message.voice.file_id
    new_file = await context.bot.get_file(file_id)
    file_path = f"voice_{update.message.id}.oga"
    await new_file.download_to_drive(file_path)

    try:
        # 2. Read and Encode Audio (The Direct Way)
        with open(file_path, "rb") as f:
            audio_data = f.read()
        b64_audio = base64.b64encode(audio_data).decode('utf-8')

        # 3. Send to Gemini via Raw HTTP (Bypassing the broken library)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": "Listen to this audio. Return ONLY a JSON object with fields: item, amount, category (Food, Transport, Tech, Bills, Misc). Example: {\"item\": \"Coffee\", \"amount\": 5, \"category\": \"Food\"}"},
                    {
                        "inline_data": {
                            "mime_type": "audio/ogg",
                            "data": b64_audio
                        }
                    }
                ]
            }]
        }

        response = requests.post(url, json=payload)
        
        # Check for errors from Google
        if response.status_code != 200:
            await update.message.reply_text(f"Google Error: {response.text}")
            return

        # 4. Parse Response
        result = response.json()
        text_resp = result['candidates'][0]['content']['parts'][0]['text']
        
        # Clean JSON
        clean_json = text_resp.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json)

        # 5. Save to Sheets
        row = [str(datetime.now().date()), data.get('item'), data.get('amount'), data.get('category')]
        sheet.append_row(row)
        await update.message.reply_text(f"✅ Saved: {data.get('item')} - {data.get('amount')}")

    except Exception as e:
        await update.message.reply_text(f"System Error: {str(e)}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)

if __name__ == '__main__':
    keep_alive()
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    voice_handler = MessageHandler(filters.VOICE, handle_voice)
    application.add_handler(voice_handler)
    application.run_polling()
