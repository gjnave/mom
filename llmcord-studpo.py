import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime as dt
import json
import os
import logging
import requests
from typing import Optional
import re
import discord
from openai import AsyncOpenAI
from enum import Enum
import dateparser
from typing import Dict, List, Tuple
import aiohttp 
import aiofiles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

with open("config.json", "r") as file:
    config = {k: v for d in json.load(file).values() for k, v in d.items()}

LLM_ACCEPTS_IMAGES: bool = any(x in config["model"] for x in ("gpt-4o", "claude-3", "gemini", "pixtral", "llava", "vision", "llama3.2-vision"))
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

# Memory storage logic
MEMORY_FILE = "user_memories.json"

async def fetch_text(url: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.text()

async def fetch_image_data(url: str, content_type: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            content = await response.read()
            return f"data:{content_type};base64,{base64.b64encode(content).decode('utf-8')}"



import re

@discord_client.event
async def on_ready():
    print(f"Logged in as {discord_client.user}")
    # Initialize memory system here
    global memory_system
    memory_system = EnhancedMemory()
    await memory_system._load_memories()  # Load memories on startup

class MemoryType(Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    DESCRIPTION = "description"
    RELATIONSHIP = "relationship"
    EVENT = "event"

@dataclass
class MemoryEntry:
    key: str
    value: str
    memory_type: MemoryType
    source: str  # 'explicit' or 'inferred'
    created: float = field(default_factory=lambda: dt.now().timestamp())
    expires: Optional[float] = None
    confidence: float = 1.0
    references: int = 1

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "type": self.memory_type.value,
            "source": self.source,
            "created": self.created,
            "expires": self.expires,
            "confidence": self.confidence,
            "references": self.references
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'MemoryEntry':
        return cls(
            key=data["key"],
            value=data["value"],
            memory_type=MemoryType(data["type"]),
            source=data["source"],
            created=data["created"],
            expires=data.get("expires"),
            confidence=data.get("confidence", 1.0),
            references=data.get("references", 1)
        )

class EnhancedMemory:
    def __init__(self):
        self.memories: Dict[str, List[MemoryEntry]] = {}
        self.sorted_cache: Dict[str, List[MemoryEntry]] = {}  # Cache for sorted memories
        asyncio.create_task(self._load_memories())  # Load memories asynchronously on startup

    async def _load_memories(self) -> Dict[str, List[MemoryEntry]]:
        """Asynchronously load memories from file."""
        if os.path.exists(MEMORY_FILE):
            async with aiofiles.open(MEMORY_FILE, mode="r") as f:
                data = json.loads(await f.read())
                self.memories = {k: [MemoryEntry.from_dict(m) for m in v] for k, v in data.items()}
        return self.memories

    async def _save_memories(self):
        """Asynchronously save memories to file."""
        data = {k: [m.to_dict() for m in v] for k, v in self.memories.items()}
        async with aiofiles.open(MEMORY_FILE, mode="w") as f:
            await f.write(json.dumps(data, indent=2))

    async def _invalidate_cache(self, user_id: str):
        """Invalidate the sorted cache for a specific user."""
        if user_id in self.sorted_cache:
            del self.sorted_cache[user_id]

    async def add_memory(self, user_id: str, memory: MemoryEntry):
        """Add a memory for a user and invalidate their cache."""
        user_id = str(user_id)
        if user_id not in self.memories:
            self.memories[user_id] = []
            
        # Check for existing similar memories
        existing = next((m for m in self.memories[user_id] 
                        if m.key == memory.key and m.value == memory.value), None)
        if existing:
            existing.references += 1
            existing.confidence = min(existing.confidence + 0.1, 1.0)
            if memory.expires:
                existing.expires = memory.expires
        else:
            self.memories[user_id].append(memory)
            
        await self._cleanup_user(user_id)
        await self._invalidate_cache(user_id)  # Invalidate cache after adding
        await self._save_memories()  # Save asynchronously

    async def remove_memory(self, user_id: str, key: str) -> bool:
        """Remove a memory for a user and invalidate their cache."""
        user_id = str(user_id)
        if user_id not in self.memories:
            return False
            
        initial_count = len(self.memories[user_id])
        self.memories[user_id] = [m for m in self.memories[user_id] if m.key != key]
        await self._cleanup_user(user_id)
        await self._invalidate_cache(user_id)  # Invalidate cache after removal
        await self._save_memories()  # Save asynchronously
        return len(self.memories[user_id]) < initial_count

    async def _cleanup_user(self, user_id: str):
        """Clean up expired or low-confidence memories for a user."""
        now = dt.now().timestamp()
        if user_id in self.memories:
            self.memories[user_id] = [
                m for m in self.memories[user_id]
                if (m.expires is None or m.expires > now) and m.confidence > 0.2
            ]

    async def get_contextual_memories(self, user_id: str, current_topic: str = "") -> str:
        """Get relevant memories for a user, using cached sorted results if available."""
        user_id = str(user_id)
        
        # Use cached sorted memories if available
        if user_id in self.sorted_cache:
            memories = self.sorted_cache[user_id]
        else:
            # Sort and cache memories
            memories = self.get_user_memories(user_id)
            scored = [(mem.confidence * mem.references, mem) for mem in memories]
            scored.sort(reverse=True, key=lambda x: x[0])  # Sort by score
            self.sorted_cache[user_id] = [mem for _, mem in scored]  # Cache sorted results
            memories = self.sorted_cache[user_id]

        # Filter by current topic if provided
        if current_topic:
            memories = [mem for mem in memories if current_topic.lower() in mem.key.lower()]

        return "\n".join(
            f"- {mem.key}: {mem.value} ({mem.memory_type.value})" 
            for mem in memories[:10]  # Return top 10 memories
        )

    def get_user_memories(self, user_id: str) -> List[MemoryEntry]:
        """Get raw memories for a user (no sorting or filtering)."""
        return self.memories.get(str(user_id), [])


def extract_memory_from_message(message: str) -> Tuple[Optional[str], Optional[str], Optional[MemoryType]]:
    patterns = [
        # Improved patterns with better punctuation handling
        (r"(?:remember|recall)\s*,?\s*(?:that\s+)?(?:my\s+([\w\s]+?)\s+(?:is|are)\s+([^\.!?]+))", MemoryType.FACT),
        (r"(?:remember|recall)\s*,?\s*(?:that\s+)?I\s*(?:'?m|am)\s+([^\.!?]+)", MemoryType.DESCRIPTION),
        (r"(?:my\s+([\w\s]+?)\s+(?:is|are)\s+([^\.!?]+))", MemoryType.FACT),
    ]

    for pattern, mem_type in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            groups = match.groups()
            if mem_type == MemoryType.DESCRIPTION and len(groups) == 1:
                return "description", groups[0].strip(), mem_type
            elif len(groups) >= 2:
                return groups[0].strip().lower(), groups[1].strip(), mem_type
                
    return None, None, None  # Ensure three values are always returned

async def handle_memory_commands(message: discord.Message):
    content = message.content.lower()
    user_id = str(message.author.id)
    
    # Memory recall command
    if re.match(r"mom,?\s+what (?:do you|should you) remember\??", content):
        memories = memory_system.get_contextual_memories(user_id)
        response = "Here's what I remember about you:\n" + memories if memories else "I don't have any memories about you yet!"
        await message.reply(response)
        return True
        
    # Memory deletion command
    if match := re.match(r"mom,?\s+forget\s+(.+)", content, re.IGNORECASE):
        key = match.group(1).strip()
        if memory_system.remove_memory(user_id, key):
            await message.reply(f"I've forgotten your {key}!")
        else:
            await message.reply(f"I didn't have any memory about {key}.")
        return True
        
    # Memory expiration command
    if match := re.match(r"mom,?\s+remember\s+(.+?)\s+for\s+(.+)", content, re.IGNORECASE):
        key, value, mem_type = extract_memory_from_message(match.group(1))
        duration = dateparser.parse(f"in {match.group(2)}")
        if key and value and duration:
            expires = duration.timestamp()
            memory = MemoryEntry(
                key=key,
                value=value,
                memory_type=mem_type,
                source="explicit",
                expires=expires
            )
            memory_system.add_memory(user_id, memory)
            await message.reply(f"I'll remember your {key} is {value} for {match.group(2)}!")
            return True
            
    return False

def get_system_prompt(user_id: str, current_message: str = "") -> dict:
    base_prompt = config["system_prompt"]
    memories = memory_system.get_contextual_memories(user_id, current_message)
    
    prompt = [
        base_prompt,
        f"Current date: {dt.now().strftime('%B %d, %Y')}",
        "User memories:",
        memories if memories else "No memories available"
    ]
    
    if LLM_ACCEPTS_NAMES:
        prompt.append("User references should use Discord ID format (<@ID>) when possible.")
    
    return {
        "role": "system",
        "content": "\n".join(prompt)
    }

async def infer_memories_from_conversation(message: discord.Message, llm_response: str):
    """
    Use LLM to extract implicit memories from conversation
    """
    user_id = str(message.author.id)
    prompt = f"""Analyze this conversation and identify permanent facts to remember about the user.
    
    Message: {message.content}
    Response: {llm_response}
    
    Output 0-3 memories in JSON format: [{{"key": "category", "value": "detail", "type": "fact|preference|description"}}]"""
    
    try:
        response = await openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        
        memories = json.loads(response.choices[0].message.content)
        for mem in memories.get("memories", []):
            entry = MemoryEntry(
                key=mem["key"],
                value=mem["value"],
                memory_type=MemoryType(mem["type"]),
                source="inferred",
                confidence=0.7
            )
            memory_system.add_memory(user_id, entry)
            
    except Exception as e:
        logging.error(f"Memory inference failed: {str(e)}")



@dataclass
class MsgNode:
    data: dict = field(default_factory=dict)
    next_msg: Optional[discord.Message] = None

    too_much_text: bool = False
    too_many_images: bool = False
    has_bad_attachments: bool = False
    fetch_next_failed: bool = False

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@discord_client.event
async def on_message(new_msg):
    global msg_nodes, last_task_time

    # Ignore messages from the bot itself
    if new_msg.author == discord_client.user:
        return

    # Log every message received
    logging.info(f"Received message: {new_msg.content} from {new_msg.author}")

    # Handle memory-related commands first
    if await handle_memory_commands(new_msg):
        return

    # Check for bot mentions, name, or the word "mom"
    if not (discord_client.user.mentioned_in(new_msg)
            or re.search(rf'\b{re.escape(discord_client.user.name)}\b', new_msg.content, re.IGNORECASE)
            or re.search(r'\bmom\b', new_msg.content, re.IGNORECASE)):
        return
        
        # Handle memory updates and recalls
    if discord_client.user.mentioned_in(new_msg) or "mom" in new_msg.content.lower():
        # Example: Add a memory
        memory = MemoryEntry(
            key="favorite_color", 
            value="blue", 
            memory_type=MemoryType.FACT, 
            source="explicit"
        )
        await memory_system.add_memory(str(new_msg.author.id), memory)

        # Example: Get contextual memories
        memories = await memory_system.get_contextual_memories(str(new_msg.author.id), current_topic="color")
        await new_msg.reply(f"I remember:\n{memories}")

    # Log that the bot is processing the message
    logging.info("Message contains 'mom' or mentions the bot. Processing...")

    # Auto-memory extraction from explicit statements
    if discord_client.user.mentioned_in(new_msg) or "mom" in new_msg.content.lower():
        key, value, mem_type = extract_memory_from_message(new_msg.content)
        if key and value and mem_type:  # Ensure all three values are present
            memory = MemoryEntry(
                key=key,
                value=value,
                memory_type=mem_type,
                source="explicit"
            )
            memory_system.add_memory(str(new_msg.author.id), memory)  # Ensure user_id is a string
            await new_msg.reply(f"📝 Got it! I'll remember your {key} is {value}.")
            return

    # Build message reply chain and set user warnings
    reply_chain = []
    user_warnings = set()
    curr_msg = new_msg

    while curr_msg and len(reply_chain) < MAX_MESSAGES:
        curr_node = msg_nodes.setdefault(curr_msg.id, MsgNode())

        async with curr_node.lock:
            if not curr_node.data:
                # Process attachments
                good_attachments = {
                    type: [att for att in curr_msg.attachments 
                          if att.content_type and type in att.content_type]
                    for type in ALLOWED_FILE_TYPES
                }

                # Build message content
                text_parts = []
                if curr_msg.content:
                    text_parts.append(curr_msg.content)
                if curr_msg.embeds:
                    text_parts.extend(embed.description for embed in curr_msg.embeds if embed.description)
                if good_attachments.get("text"):
                    text_parts.extend(await asyncio.gather(*[fetch_text(att.url) for att in good_attachments["text"]]))

                # Combine text parts
                text = "\n".join(text_parts)
                if curr_msg.content.startswith(discord_client.user.mention):
                    text = text.replace(discord_client.user.mention, "", 1).lstrip()

                # Build content array
                if LLM_ACCEPTS_IMAGES and good_attachments.get("image"):
                    content = ([{"type": "text", "text": text[:MAX_TEXT]}] if text else []) + [
                        {
                            "type": "image_url",
                            "image_url": {"url": await fetch_image_data(att.url, att.content_type)},
                        }
                        for att in good_attachments["image"][:MAX_IMAGES]
                    ]
                else:
                    content = text[:MAX_TEXT]

                # Create message data
                data = {
                    "content": content,
                    "role": "assistant" if curr_msg.author == discord_client.user else "user",
                }
                if LLM_ACCEPTS_NAMES:
                    data["name"] = str(curr_msg.author.id)

                curr_node.data = data
                curr_node.too_much_text = len(text) > MAX_TEXT
                curr_node.too_many_images = len(good_attachments.get("image", [])) > MAX_IMAGES
                curr_node.has_bad_attachments = len(curr_msg.attachments) > sum(len(att_list) for att_list in good_attachments.values())

                # Build message chain
                try:
                    if (not curr_msg.reference 
                        and curr_msg.channel.type != discord.ChannelType.private
                        and discord_client.user.mention not in curr_msg.content):
                        prev_msg = [m async for m in curr_msg.channel.history(before=curr_msg, limit=1)]
                        if prev_msg and prev_msg[0].author == curr_msg.author:
                            curr_node.next_msg = prev_msg[0]
                    else:
                        if curr_msg.reference:
                            curr_node.next_msg = await curr_msg.channel.fetch_message(curr_msg.reference.message_id)
                except Exception as e:
                    logging.error(f"Error building message chain: {str(e)}")
                    curr_node.fetch_next_failed = True

            if curr_node.data.get("content"):
                reply_chain.append(curr_node.data)

            # Collect warnings
            if curr_node.too_much_text:
                user_warnings.add(f"⚠️ Max {MAX_TEXT:,} characters")
            if curr_node.too_many_images:
                user_warnings.add(f"⚠️ Max {MAX_IMAGES} image{'s' if MAX_IMAGES !=1 else ''}")
            if curr_node.has_bad_attachments:
                user_warnings.add("⚠️ Unsupported attachments")
            if curr_node.fetch_next_failed:
                user_warnings.add("⚠️ Incomplete message history")

            curr_msg = curr_node.next_msg

    # Prepare messages for LLM
    system_prompt = get_system_prompt(str(new_msg.author.id), new_msg.content)  # Ensure user_id is a string
    messages = [system_prompt] + reply_chain[::-1]

    # Generate response
    response_msgs = []
    response_contents = []
    try:
        async with new_msg.channel.typing():
            stream = await openai_client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                extra_body=config["extra_api_parameters"]
            )
            
            async for chunk in stream:
                content = chunk.choices[0].delta.content or ""
                finish_reason = chunk.choices[0].finish_reason

                if not response_contents:
                    # Initialize first response message
                    response_contents = [""]
                    if not USE_PLAIN_RESPONSES:
                        embed = discord.Embed(description=STREAMING_INDICATOR, color=EMBED_COLOR_INCOMPLETE)
                        for warning in user_warnings:
                            embed.add_field(name=warning, value="", inline=False)
                        response_msg = await new_msg.reply(embed=embed)
                        msg_nodes[response_msg.id] = MsgNode(next_msg=new_msg)
                        response_msgs.append(response_msg)

                # Handle content splitting
                if len(response_contents[-1] + content) > MAX_MESSAGE_LENGTH:
                    response_contents.append("")
                    if not USE_PLAIN_RESPONSES:
                        embed = discord.Embed(description=STREAMING_INDICATOR, color=EMBED_COLOR_INCOMPLETE)
                        response_msg = await response_msgs[-1].reply(embed=embed)
                        msg_nodes[response_msg.id] = MsgNode(next_msg=new_msg)
                        response_msgs.append(response_msg)

                response_contents[-1] += content

                # Update message
                if not USE_PLAIN_RESPONSES:
                    embed.description = response_contents[-1]
                    if not finish_reason:
                        embed.description += STREAMING_INDICATOR
                    embed.color = EMBED_COLOR_COMPLETE if finish_reason == "stop" else EMBED_COLOR_INCOMPLETE
                    await response_msgs[-1].edit(embed=embed)

        # Infer implicit memories from conversation
        await infer_memories_from_conversation(new_msg, "".join(response_contents))

    except Exception as e:
        logging.error(f"Response generation failed: {str(e)}")
        await new_msg.reply("⚠️ Error generating response")
        return

    # Finalize message nodes
    for msg in response_msgs:
        msg_nodes[msg.id].data = {
            "content": "".join(response_contents),
            "role": "assistant",
            "name": str(discord_client.user.id) if LLM_ACCEPTS_NAMES else None
        }
        msg_nodes[msg.id].lock.release()

    # Cleanup old nodes
    if len(msg_nodes) > MAX_MESSAGE_NODES:
        for msg_id in sorted(msg_nodes.keys())[:len(msg_nodes) - MAX_MESSAGE_NODES]:
            del msg_nodes[msg_id]
async def main():
    await discord_client.start(config["bot_token"])

asyncio.run(main())