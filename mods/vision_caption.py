import asyncio
import base64
import logging
import os
import requests
import tempfile

# IMAGE CAPTIONING CONFIGURATION
LLAMA_CPP_PATH = r"E:\temp\llama.cpp\build\bin\llama-mtmd-cli.exe"
VISION_MODEL_PATH = r"E:\models\joycaption\llama3-joycaption-alpha-two-vqa-test-1-Q6_K.gguf"
MMPROJ_PATH = r"E:\models\joycaption\mmproj\llama3-joycaption-alpha-two-vqa-test-1-mmproj-model-F16.gguf"
ENABLE_IMAGE_CAPTIONING = True  # Set to False to disable
CAPTION_PROMPT = "Describe this image in detailed language."

# LOGGING CONFIGURATION
# 0 = SILENT    - No logging at all
# 1 = MINIMAL   - Only errors and critical info
# 2 = NORMAL    - Standard operation info (recommended)
# 3 = DETAILED  - Includes process details and timings
# 4 = VERBOSE   - Everything including data previews
LOG_LEVEL = 2

def log(level, message, *args):
    """Custom logging function that respects LOG_LEVEL"""
    if LOG_LEVEL >= level:
        logging.info(message, *args)

def log_error(message, *args):
    """Always log errors regardless of LOG_LEVEL"""
    logging.error(message, *args)

def validate_paths():
    """Check if all required files exist"""
    if not ENABLE_IMAGE_CAPTIONING:
        log(2, "Image captioning is disabled in config")
        return False
    
    missing = []
    if not os.path.exists(LLAMA_CPP_PATH):
        missing.append(f"Llama.cpp executable: {LLAMA_CPP_PATH}")
    if not os.path.exists(VISION_MODEL_PATH):
        missing.append(f"Vision model: {VISION_MODEL_PATH}")
    if not os.path.exists(MMPROJ_PATH):
        missing.append(f"MMProj model: {MMPROJ_PATH}")
    
    if missing:
        log_error("Vision Caption Plugin - Missing required files:")
        for item in missing:
            log_error(f"  - {item}")
        return False
    return True

async def caption_image(image_path: str, prompt: str = CAPTION_PROMPT) -> str:
    """Generate a caption for an image using Joycaption model"""
    log(3, f"Starting caption generation for: {image_path}")
    
    if not validate_paths():
        log_error("Path validation failed, skipping caption")
        return None
    
    cmd = [
        LLAMA_CPP_PATH,
        "--model", VISION_MODEL_PATH,
        "--mmproj", MMPROJ_PATH,
        "--image", image_path,
        "--prompt", prompt
    ]
    
    log(3, f"Running vision model command")
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        log(3, "Waiting for vision model to process image...")
        stdout, stderr = await process.communicate()
        
        stdout_text = stdout.decode().strip()
        stderr_text = stderr.decode().strip()
        
        log(4, f"Vision model return code: {process.returncode}")
        if stderr_text and LOG_LEVEL >= 4:
            log(4, f"Vision model stderr: {stderr_text[:500]}")
        
        if process.returncode != 0:
            log_error(f"Vision model failed with return code {process.returncode}")
            log_error(f"stderr: {stderr_text}")
            return None
        
        if not stdout_text:
            log_error("Vision model returned empty output")
            return None
        
        # The actual output is the last line, after the prompt
        caption = stdout_text.split('\n')[-1].strip()
        
        # Remove the prompt from the output if it's included
        if prompt in caption:
            caption = caption.split(prompt, 1)[-1].strip()
        
        log(2, f"✓ Generated caption ({len(caption)} chars): {caption[:100]}...")
        log(4, f"Full caption: {caption}")
        return caption
        
    except Exception as e:
        log_error(f"Exception while running vision model: {type(e).__name__}: {e}")
        if LOG_LEVEL >= 3:
            import traceback
            log_error(f"Traceback: {traceback.format_exc()}")
        return None

async def process_attachment(attachment, llm_accepts_images, message=None):
    """Process image attachments with AI captioning"""
    log(3, f"process_attachment called for: {attachment.filename}")
    
    if not ENABLE_IMAGE_CAPTIONING:
        log(3, "Image captioning disabled, returning None")
        return None
    
    if not attachment.content_type or "image" not in attachment.content_type:
        log(3, f"Not an image attachment: {attachment.content_type}")
        return None
    
    log(2, f"Processing image: {attachment.filename} ({attachment.size} bytes)")
    
    processing_msg = None
    if message:
        try:
            processing_msg = await message.channel.send(f"⏳ **Processing Image:** `{attachment.filename}`...")
        except Exception as e:
            log_error(f"Failed to send processing message: {e}")

    try:
        # Download image
        image_data = requests.get(attachment.url).content
        base64_data = base64.b64encode(image_data).decode('utf-8')
        
        log(3, f"Image downloaded successfully, size: {len(base64_data)} chars (base64)")
        log(4, f"Base64 preview: {base64_data[:100]}...")
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(image_data)
            tmp_path = tmp.name
        
        log(3, f"Temp file created: {tmp_path}")
        
        try:
            caption = await caption_image(tmp_path)
        finally:
            # Clean up temp file
            try:
                os.unlink(tmp_path)
                log(4, f"Temp file deleted: {tmp_path}")
            except Exception as e:
                log_error(f"Failed to delete temp file {tmp_path}: {e}")
        
        if caption:
            log(2, f"✓ Caption generated successfully")
        else:
            log(1, f"⚠ Caption generation failed, returning image data only")

        if processing_msg:
            try:
                await processing_msg.delete()
            except Exception as e:
                log_error(f"Failed to delete processing message: {e}")
        
        # Return result even if caption failed (still send the image)
        return {
            "caption": caption,
            "image_data": base64_data
        }
    
    except Exception as e:
        log_error(f"Error in process_attachment: {type(e).__name__}: {e}")
        if LOG_LEVEL >= 3:
            import traceback
            log_error(f"Traceback: {traceback.format_exc()}")
        
        if processing_msg:
            try:
                await processing_msg.delete()
            except Exception as e:
                log_error(f"Failed to delete processing message: {e}")
        return None

def setup():
    """Plugin setup function"""
    
    # Log level descriptions
    level_names = {
        0: "SILENT",
        1: "MINIMAL",
        2: "NORMAL",
        3: "DETAILED",
        4: "VERBOSE"
    }
    
    log(1, f"Vision Caption Plugin - Logging level: {level_names.get(LOG_LEVEL, 'UNKNOWN')} ({LOG_LEVEL})")
    
    if ENABLE_IMAGE_CAPTIONING:
        if validate_paths():
            log(1, "✓ Vision Caption plugin: All required files found")
            log(2, f"  Executable: {os.path.basename(LLAMA_CPP_PATH)}")
            log(2, f"  Model: {os.path.basename(VISION_MODEL_PATH)}")
            log(2, f"  MMProj: {os.path.basename(MMPROJ_PATH)}")
            log(3, f"  Full paths validated:")
            log(3, f"    Exe: {LLAMA_CPP_PATH}")
            log(3, f"    Model: {VISION_MODEL_PATH}")
            log(3, f"    MMProj: {MMPROJ_PATH}")
        else:
            log_error("⚠ Vision Caption plugin loaded but missing required files - captioning disabled")
    else:
        log(1, "Vision Caption plugin loaded but disabled in config")
    
    return {
        "name": "Vision Caption (Joycaption)",
        "version": "1.1",
        "description": "AI-powered image understanding using Joycaption model",
        "author": "Your Company"
    }