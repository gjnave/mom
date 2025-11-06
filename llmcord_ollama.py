import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime as dt
import json
import logging
import requests
from typing import Optional
import re
import discord
import os
from datetime import datetime as dt
from openai import AsyncOpenAI


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

with open("config.json", "r") as file:
    config = {k: v for d in json.load(file).values() for k, v in d.items()}

LLM_ACCEPTS_IMAGES: bool = any(x in config["model"] for x in ("gpt-4o", "claude-3", "gemini", "pixtral", "llava", "vision"))
LLM_ACCEPTS_NAMES: bool = "openai/" in config["model"]

ALLOWED_FILE_TYPES = ("image", "text")
ALLOWED_CHANNEL_TYPES = (discord.ChannelType.text, discord.ChannelType.public_thread, discord.ChannelType.private_thread, discord.ChannelType.private)
ALLOWED_CHANNEL_IDS = config["allowed_channel_ids"]
ALLOWED_ROLE_IDS = config["allowed_role_ids"]

MAX_TEXT = config["max_text"]
MAX_IMAGES = config["max_images"] if LLM_ACCEPTS_IMAGES else 0
MAX_MESSAGES = config["max_messages"]

STREAMING_INDICATOR = " ⚪"
EDIT_DELAY_SECONDS = 1

USE_PLAIN_RESPONSES: bool = config["use_plain_responses"]
MAX_MESSAGE_LENGTH = 3000 if USE_PLAIN_RESPONSES else (4096 - len(STREAMING_INDICATOR))

EMBED_COLOR_COMPLETE = discord.Color.dark_green()
EMBED_COLOR_INCOMPLETE = discord.Color.orange()

MAX_MESSAGE_NODES = 100

provider, model = config["model"].split("/", 1)
base_url = config["providers"][provider]["base_url"]
api_key = config["providers"][provider].get("api_key", "None")
openai_client = AsyncOpenAI(base_url=base_url, api_key=api_key)

intents = discord.Intents.default()
intents.message_content = True
activity = discord.CustomActivity(name=config["status_message"][:128] or "github.com/jakobdylanc/llmcord.py")
discord_client = discord.Client(intents=intents, activity=activity)

msg_nodes = {}
last_task_time = None

if config["client_id"] != 123456789:
    print(f"\nBOT INVITE URL:\nhttps://discord.com/api/oauth2/authorize?client_id={config['client_id']}&permissions=412317273088&scope=bot\n")

@dataclass
class MsgNode:
    data: dict = field(default_factory=dict)
    next_msg: Optional[discord.Message] = None

    too_much_text: bool = False
    too_many_images: bool = False
    has_bad_attachments: bool = False
    fetch_next_failed: bool = False

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

def get_system_prompt():
    system_prompt_extras = [f"Today's date: {dt.now().strftime('%B %d %Y')}."]
    if LLM_ACCEPTS_NAMES:
        system_prompt_extras += ["User's names are their Discord IDs and should be typed as '<@ID>'."]

    return {
        "role": "system",
        "content": "\n".join([config["system_prompt"]] + system_prompt_extras),
    }

class UserHistory:
    def __init__(self, history_dir="user_histories"):
        self.history_dir = history_dir
        os.makedirs(history_dir, exist_ok=True)
    
    def get_user_file_path(self, user_id):
        """Get the path to a user's history file"""
        return os.path.join(self.history_dir, f"{user_id}.json")
    
    def get_user_history(self, user_id):
        """Get all conversation history for a user"""
        file_path = self.get_user_file_path(user_id)
        
        if not os.path.exists(file_path):
            return []
            
        with open(file_path, 'r') as f:
            history = json.load(f)
            return history.get("conversations", [])
    
    def is_significant_conversation(self, messages):
        """Determine if a conversation is worth saving"""
        if not messages:
            return False
            
        # Define criteria for significant conversations
        significant_indicators = [
            "remember",
            "dont forget",
            "my name is",
            "i am",
            "i'm",
            "i like",
            "i love",
            "i hate",
            "i need",
            "always",
            "never",
            "favorite"
        ]
        
        # Join all messages into one string for checking
        conversation_text = " ".join(
            msg["content"] if isinstance(msg["content"], str) 
            else msg["content"][0]["text"] if msg["content"] else ""
            for msg in messages
        ).lower()
        
        # Check if any significant indicators are present
        return any(indicator in conversation_text for indicator in significant_indicators)
    
    def extract_key_information(self, messages):
        """Extract only important parts of the conversation"""
        key_messages = []
        
        for msg in messages:
            content = msg["content"] if isinstance(msg["content"], str) else msg["content"][0]["text"] if msg["content"] else ""
            
            # Skip short or empty messages
            if not content or len(content.split()) < 3:
                continue
                
            # Skip messages that don't contain personal information
            if not any(indicator in content.lower() for indicator in [
                "remember", "my name", "i am", "i'm", "i like", "i love", 
                "i hate", "i need", "always", "never", "favorite"
            ]):
                continue
                
            key_messages.append({
                "role": msg["role"],
                "content": content
            })
        
        return key_messages
    
    def save_conversation(self, user_id, username, messages):
        """Save conversation history for a user"""
        # Only save if conversation is significant
        if not self.is_significant_conversation(messages):
            return
            
        # Extract only key information
        key_messages = self.extract_key_information(messages)
        if not key_messages:
            return
            
        file_path = self.get_user_file_path(user_id)
        
        # Load existing history if it exists
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                history = json.load(f)
        else:
            history = {
                "user_id": user_id,
                "username": username,
                "conversations": []
            }
        
        # Add new conversation with only key information
        conversation = {
            "timestamp": dt.now().isoformat(),
            "messages": key_messages
        }
        
        # Keep only last 10 conversations
        history["conversations"] = (history["conversations"] + [conversation])[-10:]
        
        # Save updated history
        with open(file_path, 'w') as f:
            json.dump(history, f, indent=2)
    
    def get_relevant_history(self, user_id, current_message):
        """Get only relevant historical context based on current message"""
        file_path = self.get_user_file_path(user_id)
        
        if not os.path.exists(file_path):
            return []
            
        # First, check if we need history based on current message
        current_text = current_message.content.lower()
        
        # Define triggers that would make history relevant
        history_triggers = [
            "remember",
            "you said",
            "last time",
            "before",
            "previously",
            "earlier",
            "yesterday",
            "last week",
            "forgot",
            "told you"
        ]
        
        # If none of the triggers are present, return empty list
        if not any(trigger in current_text for trigger in history_triggers):
            return []
            
        # If we need history, load and filter it
        with open(file_path, 'r') as f:
            history = json.load(f)
            
        # Get recent conversations
        recent_convos = history["conversations"][-3:]  # Last 3 conversations
        
        # Filter to only relevant messages based on content similarity
        relevant_messages = []
        for conv in recent_convos:
            for msg in conv["messages"]:
                # Simple relevance check - can be made more sophisticated
                if any(word in current_text for word in msg["content"].lower().split()):
                    relevant_messages.append(msg)
                    
        return relevant_messages


@discord_client.event
async def on_message(new_msg):
    global msg_nodes, last_task_time

    # Initialize user_history if not exists
    if not hasattr(on_message, "user_history"):
        on_message.user_history = UserHistory()

    # Ignore messages from the bot itself
    if new_msg.author == discord_client.user:
        return

    logging.info(f"Message received: {new_msg.content}")  # Log the incoming message

    # Check if message contains "//" followed by bot name - if so, ignore it
    if re.search(rf'//\s*{re.escape(discord_client.user.name)}\b', new_msg.content, re.IGNORECASE):
        return

    # Check if the bot's mention is present in the message or if its name is mentioned as a whole word
    if discord_client.user.mentioned_in(new_msg) or re.search(rf'\b{re.escape(discord_client.user.name)}\b', new_msg.content, re.IGNORECASE):
        response_msgs = []
        response_contents = []
        edit_task = None
        embed = None

        try:
            # Build message reply chain and set user warnings
            reply_chain = []
            user_warnings = set()

            # Get user's conversation history
            user_history = on_message.user_history.get_user_history(new_msg.author.id)
            
            # Create context from past conversations
            historical_context = []
            if user_history:
                historical_context = [
                    {
                        "role": "system",
                        "content": f"Previous conversation history with user {new_msg.author.name}:"
                    }
                ]
                for conv in user_history:
                    for msg in conv["messages"]:
                        historical_context.append({
                            "role": msg["role"],
                            "content": msg["content"]
                        })

            curr_msg = new_msg
            while curr_msg and len(reply_chain) < MAX_MESSAGES:
                curr_node = msg_nodes.setdefault(curr_msg.id, MsgNode())

                async with curr_node.lock:
                    if not curr_node.data:
                        good_attachments = {type: [att for att in curr_msg.attachments if att.content_type and type in att.content_type] for type in ALLOWED_FILE_TYPES}

                        text = "\n".join(
                            ([curr_msg.content] if curr_msg.content else [])
                            + [embed.description for embed in curr_msg.embeds if embed.description]
                            + [requests.get(att.url).text for att in good_attachments["text"]]
                        )
                        if curr_msg.content.startswith(discord_client.user.mention):
                            text = text.replace(discord_client.user.mention, "", 1).lstrip()

                        if LLM_ACCEPTS_IMAGES and good_attachments["image"][:MAX_IMAGES]:
                            content = ([{"type": "text", "text": text[:MAX_TEXT]}] if text[:MAX_TEXT] else []) + [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{att.content_type};base64,{base64.b64encode(requests.get(att.url).content).decode('utf-8')}"},
                                }
                                for att in good_attachments["image"][:MAX_IMAGES]
                            ]
                        else:
                            content = text[:MAX_TEXT]

                        data = {
                            "content": content,
                            "role": "assistant" if curr_msg.author == discord_client.user else "user",
                        }
                        if LLM_ACCEPTS_NAMES:
                            data["name"] = str(curr_msg.author.id)

                        curr_node.data = data
                        curr_node.too_much_text = len(text) > MAX_TEXT
                        curr_node.too_many_images = len(good_attachments["image"]) > MAX_IMAGES
                        curr_node.has_bad_attachments = len(curr_msg.attachments) > sum(len(att_list) for att_list in good_attachments.values())

                        try:
                            if (
                                not curr_msg.reference
                                and curr_msg.channel.type != discord.ChannelType.private
                                and discord_client.user.mention not in curr_msg.content
                                and (prev_msg_in_channel := ([m async for m in curr_msg.channel.history(before=curr_msg, limit=1)] or [None])[0])
                                and any(prev_msg_in_channel.type == type for type in (discord.MessageType.default, discord.MessageType.reply))
                                and prev_msg_in_channel.author == curr_msg.author
                            ):
                                curr_node.next_msg = prev_msg_in_channel
                            else:
                                next_is_thread_parent: bool = not curr_msg.reference and curr_msg.channel.type == discord.ChannelType.public_thread
                                if next_msg_id := curr_msg.channel.id if next_is_thread_parent else getattr(curr_msg.reference, "message_id", None):
                                    next_node = msg_nodes.setdefault(next_msg_id, MsgNode())
                                    while next_node.lock.locked():
                                        await asyncio.sleep(0)
                                    curr_node.next_msg = (
                                        (curr_msg.channel.starter_message or await curr_msg.channel.parent.fetch_message(next_msg_id))
                                        if next_is_thread_parent
                                        else (curr_msg.reference.cached_message or await curr_msg.channel.fetch_message(next_msg_id))
                                    )
                        except (discord.NotFound, discord.HTTPException, AttributeError):
                            logging.exception("Error fetching next message in the chain")
                            curr_node.fetch_next_failed = True

                    if curr_node.data["content"]:
                        reply_chain += [curr_node.data]

                    if curr_node.too_much_text:
                        user_warnings.add(f"⚠️ Max {MAX_TEXT:,} characters per message")
                    if curr_node.too_many_images:
                        user_warnings.add(f"⚠️ Max {MAX_IMAGES} image{'' if MAX_IMAGES == 1 else 's'} per message" if MAX_IMAGES > 0 else "⚠️ Can't see images")
                    if curr_node.has_bad_attachments:
                        user_warnings.add("⚠️ Unsupported attachments")
                    if curr_node.fetch_next_failed or (curr_node.next_msg and len(reply_chain) == MAX_MESSAGES):
                        user_warnings.add(f"⚠️ Only using last {len(reply_chain)} message{'' if len(reply_chain) == 1 else 's'}")

                    curr_msg = curr_node.next_msg

            # Combine current conversation with the system note and historical context at the end
            messages = (
                list(reply_chain)
                + ([get_system_prompt()] if config["system_prompt"] else [])
                + ["The following information is only for context. Do not use the following information unless it is actively pertinent to the immediate conversation."]
                + list(historical_context)
            )[::-1]

            logging.info(f"Message received (user ID: {new_msg.author.id}, attachments: {len(new_msg.attachments)}, reply chain length: {len(reply_chain)}):\n{new_msg.content}")

            # Generate chat kwargs and send response message(s)
            kwargs = create_chat_kwargs(messages, model, config)
                
            async with new_msg.channel.typing():
                async for curr_chunk in await openai_client.chat.completions.create(**kwargs):
                    curr_content = curr_chunk.choices[0].delta.content or ""
                    finish_reason = curr_chunk.choices[0].finish_reason

                    # Initialize first message if needed
                    if not response_contents:
                        response_contents = [""]
                        if not USE_PLAIN_RESPONSES:
                            embed = discord.Embed(description=STREAMING_INDICATOR, color=EMBED_COLOR_INCOMPLETE)
                            for warning in sorted(user_warnings):
                                embed.add_field(name=warning, value="", inline=False)
                            response_msg = await new_msg.reply(embed=embed, silent=True)
                            msg_nodes[response_msg.id] = MsgNode(next_msg=new_msg)
                            await msg_nodes[response_msg.id].lock.acquire()
                            last_task_time = dt.now().timestamp()
                            response_msgs += [response_msg]

                    # Check if we need to start a new message due to length
                    if len(response_contents[-1] + curr_content) > MAX_MESSAGE_LENGTH:
                        response_contents += [""]
                        if not USE_PLAIN_RESPONSES:
                            embed = discord.Embed(description=STREAMING_INDICATOR, color=EMBED_COLOR_INCOMPLETE)
                            response_msg = await response_msgs[-1].reply(embed=embed, silent=True)
                            msg_nodes[response_msg.id] = MsgNode(next_msg=new_msg)
                            await msg_nodes[response_msg.id].lock.acquire()
                            response_msgs += [response_msg]

                    # Add current content to the latest message
                    response_contents[-1] += curr_content

                    # Update message if needed
                    if not USE_PLAIN_RESPONSES:
                        should_update = (
                            finish_reason is not None or
                            (not edit_task or edit_task.done()) and
                            dt.now().timestamp() - last_task_time >= EDIT_DELAY_SECONDS
                        )

                        if should_update:
                            while edit_task and not edit_task.done():
                                await asyncio.sleep(0)

                            embed.description = response_contents[-1]
                            if not finish_reason:
                                embed.description += STREAMING_INDICATOR

                            embed.color = EMBED_COLOR_COMPLETE if finish_reason == "stop" else EMBED_COLOR_INCOMPLETE
                            edit_task = asyncio.create_task(response_msgs[-1].edit(embed=embed))
                            last_task_time = dt.now().timestamp()

            # Final update for non-streaming mode
            if USE_PLAIN_RESPONSES:
                for content in response_contents:
                    reply_to_msg = new_msg if not response_msgs else response_msgs[-1]
                    response_msg = await reply_to_msg.reply(content=content)
                    msg_nodes[response_msg.id] = MsgNode(next_msg=new_msg)
                    await msg_nodes[response_msg.id].lock.acquire()
                    response_msgs += [response_msg]

            # Create MsgNode data for response messages
            data = {
                "content": "".join(response_contents),
                "role": "assistant",
            }
            if LLM_ACCEPTS_NAMES:
                data["name"] = str(discord_client.user.id)

            for msg in response_msgs:
                msg_nodes[msg.id].data = data
                msg_nodes[msg.id].lock.release()

            # Save the conversation history
            try:
                conversation_messages = [
                    {
                        "role": msg["role"],
                        "content": msg["content"] if isinstance(msg["content"], str) 
                        else msg["content"][0]["text"] if msg["content"] else ""
                    }
                    for msg in reply_chain
                ]
                on_message.user_history.save_conversation(
                    new_msg.author.id,
                    new_msg.author.name,
                    conversation_messages
                )
            except Exception as e:
                logging.error(f"Error saving conversation history: {e}")

        except Exception as e:
            logging.exception("Error while generating response")

        # Delete oldest MsgNodes from the cache
        if (num_nodes := len(msg_nodes)) > MAX_MESSAGE_NODES:
            for msg_id in sorted(msg_nodes.keys())[: num_nodes - MAX_MESSAGE_NODES]:
                async with msg_nodes.setdefault(msg_id, MsgNode()).lock:
                    del msg_nodes[msg_id]



    
def sanitize_message(msg):
    """
    Sanitizes messages for OpenAI API compatibility.
    Returns a properly formatted message dictionary with 'role' and 'content' keys.
    """
    try:
        if isinstance(msg, dict):
            # Process content if it's a list of content parts
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
                )
            elif not isinstance(content, str):
                content = str(content)
                
            # Ensure role is valid
            role = msg.get("role", "user")
            if role not in ["system", "user", "assistant"]:
                role = "user"

            return {"role": role, "content": content}
        
        elif isinstance(msg, str):  # If message is a string, treat as user message
            return {"role": "user", "content": msg}
        
        else:  # Any other type, convert to string and treat as user message
            return {"role": "user", "content": str(msg)}

    except Exception as e:
        print(f"Error sanitizing message: {e}")
        return {"role": "user", "content": "Error processing message"}
                
        
        
# Helper function to create kwargs with sanitized messages
def create_chat_kwargs(messages, model, config):
    """
    Creates properly formatted kwargs for OpenAI API chat completion.
    """
    sanitized_messages = [sanitize_message(msg) for msg in messages]
    
    kwargs = {
        "model": model,
        "messages": sanitized_messages,
        "stream": True
    }
    
    # Add any additional parameters from config
    if "extra_api_parameters" in config:
        kwargs.update(config["extra_api_parameters"])
        
    return kwargs


async def main():
    await discord_client.start(config["bot_token"])

asyncio.run(main())