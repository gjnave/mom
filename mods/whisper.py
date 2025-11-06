"""
Whisper Transcription Plugin
Transcribes audio files using faster-whisper (more efficient than openai-whisper).
Usage: Upload file and say "transcribe" or "transcript" in the same message
Rename this file (remove the underscore) to activate it.
"""

import logging
import os
import asyncio
import tempfile
import subprocess
from pathlib import Path
from faster_whisper import WhisperModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Model size (tiny, base, small, medium, large)
WHISPER_MODEL = "base"
whisper_model = None

# Keywords that trigger transcription
TRIGGER_KEYWORDS = {"transcribe", "transcript", "translate this"}

# ============================================================================
# INITIALIZATION
# ============================================================================

async def on_bot_ready(discord_client):
    """Load the Whisper model when the bot starts."""
    global whisper_model
    try:
        logger.info(f"Loading Whisper model ({WHISPER_MODEL})...")
        whisper_model = WhisperModel(WHISPER_MODEL, device="auto", compute_type="auto")
        logger.info("✓ Whisper model loaded successfully!")
    except Exception as e:
        logger.error(f"Failed to load Whisper model: {e}")

# ============================================================================
# HOOKS
# ============================================================================

async def on_message_received(message):
    """
    Checks if a message contains a transcription request and an audio/video file.
    """
    if whisper_model is None:
        return

    message_lower = message.content.lower()
    if not any(keyword in message_lower for keyword in TRIGGER_KEYWORDS):
        return

    audio_extensions = {'.mp3', '.wav', '.m4a', '.ogg', '.flac', '.webm', '.aac', '.wma'}
    video_extensions = {'.mp4', '.mkv', '.mov', '.avi', '.flv', '.wmv', '.webm', '.m4v', '.mts', '.m2ts'}
    
    has_audio_video = any(
        Path(att.filename).suffix.lower() in (audio_extensions | video_extensions)
        for att in message.attachments
    )

    if has_audio_video:
        logger.info("Transcription request detected, passing to transcribe_command")
        await transcribe_command(message, str(message.author.id))
        return False  # Stop further processing of the message

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def transcribe_audio_file(file_path, filename):
    """Transcribe an audio or video file using faster-whisper."""
    audio_extensions = {'.mp3', '.wav', '.m4a', '.ogg', '.flac', '.webm', '.aac', '.wma'}
    video_extensions = {'.mp4', '.mkv', '.mov', '.avi', '.flv', '.wmv', '.webm', '.m4v', '.mts', '.m2ts'}
    file_ext = Path(filename).suffix.lower()
    
    is_video = file_ext in video_extensions
    audio_path = file_path
    
    try:
        # If it's a video, extract audio using ffmpeg
        if is_video:
            logger.info(f"Extracting audio from video: {filename}")
            audio_path = file_path.replace(file_ext, ".wav")
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["ffmpeg", "-i", file_path, "-q:a", "9", "-n", audio_path],
                    capture_output=True,
                    check=True
                )
            )
        
        logger.info(f"Transcribing: {filename}")
        
        # Run Whisper transcription in a thread pool
        loop = asyncio.get_event_loop()
        segments, info = await loop.run_in_executor(
            None,
            lambda: whisper_model.transcribe(audio_path, language="en")
        )
        
        # Combine all segments into one string
        transcription = " ".join(segment.text for segment in segments).strip()
        
        # Clean up temporary files
        try:
            os.unlink(file_path)
            if audio_path != file_path:
                os.unlink(audio_path)
        except:
            pass
        
        return transcription
        
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise

# ============================================================================
# CUSTOM COMMANDS
# ============================================================================

async def transcribe_command(message, user_id):
    """Handle transcription of audio/video files."""
    
    if whisper_model is None:
        await message.channel.send("❌ Whisper model is not loaded yet")
        return
    
    # Check for audio/video attachments
    audio_extensions = {'.mp3', '.wav', '.m4a', '.ogg', '.flac', '.webm', '.aac', '.wma'}
    video_extensions = {'.mp4', '.mkv', '.mov', '.avi', '.flv', '.wmv', '.webm', '.m4v', '.mts', '.m2ts'}
    
    audio_video_files = []
    for att in message.attachments:
        file_ext = Path(att.filename).suffix.lower()
        is_audio = file_ext in audio_extensions or (att.content_type and att.content_type.startswith('audio/'))
        is_video = file_ext in video_extensions or (att.content_type and att.content_type.startswith('video/'))
        
        if is_audio or is_video:
            audio_video_files.append(att)
    
    if not audio_video_files:
        await message.channel.send("❌ No audio or video files found")
        return
    
    # Process each file
    for att in audio_video_files:
        try:
            processing_msg = await message.channel.send(f"⏳ **Processing:** `{att.filename}`...")
            
            file_ext = Path(att.filename).suffix.lower()
            with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as tmp_file:
                tmp_path = tmp_file.name
                await att.save(tmp_path)
            
            transcription = await transcribe_audio_file(tmp_path, att.filename)
            
            if not transcription:
                await processing_msg.edit(content=f"🔇 No speech detected in `{att.filename}`")
            else:
                await processing_msg.delete()
                result_text = f"🎤 **Transcription of `{att.filename}`:**\n\n{transcription}"
                if len(result_text) > 1900:
                    await message.channel.send(result_text[:1900] + "...")
                else:
                    await message.channel.send(result_text)
        
        except Exception as e:
            logger.error(f"Error transcribing {att.filename}: {e}", exc_info=True)
            try:
                await processing_msg.delete()
            except:
                pass
            await message.channel.send(f"❌ Error transcribing `{att.filename}`: {str(e)}")

commands = {
    "transcribe": transcribe_command,
}

# ============================================================================
# SETUP (REQUIRED)
# ============================================================================

def setup():
    """REQUIRED: Return plugin metadata."""
    return {
        "name": "Whisper Transcription",
        "version": "3.1",
        "description": "Transcribes audio/video files using faster-whisper",
        "author": "Your Name Here"
    }
