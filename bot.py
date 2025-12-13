import os
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import pickle

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Google Drive API scopes
SCOPES = ['https://www.googleapis.com/auth/drive.file']

# Initialize Pyrogram client
app = Client(
    "gdrive_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

def get_gdrive_service():
    """Get Google Drive service instance"""
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    
    return build('drive', 'v3', credentials=creds)

@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    await message.reply_text(
        "👋 Hello! I'm a Google Drive Upload Bot.\n\n"
        "📎 Send me any file and I'll upload it to your Google Drive!\n\n"
        "Commands:\n"
        "/start - Show this message\n"
        "/help - Get help"
    )

@app.on_message(filters.command("help"))
async def help_command(client: Client, message: Message):
    await message.reply_text(
        "📚 How to use:\n\n"
        "1. Send me any document/file\n"
        "2. I'll download it temporarily\n"
        "3. Upload it to your Google Drive\n"
        "4. Send you the link\n\n"
        "✨ Simple as that!"
    )

@app.on_message(filters.document | filters.video | filters.audio | filters.photo)
async def handle_file(client: Client, message: Message):
    try:
        # Send status message
        status_msg = await message.reply_text("⏬ Downloading file...")
        
        # Download file
        file_path = await message.download()
        file_name = message.document.file_name if message.document else f"file_{message.id}"
        
        await status_msg.edit_text("☁️ Uploading to Google Drive...")
        
        # Upload to Google Drive
        service = get_gdrive_service()
        file_metadata = {'name': file_name}
        media = MediaFileUpload(file_path, resumable=True)
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        
        # Delete local file
        os.remove(file_path)
        
        # Send success message
        await status_msg.edit_text(
            f"✅ File uploaded successfully!\n\n"
            f"📁 File Name: {file_name}\n"
            f"🔗 Link: {file.get('webViewLink')}"
        )
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await message.reply_text(f"❌ Error: {str(e)}")

if __name__ == "__main__":
    logger.info("🚀 Bot starting...")
    app.run()
