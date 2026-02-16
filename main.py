import os
import json
import logging
import google.generativeai as genai
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from flask import Flask
from threading import Thread

# --- WEB SERVER TO KEEP BOT ALIVE ---
app = Flask('')

@app.route('/')
def home():
    return "I am alive!"

def run_http():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SHEET_NAME = "Expenses" # Must match your Google Sheet name exactly!

# --- GOOGLE SHEETS SETUP ---
# We load the credentials from an environment variable called GOOGLE_CREDS
creds_json_str = os.environ.get("GOOGLE_CREDS")
creds_dict = json.loads(creds_json_str)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open(SHEET_NAME).sheet1

# --- GEMINI SETUP ---
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    await update.message.reply_text(f"Processing voice note from {user.first_name}...")

    # Download file
    file_id = update.message.voice.file_id
    new_file = await context.bot.get_file(file_id)
    file_path = f"voice_{update.message.id}.oga"
    await new_file.download_to_drive(file_path)

    try:
        # Upload to Gemini
        uploaded_file = genai.upload_file(path=file_path, mime_type="audio/ogg")
        
        # Ask Gemini to categorize
        prompt = """
        Listen to this audio. It is a transaction.
        Extract these fields into a JSON object:
        1. "item": What was bought?
        2. "amount": The cost (number only).
        3. "category": One of [Food, Transport, Tech, Bills, Misc].
        
        Example output: {"item": "Burger", "amount": 150, "category": "Food"}
        """
        response = model.generate_content([prompt, uploaded_file])
        
        # Clean the response
        text_resp = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text_resp)

        # Save to Sheet
        row = [str(datetime.now().date()), data.get('item'), data.get('amount'), data.get('category')]
        sheet.append_row(row)
        await update.message.reply_text(f"✅ Saved: {data['item']} - {data['amount']}")

    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)

if __name__ == '__main__':
    keep_alive()
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    voice_handler = MessageHandler(filters.VOICE, handle_voice)
    application.add_handler(voice_handler)
    application.run_polling()
