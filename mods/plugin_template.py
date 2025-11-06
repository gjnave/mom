"""
Plugin Template
This is a template showing all available hooks and how to create plugins.
Rename this file (remove the underscore) to activate it.
"""

import logging

# ============================================================================
# AVAILABLE HOOKS
# ============================================================================
# on_bot_ready(discord_client) - Called when bot starts
# on_message_received(message) - Called for every message, can return False to stop processing
# process_attachment(attachment, llm_accepts_images) - Process attachments, return dict with 'caption' and 'image_data'
# before_llm_call(messages, original_message) - Modify messages before sending to LLM
# after_llm_response(original_message, response_text) - Called after LLM responds

# ============================================================================
# CUSTOM COMMANDS
# ============================================================================
# Add custom commands to the 'commands' dictionary
# Each command receives (message, user_id) as parameters

async def hello_command(message, user_id):
    """Example custom command: !hello"""
    await message.channel.send(f"Hello <@{user_id}>! This is a custom command from a plugin!")

async def info_command(message, user_id):
    """Example custom command: !plugininfo"""
    await message.channel.send("This is an example plugin showing how to create custom commands!")

# Register your commands here
commands = {
    "hello": hello_command,
    "plugininfo": info_command,
}

# ============================================================================
# HOOK IMPLEMENTATIONS
# ============================================================================

async def on_bot_ready(discord_client):
    """
    Called when the bot is ready and connected to Discord.
    Use this for initialization tasks.
    """
    logging.info("Example plugin loaded successfully!")
    # You can access the discord client here if needed
    # print(f"Bot name: {discord_client.user.name}")

async def on_message_received(message):
    """
    Called for every message the bot receives.
    Return False to stop the bot from processing this message further.
    Return None or True to continue normal processing.
    """
    # Example: Log all messages
    # logging.info(f"Plugin saw message: {message.content}")
    
    # Example: Block messages containing certain words
    # if "blocked_word" in message.content.lower():
    #     await message.channel.send("That word is not allowed!")
    #     return False  # Stop processing
    
    return None  # Continue normal processing

async def process_attachment(attachment, llm_accepts_images):
    """
    Called when processing attachments.
    Return a dict with 'caption' and/or 'image_data' to add to the message.
    Return None to skip.
    """
    # Example: Add custom processing for specific file types
    # if attachment.filename.endswith('.pdf'):
    #     return {"caption": "This is a PDF file"}
    
    return None

async def before_llm_call(messages, original_message):
    """
    Called right before messages are sent to the LLM.
    You can modify the messages array here.
    Return the modified messages or None to keep original.
    """
    # Example: Add a custom system message
    # messages.insert(1, {"role": "system", "content": "Always be extra polite!"})
    
    # Example: Log the conversation
    # logging.info(f"Sending {len(messages)} messages to LLM")
    
    return messages

async def after_llm_response(original_message, response_text):
    """
    Called after the LLM generates a response.
    Use this for logging, analytics, or post-processing.
    """
    # Example: Log responses
    # logging.info(f"Bot responded with {len(response_text)} characters")
    
    # Example: Save interesting conversations
    # if len(response_text) > 500:
    #     with open("long_responses.txt", "a") as f:
    #         f.write(f"{response_text}\n\n")
    
    pass

# ============================================================================
# SETUP (REQUIRED)
# ============================================================================

def setup():
    """
    REQUIRED: This function must return a dict with plugin metadata.
    """
    return {
        "name": "Example Plugin",
        "version": "1.0",
        "description": "A template showing how to create plugins",
        "author": "Your Name Here"
    }