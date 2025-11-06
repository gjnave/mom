"""
Voice TTS Plugin using Piper TTS
Converts bot responses to speech and sends as voice messages in Discord
Works with the Piper installation you have!
"""

import asyncio
import logging
import os
import subprocess
import tempfile
import discord
import json
import requests
import re

# ============================================================================
# CONFIGURATION
# ============================================================================

# Enable/disable voice responses
ENABLE_VOICE = True

# Piper TTS settings (based on your folder structure)
PIPER_EXE_PATH = r"D:\discordbot\piper-tts\piper.exe"
PIPER_DATA_PATH = r"D:\discordbot\piper-tts\espeak-ng-data"

# Voice model settings - will auto-download if not present
DEFAULT_VOICE = "en_US-amy-medium"  # Recommended female voice
VOICES_DIR = r"D:\discordbot\piper-tts\voices"  # Where to store voice models

# Available voices to download:
# Female: en_US-amy-medium, en_US-lessac-medium (male), en_GB-alba-medium (British)
# Fast: en_US-amy-low, en_US-danny-low
# More at: https://github.com/rhasspy/piper/blob/master/VOICES.md

# Voice settings
VOICE_LENGTH_SCALE = 1.0  # Affects speaking rate (0.5-2.0)
VOICE_NOISE_SCALE = 0.667  # Voice variation
VOICE_NOISE_W = 0.8  # Phoneme duration variation

# Trigger settings - when should the bot speak?
SPEAK_ON_MENTION = True  # Speak when @mentioned
SPEAK_ON_COMMAND = True  # Speak when !voice command is used
MAX_TEXT_LENGTH = 500  # Don't voice responses longer than this

# Performance settings
VOICE_CACHE_DIR = "voice_cache"  # Cache generated audio

# Logging
LOG_LEVEL = 2  # 0=Silent, 1=Minimal, 2=Normal, 3=Detailed, 4=Verbose

# ============================================================================
# GLOBALS
# ============================================================================

piper_available = False
voice_enabled_users = set()
current_voice_model = None
current_voice_config = None

def log(level, message, *args):
    """Custom logging function"""
    if LOG_LEVEL >= level:
        logging.info(message, *args)

def log_error(message, *args):
    logging.error(message, *args)

# ============================================================================
# TEXT SANITIZATION FOR TTS
# ============================================================================

def sanitize_text_for_tts(text):
    """
    Remove or replace characters that can't be spoken or cause encoding issues
    This fixes the emoji encoding error!
    """
    # Remove emoji and other non-ASCII characters
    # Keep only printable ASCII characters and common punctuation
    text = text.encode('ascii', 'ignore').decode('ascii')
    
    # Clean up extra whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

# ============================================================================
# VOICE MANAGEMENT
# ============================================================================

def download_voice_model(voice_name):
    """Download a Piper voice model from HuggingFace"""
    base_url = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
    
    # Parse voice name: en_US-kristin-medium
    # Format: lang_COUNTRY-speaker-quality
    parts = voice_name.split('-')
    if len(parts) != 3:
        log_error(f"Invalid voice name format: {voice_name}")
        return None, None
    
    lang_code = parts[0]  # en_US
    speaker = parts[1]     # kristin
    quality = parts[2]     # medium
    lang_short = lang_code.split('_')[0]  # en
    
    # Build URL: en/en_US/kristin/medium/en_US-kristin-medium.onnx
    model_url = f"{base_url}/{lang_short}/{lang_code}/{speaker}/{quality}/{voice_name}.onnx"
    config_url = f"{base_url}/{lang_short}/{lang_code}/{speaker}/{quality}/{voice_name}.onnx.json"
    
    os.makedirs(VOICES_DIR, exist_ok=True)
    
    model_path = os.path.join(VOICES_DIR, f"{voice_name}.onnx")
    config_path = os.path.join(VOICES_DIR, f"{voice_name}.onnx.json")
    
    try:
        log(2, f"Downloading voice model: {voice_name}...")
        
        # Download model (.onnx file)
        log(3, f"Downloading model from {model_url}")
        response = requests.get(model_url, stream=True, timeout=60)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(model_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if LOG_LEVEL >= 2 and total_size > 0:
                    progress = (downloaded / total_size) * 100
                    if int(progress) % 10 == 0:  # Log every 10%
                        log(2, f"  Progress: {int(progress)}%")
        
        # Download config (.json file)
        log(3, f"Downloading config from {config_url}")
        response = requests.get(config_url, timeout=30)
        response.raise_for_status()
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(response.text)
        
        log(2, f"✓ Downloaded voice model to {model_path}")
        return model_path, config_path
        
    except Exception as e:
        log_error(f"Failed to download voice model: {e}")
        return None, None

def find_or_download_voice(voice_name):
    """Find voice model or download if not present"""
    model_path = os.path.join(VOICES_DIR, f"{voice_name}.onnx")
    config_path = os.path.join(VOICES_DIR, f"{voice_name}.onnx.json")
    
    if os.path.exists(model_path) and os.path.exists(config_path):
        log(2, f"✓ Voice model found: {voice_name}")
        return model_path, config_path
    
    log(2, f"Voice model not found, downloading: {voice_name}")
    return download_voice_model(voice_name)

# ============================================================================
# PIPER SETUP
# ============================================================================

def init_piper():
    """Initialize Piper TTS"""
    global piper_available, current_voice_model, current_voice_config
    
    if not ENABLE_VOICE:
        log(1, "Voice TTS is disabled in config")
        return False
    
    try:
        log(2, "Initializing Piper TTS...")
        
        # Check if Piper executable exists
        if not os.path.exists(PIPER_EXE_PATH):
            log_error(f"Piper executable not found: {PIPER_EXE_PATH}")
            return False
        
        # Create voices directory
        os.makedirs(VOICES_DIR, exist_ok=True)
        
        # Test Piper
        log(3, "Testing Piper installation...")
        result = subprocess.run(
            [PIPER_EXE_PATH, "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode != 0:
            log_error(f"Piper test failed: {result.stderr}")
            return False
        
        log(1, f"✓ Piper TTS ready: {result.stdout.strip()}")
        
        # Load or download default voice
        current_voice_model, current_voice_config = find_or_download_voice(DEFAULT_VOICE)
        
        if not current_voice_model:
            log_error("Failed to load default voice")
            return False
        
        log(2, f"  Voice: {DEFAULT_VOICE}")
        piper_available = True
        
        # Create cache directory
        os.makedirs(VOICE_CACHE_DIR, exist_ok=True)
        return True
            
    except Exception as e:
        log_error(f"Failed to initialize Piper: {e}")
        import traceback
        log_error(traceback.format_exc())
        return False

async def generate_speech(text: str) -> str:
    """Generate speech from text and return file path"""
    global piper_available, current_voice_model, current_voice_config
    
    if not piper_available or not current_voice_model:
        log_error("Piper not available")
        return None
    
    try:
        log(3, f"Generating speech for text ({len(text)} chars)")
        
        # Create temporary output file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav", dir=VOICE_CACHE_DIR) as tmp:
            output_path = tmp.name
        
        # Clean text for TTS
        clean_text = text.strip()
        
        # Remove Discord mentions and formatting
        clean_text = re.sub(r'<@!?\d+>', '', clean_text)
        clean_text = re.sub(r'<#\d+>', '', clean_text)
        clean_text = re.sub(r'<:\w+:\d+>', '', clean_text)
        clean_text = clean_text.replace('*', '').replace('_', '').replace('`', '')
        clean_text = clean_text.replace('||', '')
        
        # CRITICAL FIX: Remove emoji and non-ASCII characters
        clean_text = sanitize_text_for_tts(clean_text)
        
        if not clean_text:
            log(2, "Empty text after cleaning, skipping TTS")
            return None
        
        log(4, f"Cleaned text: {clean_text[:100]}...")
        
        # Build Piper command
        cmd = [
            PIPER_EXE_PATH,
            "--model", current_voice_model,
            "--config", current_voice_config,
            "--output_file", output_path,
            "--length_scale", str(VOICE_LENGTH_SCALE),
            "--noise_scale", str(VOICE_NOISE_SCALE),
            "--noise_w", str(VOICE_NOISE_W),
        ]
        
        # Run Piper with UTF-8 encoding
        def run_piper():
            process = subprocess.run(
                cmd,
                input=clean_text,
                text=True,
                encoding='utf-8',  # Explicitly use UTF-8
                capture_output=True,
                timeout=30,
                env={**os.environ, "PIPER_PHONEMIZE_ESPEAK_DATA": PIPER_DATA_PATH}
            )
            if process.returncode != 0:
                raise Exception(f"Piper failed: {process.stderr}")
            return output_path
        
        output_path = await asyncio.to_thread(run_piper)
        
        log(2, f"✓ Speech generated: {os.path.basename(output_path)}")
        return output_path
        
    except Exception as e:
        log_error(f"Error generating speech: {e}")
        if LOG_LEVEL >= 3:
            import traceback
            log_error(traceback.format_exc())
        return None

# ============================================================================
# CUSTOM COMMANDS
# ============================================================================

async def voice_command(message, user_id):
    """Toggle voice responses for a user: !voice"""
    global voice_enabled_users, piper_available
    
    if not ENABLE_VOICE or not piper_available:
        await message.channel.send("Voice TTS is not available, sweetie!")
        return
    
    if user_id in voice_enabled_users:
        voice_enabled_users.remove(user_id)
        await message.channel.send("Voice responses disabled for you, honey!")
    else:
        voice_enabled_users.add(user_id)
        await message.channel.send("Voice responses enabled for you, dear!")
        
        # Send a test voice message
        test_path = await generate_speech("Voice enabled! I'll speak my responses to you now, sweetie!")
        if test_path:
            try:
                await message.channel.send(file=discord.File(test_path, "test.wav"))
                await asyncio.sleep(1)
                os.unlink(test_path)
            except Exception as e:
                log_error(f"Failed to send test voice: {e}")

async def speak_command(message, user_id):
    """Make the bot speak specific text: !speak <text>"""
    global piper_available
    
    if not ENABLE_VOICE or not piper_available:
        await message.channel.send("Voice TTS is not available, sweetie!")
        return
    
    text = message.content[len("!speak"):].strip()
    if not text:
        await message.channel.send("Usage: !speak <text to speak>, honey!")
        return
    
    if len(text) > MAX_TEXT_LENGTH:
        await message.channel.send(f"Text too long! Keep it under {MAX_TEXT_LENGTH} characters, dear.")
        return
    
    audio_path = await generate_speech(text)
    if audio_path:
        try:
            await message.channel.send(file=discord.File(audio_path, "speech.wav"))
            await asyncio.sleep(1)
            os.unlink(audio_path)
        except Exception as e:
            log_error(f"Failed to send voice message: {e}")
            await message.channel.send("Oops, couldn't generate the voice, sweetie!")
    else:
        await message.channel.send("Couldn't generate speech, honey!")

async def setvoice_command(message, user_id):
    """Change the voice: !setvoice <voice_name>"""
    global current_voice_model, current_voice_config
    
    voice_name = message.content[len("!setvoice"):].strip()
    if not voice_name:
        current = os.path.basename(current_voice_model).replace('.onnx', '') if current_voice_model else "unknown"
        await message.channel.send(f"Current voice: **{current}**\nUsage: !setvoice <voice_name>\nExample: !setvoice en_US-lessac-medium")
        return
    
    # Try to load/download the voice
    model, config = await asyncio.to_thread(find_or_download_voice, voice_name)
    
    if model and config:
        current_voice_model = model
        current_voice_config = config
        await message.channel.send(f"Voice changed to **{voice_name}**, honey!")
        
        # Test new voice
        test_path = await generate_speech("Testing new voice!")
        if test_path:
            try:
                await message.channel.send(file=discord.File(test_path, "test.wav"))
                await asyncio.sleep(1)
                os.unlink(test_path)
            except:
                pass
    else:
        await message.channel.send(f"Couldn't load voice **{voice_name}**, sweetie! Check the name and try again.")

async def voices_command(message, user_id):
    """List available voices"""
    voices_info = """**Popular Piper Voices:**

**Female:**
• `en_US-amy-medium` (clear, recommended)
• `en_US-amy-low` (faster)
• `en_GB-alba-medium` (British)

**Male:**
• `en_US-lessac-medium` (authoritative)
• `en_US-danny-low` (casual, fast)
• `en_GB-alan-medium` (British)

Use `!setvoice <name>` to change!
More voices: https://rhasspy.github.io/piper-samples/"""
    
    await message.channel.send(voices_info)

commands = {
    "voice": voice_command,
    "speak": speak_command,
    "setvoice": setvoice_command,
    "voices": voices_command,
}

# ============================================================================
# HOOKS
# ============================================================================

async def on_bot_ready(discord_client):
    """Initialize TTS when bot starts"""
    log(1, "Voice TTS plugin starting...")
    
    success = await asyncio.to_thread(init_piper)
    
    if success:
        log(1, "✓ Voice TTS plugin ready!")
    else:
        log_error("⚠ Voice TTS plugin failed to initialize")

async def after_llm_response(original_message, response_text):
    """Generate voice after bot responds"""
    global piper_available, voice_enabled_users
    
    if not ENABLE_VOICE or not piper_available:
        return
    
    user_id = str(original_message.author.id)
    
    should_speak = False
    
    if SPEAK_ON_COMMAND and original_message.content.startswith("!voice"):
        return
    
    if user_id in voice_enabled_users:
        should_speak = True
    elif SPEAK_ON_MENTION:
        bot_user_id = original_message.guild.me.id if original_message.guild else None
        if bot_user_id:
            bot_mentioned = any(mention.id == bot_user_id for mention in original_message.mentions)
            should_speak = bot_mentioned
    
    if not should_speak:
        return
    
    if len(response_text) > MAX_TEXT_LENGTH:
        log(2, f"Response too long for voice ({len(response_text)} chars), skipping")
        return
    
    log(2, "Generating voice response...")
    
    audio_path = await generate_speech(response_text)
    
    if audio_path:
        try:
            await original_message.channel.send(file=discord.File(audio_path, "response.wav"))
            log(2, "✓ Voice message sent")
            
            await asyncio.sleep(1)
            try:
                os.unlink(audio_path)
            except Exception as e:
                log(3, f"Failed to delete temp file: {e}")
                
        except Exception as e:
            log_error(f"Failed to send voice message: {e}")

# ============================================================================
# SETUP
# ============================================================================

def setup():
    """Plugin setup"""
    return {
        "name": "Voice TTS (Piper)",
        "version": "1.2",
        "description": "Text-to-speech using Piper TTS with auto-downloading voices (emoji fix)",
        "author": "Your Company"
    }