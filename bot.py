import os
import logging
import time
from pyrogram import Client, filters
from pyrogram.types import Message
from google.oauth2.credentials import Credentials
import json
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, HttpError
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
    """Get Google Drive service instance using Service Account"""
    # Check for Service Account JSON in environment variable
    service_account_json = os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON')
    
    if not service_account_json:
        if not os.path.exists('credentials.json'):
            raise FileNotFoundError(
                "Error: Missing Google credentials! Set GDRIVE_SERVICE_ACCOUNT_JSON env var or provide credentials.json"
            )
        # Fallback to credentials.json if it exists
        with open('credentials.json', 'r') as f:
            service_account_json = f.read()
    
    # Parse the Service Account JSON
    try:
        service_account_info = json.loads(service_account_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in GDRIVE_SERVICE_ACCOUNT_JSON: {e}")
    
    # Create credentials from service account info
    creds = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=SCOPES
    )
    
    return build('drive', 'v3', credentials=creds)

async def safe_edit_message(message, text):
    """Safely edit message, ignoring MESSAGE_NOT_MODIFIED errors"""
    try:
        await message.edit_text(text)
        logger.debug(f"Message edited successfully")
    except Exception as e:
        error_msg = str(e).lower()
        # Ignore MESSAGE_NOT_MODIFIED errors
        if "message is not modified" in error_msg or "400" in error_msg:
            logger.debug(f"Message not modified (expected): {e}")
        else:
            logger.warning(f"Failed to edit message: {type(e).__name__}: {e}")

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
        
        new_text = (
            f"\u23ec Downloading file...\n"
            f"\ud83d\udcca Progress: {progress_percent}%\n"
            f"\ud83d\udcbe {speed_mb:.1f} MB / {total_mb:.1f} MB"
        )
        await safe_edit_message(status_msg, new_text)
        
        # Update last update time
        last_update_time[message_id] = current_time
        
    except Exception:
        # Silently ignore all errors including FloodWait
        pass

@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    await message.reply_text(
        "\ud83d\udc4b Hello! I'm a Google Drive Upload Bot.\n\n"
        "\ud83d\udcce Send me any file and I'll upload it to your Google Drive!\n\n"
        "Commands:\n"
        "/start - Show this message\n"
        "/help - Get help"
    )

@app.on_message(filters.command("help"))
async def help_command(client: Client, message: Message):
    await message.reply_text(
        "\ud83d\udcda How to use:\n\n"
        "1. Send me any document/file\n"
        "2. I'll download it temporarily\n"
        "3. Upload it to your Google Drive\n"
        "4. Send you the link\n\n"
        "\u2728 Simple as that!"
    )

@app.on_message(filters.document | filters.video | filters.audio | filters.photo)
async def handle_file(client: Client, message: Message):
    status_msg = None
    try:
        # Send status message
        status_msg = await message.reply_text("\u23ec Downloading file...")
        
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
        
        await safe_edit_message(status_msg, "\u2601\ufe0f Uploading to Google Drive...")
        
        # Upload to Google Drive with progress
        try:
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
                        new_text = f"\u2601\ufe0f Uploading to Google Drive... {progress_percent}%"
                        await safe_edit_message(status_msg, new_text)
                        last_progress = progress_percent
                        last_upload_time = current_time
            
            # Delete local file
            os.remove(file_path)
            
            # Send success message
            success_text = (
                f"\u2705 File uploaded successfully!\n\n"
                f"\ud83d\udcc4 File Name: {file_name}\n"
                f"\ud83d\udd17 Link: {response.get('webViewLink')}"
            )
            await safe_edit_message(status_msg, success_text)
            
        except HttpError as http_err:
            # Handle Google Drive API errors specifically
            status_code = http_err.resp.status
            error_content = http_err.content.decode('utf-8') if isinstance(http_err.content, bytes) else str(http_err.content)
            
            # Parse error message
            if status_code == 403:
                error_msg = "Permission denied. Check if the service account has access to the Google Drive folder."
            elif status_code == 404:
                error_msg = f"Google Drive folder not found. Check folder ID: {GDRIVE_FOLDER_ID}"
            elif status_code == 400:
                error_msg = f"Invalid request to Google Drive. Check folder ID format."
            else:
                error_msg = f"Google Drive API error (HTTP {status_code})"
            
            error_text = f"\u274c Error: {error_msg}"
            logger.error(f"Google Drive HTTP Error {status_code}: {error_content}")
            
            try:
                await safe_edit_message(status_msg, error_text)
            except Exception as edit_error:
                logger.error(f"Failed to send error message: {edit_error}")
                try:
                    await message.reply_text(error_text)
                except Exception as reply_error:
                    logger.error(f"Failed to reply with error: {reply_error}")
                    
    except Exception as e:
        error_details = f"{type(e).__name__}: {str(e)}"
        logger.error(f"Error during file handling: {error_details}")
        
        if status_msg:
            error_text = f"\u274c Error: {error_details}"
            try:
                await safe_edit_message(status_msg, error_text)
            except Exception as edit_error:
                logger.error(f"Failed to send error message: {edit_error}")
                try:
                    await message.reply_text(error_text)
                except Exception as reply_error:
                    logger.error(f"Failed to reply with error: {reply_error}")
        else:
            error_text = f"\u274c Error: {error_details}"
            try:
                await message.reply_text(error_text)
            except Exception as reply_error:
                logger.error(f"Failed to reply with error: {reply_error}")

if __name__ == "__main__":
    logger.info("\ud83d\ude80 Bot starting...")
    app.run()
