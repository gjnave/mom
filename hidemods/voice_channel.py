"""
Voice Channel Plugin
Makes the bot join voice channels and speak responses using TTS
Works together with the voice_tts plugin
"""

import asyncio
import logging
import discord
import os

# ============================================================================
# CONFIGURATION
# ============================================================================

ENABLE_VOICE_CHANNEL = True

# Auto-join settings
AUTO_JOIN_ON_MENTION = True  # Join voice when mentioned if user is in voice
AUTO_LEAVE_TIMEOUT = 300  # Leave after 5 minutes of inactivity (seconds)

# Voice settings
SPEAK_IN_VOICE_CHANNEL = True  # Speak responses in voice channel
ALSO_SEND_TEXT = True  # Also send text response in text channel

# Logging
LOG_LEVEL = 2  # 0=Silent, 1=Minimal, 2=Normal, 3=Detailed, 4=Verbose

# ============================================================================
# GLOBALS
# ============================================================================

voice_clients = {}  # guild_id -> voice_client
last_activity = {}  # guild_id -> timestamp
audio_queue = {}  # guild_id -> queue of audio files to play

def log(level, message, *args):
    """Custom logging function"""
    if LOG_LEVEL >= level:
        logging.info(message, *args)

def log_error(message, *args):
    logging.error(message, *args)

# ============================================================================
# VOICE CHANNEL MANAGEMENT
# ============================================================================

async def join_voice_channel(channel: discord.VoiceChannel):
    """Join a voice channel"""
    guild_id = channel.guild.id
    
    try:
        # Check if already connected
        if guild_id in voice_clients and voice_clients[guild_id].is_connected():
            # Move to new channel if different
            if voice_clients[guild_id].channel.id != channel.id:
                log(2, f"Moving to voice channel: {channel.name}")
                await voice_clients[guild_id].move_to(channel)
            return voice_clients[guild_id]
        
        # Connect to voice channel
        log(2, f"Joining voice channel: {channel.name}")
        voice_client = await channel.connect()
        voice_clients[guild_id] = voice_client
        last_activity[guild_id] = asyncio.get_event_loop().time()
        audio_queue[guild_id] = asyncio.Queue()
        
        # Start audio playback task
        asyncio.create_task(audio_playback_task(guild_id))
        
        return voice_client
        
    except Exception as e:
        log_error(f"Failed to join voice channel: {e}")
        return None

async def leave_voice_channel(guild_id: int):
    """Leave a voice channel"""
    if guild_id in voice_clients:
        try:
            log(2, f"Leaving voice channel in guild {guild_id}")
            await voice_clients[guild_id].disconnect()
        except:
            pass
        finally:
            if guild_id in voice_clients:
                del voice_clients[guild_id]
            if guild_id in last_activity:
                del last_activity[guild_id]
            if guild_id in audio_queue:
                del audio_queue[guild_id]

async def audio_playback_task(guild_id: int):
    """Task that plays audio files from queue"""
    log(3, f"Audio playback task started for guild {guild_id}")
    
    while guild_id in voice_clients:
        try:
            voice_client = voice_clients[guild_id]
            
            if not voice_client.is_connected():
                log(2, "Voice client disconnected, stopping playback task")
                break
            
            # Wait for audio file
            try:
                audio_path = await asyncio.wait_for(
                    audio_queue[guild_id].get(),
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            
            if audio_path is None:  # Stop signal
                break
            
            log(3, f"Playing audio: {os.path.basename(audio_path)}")
            
            # Play audio
            audio_source = discord.FFmpegPCMAudio(audio_path)
            voice_client.play(audio_source)
            
            # Wait for playback to finish
            while voice_client.is_playing():
                await asyncio.sleep(0.1)
            
            log(3, "Audio playback finished")
            
            # Clean up audio file
            try:
                await asyncio.sleep(0.5)  # Give it a moment
                os.unlink(audio_path)
                log(4, f"Deleted audio file: {audio_path}")
            except Exception as e:
                log(3, f"Failed to delete audio file: {e}")
            
            # Update activity
            last_activity[guild_id] = asyncio.get_event_loop().time()
            
        except Exception as e:
            log_error(f"Error in audio playback task: {e}")
            await asyncio.sleep(1)
    
    log(3, f"Audio playback task ended for guild {guild_id}")

async def play_audio_in_voice(guild_id: int, audio_path: str):
    """Add audio file to playback queue"""
    if guild_id in audio_queue:
        await audio_queue[guild_id].put(audio_path)
        log(2, "Audio queued for playback")
        return True
    return False

# ============================================================================
# INACTIVITY CHECKER
# ============================================================================

async def inactivity_checker():
    """Check for inactive voice connections and disconnect"""
    while True:
        try:
            await asyncio.sleep(30)  # Check every 30 seconds
            
            current_time = asyncio.get_event_loop().time()
            
            for guild_id in list(voice_clients.keys()):
                if guild_id in last_activity:
                    inactive_time = current_time - last_activity[guild_id]
                    
                    if inactive_time > AUTO_LEAVE_TIMEOUT:
                        log(2, f"Voice client inactive for {int(inactive_time)}s, leaving")
                        await leave_voice_channel(guild_id)
                        
        except Exception as e:
            log_error(f"Error in inactivity checker: {e}")

# ============================================================================
# CUSTOM COMMANDS
# ============================================================================

async def join_command(message, user_id):
    """Join the user's voice channel: !join"""
    if not ENABLE_VOICE_CHANNEL:
        await message.channel.send("Voice channel support is disabled, sweetie!")
        return
    
    # Check if user is in a voice channel
    if not message.author.voice or not message.author.voice.channel:
        await message.channel.send("You need to be in a voice channel first, honey!")
        return
    
    voice_channel = message.author.voice.channel
    voice_client = await join_voice_channel(voice_channel)
    
    if voice_client:
        await message.channel.send(f"Joined **{voice_channel.name}**, dear! 🎤")
    else:
        await message.channel.send("Couldn't join the voice channel, sweetie!")

async def leave_command(message, user_id):
    """Leave the voice channel: !leave"""
    guild_id = message.guild.id
    
    if guild_id not in voice_clients:
        await message.channel.send("I'm not in a voice channel, honey!")
        return
    
    await leave_voice_channel(guild_id)
    await message.channel.send("Left the voice channel, dear!")

commands = {
    "join": join_command,
    "leave": leave_command,
}

# ============================================================================
# HOOKS
# ============================================================================

async def on_bot_ready(discord_client):
    """Start inactivity checker when bot is ready"""
    if ENABLE_VOICE_CHANNEL:
        log(1, "Voice Channel plugin ready!")
        asyncio.create_task(inactivity_checker())

async def on_message_received(message):
    """Auto-join voice channel if mentioned"""
    if not ENABLE_VOICE_CHANNEL or not AUTO_JOIN_ON_MENTION:
        return None
    
    # Check if bot was mentioned
    if message.guild and message.guild.me in message.mentions:
        # Check if user is in voice channel
        if message.author.voice and message.author.voice.channel:
            guild_id = message.guild.id
            
            # Only join if not already connected
            if guild_id not in voice_clients:
                log(2, "Bot mentioned by user in voice channel, auto-joining")
                await join_voice_channel(message.author.voice.channel)
    
    return None  # Continue normal processing

async def after_llm_response(original_message, response_text):
    """Play TTS response in voice channel if connected"""
    if not ENABLE_VOICE_CHANNEL or not SPEAK_IN_VOICE_CHANNEL:
        return
    
    guild_id = original_message.guild.id if original_message.guild else None
    
    if not guild_id or guild_id not in voice_clients:
        return  # Not in a voice channel
    
    # Check if voice_tts plugin generated audio
    # We'll look for the most recent .wav file in voice_cache
    try:
        import glob
        voice_cache_dir = "voice_cache"
        
        if not os.path.exists(voice_cache_dir):
            return
        
        # Get most recent wav file
        wav_files = glob.glob(os.path.join(voice_cache_dir, "*.wav"))
        if not wav_files:
            return
        
        # Find the newest file
        newest_file = max(wav_files, key=os.path.getctime)
        
        # Check if it's very recent (within last 2 seconds)
        file_age = asyncio.get_event_loop().time() - os.path.getctime(newest_file)
        if file_age < 2.0:
            log(2, f"Playing TTS response in voice channel")
            await play_audio_in_voice(guild_id, newest_file)
        
    except Exception as e:
        log_error(f"Error playing audio in voice: {e}")

# ============================================================================
# SETUP
# ============================================================================

def setup():
    """Plugin setup"""
    
    # Check if FFmpeg is available
    import shutil
    if not shutil.which("ffmpeg"):
        log_error("⚠ FFmpeg not found! Voice playback will not work.")
        log_error("Download from: https://ffmpeg.org/download.html")
        log_error("Make sure ffmpeg.exe is in your PATH or same folder as bot")
    
    return {
        "name": "Voice Channel Support",
        "version": "1.0",
        "description": "Join voice channels and speak responses using TTS",
        "author": "Your Company"
    }