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

# --- WEB SERVER ---
app = Flask('')
@app.route('/')
def home(): return "I am alive!"
def run_http(): app.run(host='0.0.0.0', port=8080)
def keep_alive():
    t = Thread(target=run_http)
    t.start()

# --- CONFIG ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SHEET_NAME = "Expenses"

# --- SHEETS SETUP ---
creds_json_str = os.environ.get("GOOGLE_CREDS")
creds_dict = json.loads(creds_json_str)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open(SHEET_NAME).sheet1

def ask_gemini(audio_b64, model_name):
    """Helper function to try a specific model"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{
            "parts": [
                {"text": "Extract JSON: item, amount, category (Food, Transport, Tech, Bills, Misc). Ex: {\"item\": \"Coffee\", \"amount\": 5, \"category\": \"Food\"}"},
                {"inline_data": {"mime_type": "audio/ogg", "data": audio_b64}}
            ]
        }]
    }
    return requests.post(url, json=payload)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    await update.message.reply_text(f"Processing...")

    file_id = update.message.voice.file_id
    new_file = await context.bot.get_file(file_id)
    file_path = f"voice_{update.message.id}.oga"
    await new_file.download_to_drive(file_path)

    try:
        with open(file_path, "rb") as f:
            b64_audio = base64.b64encode(f.read()).decode('utf-8')

        # --- ATTEMPT 1: FLASH ---
        response = ask_gemini(b64_audio, "gemini-1.5-flash")
        
        # --- ATTEMPT 2: PRO (Fallback) ---
        if response.status_code != 200:
            await update.message.reply_text("Flash model failed, trying Pro model...")
            response = ask_gemini(b64_audio, "gemini-1.5-pro")

        # --- FINAL CHECK ---
        if response.status_code != 200:
            # If both failed, ask Google what IS available
            list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
            list_resp = requests.get(list_url)
            
            if list_resp.status_code == 200:
                models = [m['name'] for m in list_resp.json().get('models', [])]
                debug_msg = "\n".join(models)
                await update.message.reply_text(f"❌ ALL Models Failed.\n\nHere are the models your Key CAN see:\n{debug_msg}")
            else:
                await update.message.reply_text(f"❌ API Key Error. Google says: {response.text}")
            return

        # SUCCESS PARSING
        result = response.json()
        text_resp = result['candidates'][0]['content']['parts'][0]['text']
        clean_json = text_resp.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json)

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
