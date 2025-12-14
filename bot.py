import os
import logging
import time
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
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")

# Google Drive API scopes
SCOPES = ['https://www.googleapis.com/auth/drive.file']

# Upload chunk size (5MB)
UPLOAD_CHUNK_SIZE = 5 * 1024 * 1024

# Global dictionary to track update times per message
last_update_time = {}

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

async def download_progress(current, total, status_msg):
    """Progress callback for download with 30-second flood protection"""
    try:
        message_id = status_msg.id
        current_time = time.time()
        
        # Get last update time for this message
        last_time = last_update_time.get(message_id, 0)
        
        # Only update if 30 seconds have passed since last update
        if current_time - last_time < 30:
            return
            
        progress_percent = int((current / total) * 100)
        speed_mb = current / 1024 / 1024
        total_mb = total / 1024 / 1024
        
        await status_msg.edit_text(
            f"⏬ Downloading file...\n"
            f"📊 Progress: {progress_percent}%\n"
            f"💾 {speed_mb:.1f} MB / {total_mb:.1f} MB"
        )
        
        # Update last update time
        last_update_time[message_id] = current_time
        
    except Exception:
        # Silently ignore all errors including FloodWait
        pass

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
    status_msg = None
    try:
        # Send status message
        status_msg = await message.reply_text("⏬ Downloading file...")
        
        # Download file with progress
        file_path = await message.download(progress=download_progress, progress_args=(status_msg,))
        
        # Clean up download progress tracking
        if status_msg.id in last_update_time:
            del last_update_time[status_msg.id]
        
        # Get filename - prioritize caption, then document name, then video/audio filename
        if message.caption and message.caption.strip():
            # Use caption as filename
            file_name = message.caption.strip()
            # Add extension if missing
            if message.video:
                if not any(file_name.lower().endswith(ext) for ext in ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv']):
                    file_name += '.mkv'  # Default to .mkv for video files
            elif message.audio:
                if not any(file_name.lower().endswith(ext) for ext in ['.mp3', '.m4a', '.flac', '.wav', '.aac']):
                    file_name += '.mp3'
            elif message.photo:
                if not any(file_name.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                    file_name += '.jpg'
        elif message.document:
            file_name = message.document.file_name
        elif message.video and message.video.file_name:
            file_name = message.video.file_name
        else:
            file_name = f"file_{message.id}"
        
        await status_msg.edit_text("☁️ Uploading to Google Drive...")
        
        # Upload to Google Drive with progress
        service = get_gdrive_service()
        file_metadata = {'name': file_name}
        if GDRIVE_FOLDER_ID:
            file_metadata['parents'] = [GDRIVE_FOLDER_ID]
        
        # Use resumable upload with progress tracking
        media = MediaFileUpload(file_path, resumable=True, chunksize=UPLOAD_CHUNK_SIZE)
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id,webViewLink',
            supportsAllDrives=True
        )
        
        # Execute upload with progress tracking
        response = None
        last_progress = 0
        last_upload_time = 0
        while response is None:
            status, response = file.next_chunk()
            if status:
                progress_percent = int(status.progress() * 100)
                current_time = time.time()
                
                # Update when both 10% progress AND 30 seconds have passed
                if (progress_percent >= last_progress + 10) and (current_time - last_upload_time >= 30):
                    await status_msg.edit_text(f"☁️ Uploading to Google Drive... {progress_percent}%")
                    last_progress = progress_percent
                    last_upload_time = current_time
        
        # Delete local file
        os.remove(file_path)
        
        # Send success message
        await status_msg.edit_text(
            f"✅ File uploaded successfully!\n\n"
            f"📄 File Name: {file_name}\n"
            f"🔗 Link: {response.get('webViewLink')}"
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        if status_msg:
            await status_msg.edit_text(f"❌ Error: {str(e)}")
        else:
            await message.reply_text(f"❌ Error: {str(e)}")

if __name__ == "__main__":
    logger.info("🚀 Bot starting...")
    app.run()
