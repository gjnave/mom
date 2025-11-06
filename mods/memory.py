"""
Enhanced Memory Plugin for Discord Bot
Implements robust, production-ready memory management with multiple memory types,
automatic cleanup, sophisticated context management, global facts, and multi-user context.
"""

import logging
import json
import os
import asyncio
from datetime import datetime as dt, timedelta
from typing import Dict, List, Optional, Any, Set
from collections import defaultdict
import hashlib
import re
import requests
import tempfile
from mods.vision_caption import caption_image

# ============================================================================
# CONFIGURATION
# ============================================================================

class MemoryConfig:
    """Configuration for memory management."""
    MEMORY_DIR = "user_memory"
    GLOBAL_DIR = "global_memory"
    ARCHIVE_DIR = "user_memory/archive"
    
    # Memory limits to prevent bloat
    MAX_SHORT_TERM_MESSAGES = 10  # Recent conversation context
    MAX_LONG_TERM_FACTS = 50      # User facts and preferences
    MAX_MEMORIES_PER_USER = 100   # Total memory items
    
    # Time-based retention
    SHORT_TERM_RETENTION_DAYS = 7
    ARCHIVE_AFTER_DAYS = 90
    
    # Token estimation (rough approximation)
    AVG_CHARS_PER_TOKEN = 4
    MAX_MEMORY_TOKENS = 1500  # Max tokens to inject into context
    MAX_MENTIONED_USERS = 3   # Max mentioned users to include context for

# ============================================================================
# MEMORY STORAGE HANDLER
# ============================================================================

class MemoryStore:
    """Handles file I/O operations with error handling and atomic writes."""
    
    def __init__(self):
        self._ensure_directories()
    
    def _ensure_directories(self):
        """Create necessary directories."""
        os.makedirs(MemoryConfig.MEMORY_DIR, exist_ok=True)
        os.makedirs(MemoryConfig.GLOBAL_DIR, exist_ok=True)
        os.makedirs(MemoryConfig.ARCHIVE_DIR, exist_ok=True)
        os.makedirs("backups", exist_ok=True)
    
    def _get_user_file(self, user_id: str) -> str:
        """Get the file path for a user's memory."""
        return os.path.join(MemoryConfig.MEMORY_DIR, f"user_{user_id}.json")
    
    def _get_global_file(self, server_id: str) -> str:
        """Get the file path for server's global memory."""
        return os.path.join(MemoryConfig.GLOBAL_DIR, f"server_{server_id}.json")
    
    async def load(self, user_id: str, is_global: bool = False, server_id: str = None) -> Dict[str, Any]:
        """Load memory for a user or global server memory with error handling."""
        if is_global and server_id:
            file_path = self._get_global_file(server_id)
            default_func = self._get_default_global_memory
        else:
            file_path = self._get_user_file(user_id)
            default_func = self._get_default_memory
        
        try:
            def read_file():
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                return default_func()
            
            return await asyncio.to_thread(read_file)
        except json.JSONDecodeError:
            logging.error(f"Corrupted memory file. Creating backup.")
            await self._backup_corrupted_file(user_id if not is_global else server_id, is_global)
            return default_func()
        except Exception as e:
            logging.error(f"Error loading memory: {e}")
            return default_func()
    
    async def save(self, identifier: str, data: Dict[str, Any], is_global: bool = False) -> bool:
        """Save memory with atomic write operation."""
        if is_global:
            file_path = self._get_global_file(identifier)
        else:
            file_path = self._get_user_file(identifier)
        
        logging.info(f"Saving memory to {file_path} with data: {data}")
        temp_path = f"{file_path}.tmp"
        
        try:
            def write_file():
                # Write to temporary file first
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                # Atomic rename
                os.replace(temp_path, file_path)
            
            await asyncio.to_thread(write_file)
            logging.info(f"Successfully saved memory to {file_path}")
            return True
        except Exception as e:
            logging.error(f"Error saving memory: {e}")
            # Clean up temp file if it exists
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False
    
    async def _backup_corrupted_file(self, identifier: str, is_global: bool = False):
        """Backup a corrupted memory file."""
        if is_global:
            file_path = self._get_global_file(identifier)
            prefix = "corrupted_global"
        else:
            file_path = self._get_user_file(identifier)
            prefix = "corrupted_user"
        
        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(
            MemoryConfig.ARCHIVE_DIR, 
            f"{prefix}_{identifier}_{timestamp}.json"
        )
        
        try:
            def backup():
                if os.path.exists(file_path):
                    os.rename(file_path, backup_path)
            await asyncio.to_thread(backup)
        except Exception as e:
            logging.error(f"Failed to backup corrupted file: {e}")
    
    async def backup_user_file(self, user_id: str):
        """Backup a user's memory file."""
        file_path = self._get_user_file(user_id)
        if os.path.exists(file_path):
            timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(
                "backups",
                f"user_{user_id}_{timestamp}.json"
            )
            try:
                def backup():
                    import shutil
                    shutil.copy(file_path, backup_path)
                await asyncio.to_thread(backup)
                logging.info(f"Backed up user file to {backup_path}")
            except Exception as e:
                logging.error(f"Failed to backup user file: {e}")

    def _get_default_memory(self) -> Dict[str, Any]:
        """Return default user memory structure."""
        return {
            "version": "2.0",
            "created_at": dt.now().isoformat(),
            "last_updated": dt.now().isoformat(),
            "short_term": [],      # Recent conversation snippets
            "long_term": {},       # Persistent facts and preferences
            "semantic_memory": [], # Important events/memories
            "statistics": {
                "total_messages": 0,
                "last_interaction": None
            }
        }
    
    def _get_default_global_memory(self) -> Dict[str, Any]:
        """Return default global server memory structure."""
        return {
            "version": "2.0",
            "created_at": dt.now().isoformat(),
            "last_updated": dt.now().isoformat(),
            "global_facts": {},    # Server-wide facts
            "global_memory": [],   # Important server events/info
        }

# ============================================================================
# MEMORY MANAGER
# ============================================================================

class MemoryManager:
    """Advanced memory management with context-aware retrieval."""
    
    def __init__(self):
        self.store = MemoryStore()
        self._cache = {}  # In-memory cache for frequently accessed users
        self._cache_ttl = {}
        self._global_cache = {}  # Cache for server global memory
        self._global_cache_ttl = {}
    
    async def get_memory(self, user_id: str) -> Dict[str, Any]:
        """Retrieve user memory with caching."""
        # Check cache first
        if user_id in self._cache:
            if dt.now() < self._cache_ttl.get(user_id, dt.now()):
                return self._cache[user_id]
        
        # Load from disk
        memory = await self.store.load(user_id)
        
        # Update cache
        self._cache[user_id] = memory
        self._cache_ttl[user_id] = dt.now() + timedelta(minutes=5)
        
        return memory
    
    async def get_global_memory(self, server_id: str) -> Dict[str, Any]:
        """Retrieve global server memory with caching."""
        # Check cache first
        if server_id in self._global_cache:
            if dt.now() < self._global_cache_ttl.get(server_id, dt.now()):
                return self._global_cache[server_id]
        
        # Load from disk
        memory = await self.store.load(server_id, is_global=True, server_id=server_id)
        
        # Update cache
        self._global_cache[server_id] = memory
        self._global_cache_ttl[server_id] = dt.now() + timedelta(minutes=10)
        
        return memory
    
    async def update_memory(self, user_id: str, memory: Dict[str, Any]) -> bool:
        """Update user memory with cache invalidation."""
        memory["last_updated"] = dt.now().isoformat()
        
        success = await self.store.save(user_id, memory)
        
        if success:
            # Update cache
            self._cache[user_id] = memory
            self._cache_ttl[user_id] = dt.now() + timedelta(minutes=5)
        
        return success
    
    async def update_global_memory(self, server_id: str, memory: Dict[str, Any]) -> bool:
        """Update global server memory with cache invalidation."""
        memory["last_updated"] = dt.now().isoformat()
        
        success = await self.store.save(server_id, memory, is_global=True)
        
        if success:
            # Update cache
            self._global_cache[server_id] = memory
            self._global_cache_ttl[server_id] = dt.now() + timedelta(minutes=10)
        
        return success
    
    async def clear_short_term_memory(self, user_id: str):
        """Clear the short-term memory for a user."""
        memory = await self.get_memory(user_id)
        memory["short_term"] = []
        await self.update_memory(user_id, memory)

    async def add_short_term(self, user_id: str, message: str, role: str = "user"):
        """Add to short-term conversational memory."""
        memory = await self.get_memory(user_id)
        
        short_term = memory.get("short_term", [])
        short_term.append({
            "role": role,
            "content": message,
            "timestamp": dt.now().isoformat()
        })
        
        # Trim to max size
        if len(short_term) > MemoryConfig.MAX_SHORT_TERM_MESSAGES:
            short_term = short_term[-MemoryConfig.MAX_SHORT_TERM_MESSAGES:]
        
        memory["short_term"] = short_term
        memory["statistics"]["total_messages"] = memory["statistics"].get("total_messages", 0) + 1
        memory["statistics"]["last_interaction"] = dt.now().isoformat()
        
        await self.update_memory(user_id, memory)
    
    async def set_fact(self, user_id: str, key: str, value: Any, category: str = "general"):
        """Store a persistent fact about the user."""
        memory = await self.get_memory(user_id)
        
        long_term = memory.get("long_term", {})
        
        if category not in long_term:
            long_term[category] = {}
        
        long_term[category][key] = {
            "value": value,
            "updated_at": dt.now().isoformat(),
            "confidence": 1.0  # Can be used for fact decay over time
        }
        
        memory["long_term"] = long_term
        await self.update_memory(user_id, memory)
    
    async def set_global_fact(self, server_id: str, key: str, value: Any, category: str = "general"):
        """Store a global server-wide fact (admin only)."""
        memory = await self.get_global_memory(server_id)
        
        global_facts = memory.get("global_facts", {})
        
        if category not in global_facts:
            global_facts[category] = {}
        
        global_facts[category][key] = {
            "value": value,
            "updated_at": dt.now().isoformat()
        }
        
        memory["global_facts"] = global_facts
        await self.update_global_memory(server_id, memory)
    
    async def add_semantic_memory(self, user_id: str, memory_text: str, importance: int = 5):
        """Add an important memory/event (1-10 importance scale)."""
        memory = await self.get_memory(user_id)
        
        semantic = memory.get("semantic_memory", [])
        
        # Generate a simple hash for deduplication
        memory_hash = hashlib.md5(memory_text.encode()).hexdigest()[:8]
        
        # Check if similar memory exists
        if not any(m.get("hash") == memory_hash for m in semantic):
            semantic.append({
                "content": memory_text,
                "importance": min(max(importance, 1), 10),
                "timestamp": dt.now().isoformat(),
                "hash": memory_hash,
                "access_count": 0
            })
            
            # Sort by importance and trim if needed
            semantic.sort(key=lambda x: x["importance"], reverse=True)
            if len(semantic) > MemoryConfig.MAX_MEMORIES_PER_USER:
                semantic = semantic[:MemoryConfig.MAX_MEMORIES_PER_USER]
            
            memory["semantic_memory"] = semantic
            await self.update_memory(user_id, memory)
    
    async def delete_semantic_memory(self, user_id: str, index: int):
        """Delete a specific semantic memory for a user by index."""
        memory = await self.get_memory(user_id)
        semantic = memory.get("semantic_memory", [])
        if 0 <= index < len(semantic):
            del semantic[index]
            memory["semantic_memory"] = semantic
            await self.update_memory(user_id, memory)
            return True
        return False

    async def delete_global_memory(self, server_id: str, index: int):
        """Delete a specific global memory for a server by index."""
        memory = await self.get_global_memory(server_id)
        global_mem = memory.get("global_memory", [])
        if 0 <= index < len(global_mem):
            del global_mem[index]
            memory["global_memory"] = global_mem
            await self.update_global_memory(server_id, memory)
            return True
        return False

    async def add_global_memory(self, server_id: str, memory_text: str, importance: int = 5):
        """Add important global server memory (admin only)."""
        memory = await self.get_global_memory(server_id)
        
        global_mem = memory.get("global_memory", [])
        
        # Generate a simple hash for deduplication
        memory_hash = hashlib.md5(memory_text.encode()).hexdigest()[:8]
        
        # Check if similar memory exists
        if not any(m.get("hash") == memory_hash for m in global_mem):
            global_mem.append({
                "content": memory_text,
                "importance": min(max(importance, 1), 10),
                "timestamp": dt.now().isoformat(),
                "hash": memory_hash
            })
            
            # Sort by importance and trim if needed
            global_mem.sort(key=lambda x: x["importance"], reverse=True)
            if len(global_mem) > 50:  # Limit global memories
                global_mem = global_mem[:50]
            
            memory["global_memory"] = global_mem
            await self.update_global_memory(server_id, memory)
    
    def extract_mentioned_users(self, message_content: str, message_obj) -> Set[str]:
        """Extract user IDs from Discord mentions in message."""
        mentioned_user_ids = set()
        
        # Discord mentions are in format <@USER_ID> or <@!USER_ID>
        if hasattr(message_obj, 'mentions'):
            for mentioned_user in message_obj.mentions:
                if not mentioned_user.bot:  # Skip bots
                    mentioned_user_ids.add(str(mentioned_user.id))
        
        return mentioned_user_ids
    
    async def get_context_for_llm(
        self, 
        user_id: str, 
        server_id: str = None,
        mentioned_user_ids: Set[str] = None,
        max_tokens: Optional[int] = None
    ) -> str:
        """Generate optimized context string for LLM injection with multi-user support."""
        max_tokens = max_tokens or MemoryConfig.MAX_MEMORY_TOKENS
        
        context_parts = []
        estimated_tokens = 0
        
        # 1. Global Server Facts (if available)
        if server_id:
            global_memory = await self.get_global_memory(server_id)
            global_facts = global_memory.get("global_facts", {})
            
            if global_facts:
                fact_parts = []
                for category, facts in global_facts.items():
                    for key, data in facts.items():
                        fact_parts.append(f"{key}: {data['value']}")
                
                if fact_parts:
                    global_text = f"Server Info: {', '.join(fact_parts[:10])}"
                    tokens = len(global_text) // MemoryConfig.AVG_CHARS_PER_TOKEN
                    if estimated_tokens + tokens <= max_tokens:
                        context_parts.append(global_text)
                        estimated_tokens += tokens
            
            # Global memories
            global_mem = global_memory.get("global_memory", [])
            if global_mem and estimated_tokens < max_tokens:
                top_global = sorted(global_mem, key=lambda x: x["importance"], reverse=True)[:3]
                mem_texts = [m["content"] for m in top_global]
                if mem_texts:
                    global_mem_text = f"Server Context: {' | '.join(mem_texts)}"
                    tokens = len(global_mem_text) // MemoryConfig.AVG_CHARS_PER_TOKEN
                    if estimated_tokens + tokens <= max_tokens:
                        context_parts.append(global_mem_text)
                        estimated_tokens += tokens
        
        # 2. Primary User Profile (Current user)
        memory = await self.get_memory(user_id)
        long_term = memory.get("long_term", {})
        if long_term:
            profile_parts = []
            for category, facts in long_term.items():
                for key, data in facts.items():
                    profile_parts.append(f"{key}: {data['value']}")
            
            if profile_parts:
                profile_text = f"User Profile: {', '.join(profile_parts[:10])}"
                tokens = len(profile_text) // MemoryConfig.AVG_CHARS_PER_TOKEN
                if estimated_tokens + tokens <= max_tokens:
                    context_parts.append(profile_text)
                    estimated_tokens += tokens
        
        # 3. Mentioned Users Context (NEW FEATURE!)
        if mentioned_user_ids and estimated_tokens < max_tokens:
            mentioned_contexts = []
            for mentioned_id in list(mentioned_user_ids)[:MemoryConfig.MAX_MENTIONED_USERS]:
                if mentioned_id == user_id:  # Skip current user
                    continue
                
                mentioned_memory = await self.get_memory(mentioned_id)
                mentioned_facts = []
                
                # Get their profile facts
                mentioned_long_term = mentioned_memory.get("long_term", {})
                for category, facts in mentioned_long_term.items():
                    for key, data in list(facts.items())[:3]:  # Limited facts per mentioned user
                        mentioned_facts.append(f"{key}: {data['value']}")
                
                # Get their top memories
                mentioned_semantic = mentioned_memory.get("semantic_memory", [])
                if mentioned_semantic:
                    top_mem = sorted(mentioned_semantic, key=lambda x: x["importance"], reverse=True)[:2]
                    for mem in top_mem:
                        mentioned_facts.append(mem["content"][:100])
                
                if mentioned_facts:
                    mentioned_contexts.append(f"### About mentioned user: {mentioned_id} ###\n" + f"{', '.join(mentioned_facts[:4])}")
            
            if mentioned_contexts:
                mentioned_text = " | ".join(mentioned_contexts)
                tokens = len(mentioned_text) // MemoryConfig.AVG_CHARS_PER_TOKEN
                if estimated_tokens + tokens <= max_tokens:
                    context_parts.append(mentioned_text)
                    estimated_tokens += tokens
        
        # 4. Important Memories (Top 5 by importance)
        semantic = memory.get("semantic_memory", [])
        if semantic and estimated_tokens < max_tokens:
            top_memories = sorted(semantic, key=lambda x: x["importance"], reverse=True)[:5]
            memory_texts = [m["content"] for m in top_memories]
            if memory_texts:
                memories_text = f"Important Context: {' | '.join(memory_texts)}"
                tokens = len(memories_text) // MemoryConfig.AVG_CHARS_PER_TOKEN
                if estimated_tokens + tokens <= max_tokens:
                    context_parts.append(memories_text)
                    estimated_tokens += tokens
        
        # 5. Recent Conversation (Short-term)
        short_term = memory.get("short_term", [])
        if short_term and estimated_tokens < max_tokens:
            recent = short_term[-5:]  # Last 5 messages
            conv_summary = []
            for msg in recent:
                conv_summary.append(f"{msg['role']}: {msg['content'][:100]}")
            
            if conv_summary:
                conv_text = f"Recent Conversation: {' → '.join(conv_summary)}"
                tokens = len(conv_text) // MemoryConfig.AVG_CHARS_PER_TOKEN
                if estimated_tokens + tokens <= max_tokens:
                    context_parts.append(conv_text)
        
        return "\n".join(context_parts)
    
    async def cleanup_old_memories(self, user_id: str):
        """Clean up old short-term memories and archive old data."""
        memory = await self.get_memory(user_id)
        modified = False
        
        # Clean old short-term memories
        short_term = memory.get("short_term", [])
        cutoff_date = dt.now() - timedelta(days=MemoryConfig.SHORT_TERM_RETENTION_DAYS)
        
        filtered_short_term = []
        for msg in short_term:
            try:
                msg_date = dt.fromisoformat(msg.get("timestamp", ""))
                if msg_date >= cutoff_date:
                    filtered_short_term.append(msg)
                else:
                    modified = True
            except:
                filtered_short_term.append(msg)  # Keep if can't parse date
        
        if modified:
            memory["short_term"] = filtered_short_term
            await self.update_memory(user_id, memory)

# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

memory_manager = MemoryManager()

# ============================================================================
# PERMISSION HELPERS
# ============================================================================

def is_admin(message) -> bool:
    """Check if user has administrator permissions."""
    if hasattr(message.author, 'guild_permissions'):
        return message.author.guild_permissions.administrator
    return False

# ============================================================================
# DISCORD COMMANDS
# ============================================================================

async def help_memory_command(message, user_id):
    """Display all available memory commands."""
    help_text = """**🧠 Memory Plugin Commands**

**Personal Memory:**
├ `!setfact <category> <key> <value>` - Store a personal fact
│  Example: `!setfact preferences food pizza`
├ `!remember <text> [importance]` - Save important memory (1-10)
│  Example: `!remember I graduated MIT in 2020 9`
├ `!myprofile` or `!profile` - View your stored info
├ `!memorystats` or `!memstats` - View memory statistics
├ `!forget confirm` - Clear all your data
└ `!deleteprofile <number> | all confirm` - Delete specific or all memories

**Server-Wide Memory (Admin Only):**
├ `!setglobal <category> <key> <value>` - Store server fact
│  Example: `!setglobal rules timezone EST`
├ `!rememberglobal <text> [importance]` - Server memory
│  Example: `!rememberglobal Server founded Jan 2024 8`
├ `!global` - View global server memory
├ `!deleteglobal <number> | all confirm` - Delete specific or all global memories
└ `!clearcontext` - Clear bot's short-term memory for you

**Owner Only:**
├ `!profilemod <user>` - View another user's profile
└ `!clearcontextmod <user>` or `!ccmod <user>` - Clear another user's short-term memory

**Other:**
└ `!helpmemory` - Show this help message

💡 **Tip:** When you mention other users (e.g., @username), I'll remember context about them too!"""
    
    await message.channel.send(help_text)

async def set_fact_command(message, user_id):
    """Store a fact: !setfact <category> <key> <value>"""
    args = message.content.split(maxsplit=3)[1:]
    
    if len(args) >= 3:
        category, key, value = args[0].lower(), args[1].lower(), args[2]
        await memory_manager.set_fact(user_id, key, value, category)
        await message.channel.send(f"✓ Remembered: {category}.{key} = {value}")
    else:
        await message.channel.send("Usage: `!setfact <category> <key> <value>`\nExample: `!setfact preferences food pizza`")

async def set_global_fact_command(message, user_id):
    """Store a global server fact (admin only): !setglobal <category> <key> <value>"""
    if user_id != "766054890207313931":
        await message.channel.send("❌ This command is restricted to the bot owner.")
        return

    if not is_admin(message):
        await message.channel.send("❌ Only administrators can set global facts.")
        return
    
    if not message.guild:
        await message.channel.send("❌ Global facts can only be set in servers, not DMs.")
        return
    
    args = message.content.split(maxsplit=3)[1:]
    
    if len(args) >= 3:
        category, key, value = args[0].lower(), args[1].lower(), args[2]
        server_id = str(message.guild.id)
        await memory_manager.set_global_fact(server_id, key, value, category)
        await message.channel.send(f"🌐 Global fact set: {category}.{key} = {value}")
    else:
        await message.channel.send("Usage: `!setglobal <category> <key> <value>`\nExample: `!setglobal rules timezone EST`")

async def remember_command(message, user_id):
    """Add important memory: !remember <text> [importance 1-10]"""
    content = message.content[len("!remember"):].strip()
    attachments = message.attachments

    if not content and not attachments:
        await message.channel.send("Usage: `!remember <memory text> [importance]` or attach an image.")
        return

    # Check for importance at the end
    parts = content.rsplit(maxsplit=1)
    importance = 5
    memory_text = content

    if len(parts) == 2 and parts[1].isdigit():
        importance = int(parts[1])
        memory_text = parts[0]

    if attachments:
        attachment = attachments[0]
        if "image" in attachment.content_type:
            processing_msg = await message.channel.send(f"⏳ **Analyzing image...**")
            try:
                image_data = requests.get(attachment.url).content
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    tmp.write(image_data)
                    tmp_path = tmp.name
                
                description = await caption_image(tmp_path, prompt="Write a long, detailed description of the person shown in this image. Focus on their physical traits — including facial structure, skin tone and texture, hair color, length, and style, eye color and shape, eyebrows, nose, lips, jawline, and any visible distinguishing marks such as freckles, moles, scars, or tattoos. Then, add a brief impression of their disposition or character as it might be inferred from their features — for example, whether they seem calm, curious, confident, mischievous, or kind. Describe them as if explaining the person to someone who has never seen them. Ignore clothing, background, pose, lighting, and facial expression.")
                os.unlink(tmp_path)

                if description:
                    memory_text += f"\n\n**Visual Description:** {description}"
                    await processing_msg.edit(content="🖼️ Image analyzed and added to memory.")
                else:
                    await processing_msg.edit(content="⚠️ Could not analyze the image.")

            except Exception as e:
                logging.error(f"Error processing image for memory: {e}")
                await processing_msg.edit(content="❌ An error occurred while processing the image.")

    if memory_text:
        await memory_manager.add_semantic_memory(user_id, memory_text, importance)
        await message.channel.send(f"✓ Memory stored (importance: {importance}/10)")

async def remember_global_command(message, user_id):
    """Add important global memory (admin only): !rememberglobal <text> [importance]"""
    if user_id != "766054890207313931":
        await message.channel.send("❌ This command is restricted to the bot owner.")
        return

    if not is_admin(message):
        await message.channel.send("❌ Only administrators can set global memories.")
        return
    
    if not message.guild:
        await message.channel.send("❌ Global memories can only be set in servers, not DMs.")
        return
    
    content = message.content[len("!rememberglobal"):].strip()
    
    if not content:
        await message.channel.send("Usage: `!rememberglobal <memory text> [importance]`\nExample: `!rememberglobal Server founded Jan 2024 8`")
        return
    
    # Check for importance at the end
    parts = content.rsplit(maxsplit=1)
    importance = 5
    memory_text = content
    
    if len(parts) == 2 and parts[1].isdigit():
        importance = int(parts[1])
        memory_text = parts[0]
    
    server_id = str(message.guild.id)
    await memory_manager.add_global_memory(server_id, memory_text, importance)
    await message.channel.send(f"🌐 Global memory stored (importance: {importance}/10)")

async def my_profile_command(message, user_id):
    """Display user's stored profile and memories."""
    memory = await memory_manager.get_memory(user_id)
    
    embed_parts = []
    
    # Profile facts
    long_term = memory.get("long_term", {})
    if long_term:
        embed_parts.append("**📋 Profile:**")
        for category, facts in long_term.items():
            if facts:
                fact_list = [f"  • {k}: {v['value']}" for k, v in list(facts.items())[:5]]
                embed_parts.append(f"*{category.title()}:*\n" + "\n".join(fact_list))
    
    # Important memories
    semantic = memory.get("semantic_memory", [])
    if semantic:
        embed_parts.append("\n**💭 Important Memories:**")
        for i, mem in enumerate(semantic, 1):
            embed_parts.append(f"{i}. {mem['content'][:100]} (⭐{mem['importance']})")
    
    # Statistics
    stats = memory.get("statistics", {})
    if stats.get("total_messages"):
        embed_parts.append(f"\n**📊 Stats:** {stats['total_messages']} messages")
    
    if embed_parts:
        response = "\n".join(embed_parts)
        # Discord message limit is 2000 chars
        if len(response) > 1900:
            response = response[:1900] + "..."
        await message.channel.send(response)
    else:
        await message.channel.send("📭 No profile data yet! Use `!setfact`, `!remember`, or just chat with me.")

async def global_command(message, user_id):
    """Display server's global memory (anyone can view)."""
    if user_id != "766054890207313931":
        await message.channel.send("❌ This command is restricted to the bot owner.")
        return

    if not message.guild:
        await message.channel.send("❌ This command only works in servers, not DMs.")
        return
    
    server_id = str(message.guild.id)
    memory = await memory_manager.get_global_memory(server_id)
    
    embed_parts = []
    
    # Global facts
    global_facts = memory.get("global_facts", {})
    if global_facts:
        embed_parts.append("**🌐 Server Facts:**")
        for category, facts in global_facts.items():
            if facts:
                fact_list = [f"  • {k}: {v['value']}" for k, v in list(facts.items())[:10]]
                embed_parts.append(f"*{category.title()}:*\n" + "\n".join(fact_list))
    
    # Global memories
    global_mem = memory.get("global_memory", [])
    if global_mem:
        embed_parts.append("\n**📌 Server Memories:**")
        for i, mem in enumerate(global_mem, 1):
            embed_parts.append(f"{i}. {mem['content'][:150]} (⭐{mem['importance']})")
    
    if embed_parts:
        response = "\n".join(embed_parts)
        if len(response) > 1900:
            response = response[:1900] + "..."
        await message.channel.send(response)
    else:
        await message.channel.send("📭 No global server memory yet! Admins can use `!setglobal` and `!rememberglobal`.")

async def forget_command(message, user_id):
    """Clear all memory: !forget [confirm]"""
    args = message.content.split()[1:]
    
    if args and args[0].lower() == "confirm":
        # Backup the user's memory file
        await memory_manager.store.backup_user_file(user_id)
        
        # Reset to default
        default_memory = memory_manager.store._get_default_memory()
        await memory_manager.update_memory(user_id, default_memory)
        memory_manager._cache.pop(user_id, None)
        await message.channel.send("🗑️ All your memories have been cleared and a backup has been created.")
    else:
        await message.channel.send("⚠️ This will delete all stored data. A backup will be created. Use `!forget confirm` to proceed.")

async def memory_stats_command(message, user_id):
    """Show detailed memory statistics."""
    memory = await memory_manager.get_memory(user_id)
    
    short_term_count = len(memory.get("short_term", []))
    fact_count = sum(len(facts) for facts in memory.get("long_term", {}).values())
    semantic_count = len(memory.get("semantic_memory", []))
    total_msgs = memory.get("statistics", {}).get("total_messages", 0)
    
    last_interaction = memory.get("statistics", {}).get("last_interaction")
    if last_interaction:
        try:
            last_dt = dt.fromisoformat(last_interaction)
            last_str = last_dt.strftime("%Y-%m-%d %H:%M")
        except:
            last_str = "Unknown"
    else:
        last_str = "Never"
    
    stats_text = f"""**🧠 Memory Statistics**
├ Short-term messages: {short_term_count}/{MemoryConfig.MAX_SHORT_TERM_MESSAGES}
├ Profile facts: {fact_count}/{MemoryConfig.MAX_LONG_TERM_FACTS}
├ Important memories: {semantic_count}/{MemoryConfig.MAX_MEMORIES_PER_USER}
├ Total messages: {total_msgs}
└ Last interaction: {last_str}

💡 Use `!helpmemory` for available commands."""
    
    await message.channel.send(stats_text)

async def clear_context_command(message, user_id):
    """Clear the bot's short-term memory for the user."""
    await memory_manager.clear_short_term_memory(user_id)
    await message.channel.send("🧠 My short-term memory has been cleared. Let's start over!")

async def delete_profile_command(message, user_id):
    """Delete a specific memory from your profile."""
    args = message.content.split()[1:]
    if not args:
        await message.channel.send("Usage: `!deleteprofile <number>` or `!deleteprofile all confirm`")
        return

    if args[0].lower() == "all" and len(args) > 1 and args[1].lower() == "confirm":
        memory = await memory_manager.get_memory(user_id)
        memory["semantic_memory"] = []
        await memory_manager.update_memory(user_id, memory)
        await message.channel.send("🗑️ All your memories have been cleared.")
        return

    try:
        index = int(args[0]) - 1
        success = await memory_manager.delete_semantic_memory(user_id, index)
        if success:
            await message.channel.send(f"🗑️ Memory #{index + 1} has been deleted.")
        else:
            await message.channel.send("❌ Invalid memory number.")
    except ValueError:
        await message.channel.send("❌ Invalid command. Please use a number or `all confirm`.")

async def delete_global_command(message, user_id):
    """Delete a specific global memory."""
    if user_id != "766054890207313931":
        await message.channel.send("❌ This command is restricted to the bot owner.")
        return

    if not message.guild:
        await message.channel.send("❌ This command only works in servers, not DMs.")
        return

    args = message.content.split()[1:]
    if not args:
        await message.channel.send("Usage: `!deleteglobal <number>` or `!deleteglobal all confirm`")
        return

    server_id = str(message.guild.id)

    if args[0].lower() == "all" and len(args) > 1 and args[1].lower() == "confirm":
        memory = await memory_manager.get_global_memory(server_id)
        memory["global_memory"] = []
        await memory_manager.update_global_memory(server_id, memory)
        await message.channel.send("🗑️ All global memories have been cleared.")
        return

    try:
        index = int(args[0]) - 1
        success = await memory_manager.delete_global_memory(server_id, index)
        if success:
            await message.channel.send(f"🗑️ Global memory #{index + 1} has been deleted.")
        else:
            await message.channel.send("❌ Invalid memory number.")
    except ValueError:
        await message.channel.send("❌ Invalid command. Please use a number or `all confirm`.")

async def profile_mod_command(message, user_id):
    """Display another user's stored profile and memories (owner only)."""
    if user_id != "766054890207313931":
        await message.channel.send("❌ This command is restricted to the bot owner.")
        return

    args = message.content.split(maxsplit=1)
    if len(args) < 2:
        await message.channel.send("Usage: `!profilemod <user_mention_or_id>`")
        return

    target_user_id = None
    if message.mentions:
        target_user_id = str(message.mentions[0].id)
    else:
        try:
            target_user_id = str(int(args[1]))
        except ValueError:
            await message.channel.send("❌ Invalid user ID. Please mention a user or provide their ID.")
            return

    memory = await memory_manager.get_memory(target_user_id)
    
    embed_parts = []
    
    # Profile facts
    long_term = memory.get("long_term", {})
    if long_term:
        embed_parts.append(f"**📋 Profile for user {target_user_id}:**")
        for category, facts in long_term.items():
            if facts:
                fact_list = [f"  • {k}: {v['value']}" for k, v in list(facts.items())[:10]]
                embed_parts.append(f"*{category.title()}:*\n" + "\n".join(fact_list))
    
    # Important memories
    semantic = memory.get("semantic_memory", [])
    if semantic:
        embed_parts.append("\n**💭 Important Memories:**")
        for i, mem in enumerate(semantic, 1):
            embed_parts.append(f"{i}. {mem['content'][:100]} (⭐{mem['importance']})")
    
    # Statistics
    stats = memory.get("statistics", {})
    if stats.get("total_messages"):
        embed_parts.append(f"\n**📊 Stats:** {stats['total_messages']} messages")
    
    if embed_parts:
        response = "\n".join(embed_parts)
        # Discord message limit is 2000 chars
        if len(response) > 1900:
            response = response[:1900] + "..."
        await message.channel.send(response)
    else:
        await message.channel.send(f"📭 No profile data yet for user {target_user_id}!")

async def clear_context_mod_command(message, user_id):
    """Clear another user's short-term memory (owner only)."""
    if user_id != "766054890207313931":
        await message.channel.send("❌ This command is restricted to the bot owner.")
        return

    args = message.content.split(maxsplit=1)
    if len(args) < 2:
        await message.channel.send("Usage: `!clearcontextmod <user_mention_or_id>`")
        return

    target_user_id = None
    if message.mentions:
        target_user_id = str(message.mentions[0].id)
    else:
        try:
            target_user_id = str(int(args[1]))
        except ValueError:
            await message.channel.send("❌ Invalid user ID. Please mention a user or provide their ID.")
            return

    await memory_manager.clear_short_term_memory(target_user_id)
    await message.channel.send(f"🧠 Short-term memory for user {target_user_id} has been cleared.")

# Command registry
commands = {
    "helpmemory": help_memory_command,
    "memhelp": help_memory_command,
    "setfact": set_fact_command,
    "setglobal": set_global_fact_command,
    "remember": remember_command,
    "rememberglobal": remember_global_command,
    "myprofile": my_profile_command,
    "profile": my_profile_command,
    "global": global_command,
    "forget": forget_command,
    "memorystats": memory_stats_command,
    "memstats": memory_stats_command,
    "cc": clear_context_command,
    "clearcontext": clear_context_command,
    "deleteprofile": delete_profile_command,
    "dp": delete_profile_command,
    "deleteglobal": delete_global_command,
    "profilemod": profile_mod_command,
    "clearcontextmod": clear_context_mod_command,
    "ccmod": clear_context_mod_command,
}

# ============================================================================
# PLUGIN HOOKS
# ============================================================================

async def on_bot_ready(discord_client):
    """Initialize plugin on bot startup."""
    memory_manager.store._ensure_directories()
    logging.info("✓ Enhanced Memory Plugin v2.0 loaded")
    logging.info(f"  - {len(commands)} commands registered")
    logging.info("  - Global memory: enabled")
    logging.info("  - Multi-user context: enabled")

async def on_message_received(message):
    """Track messages for context (if not a command)."""
    if not message.author.bot and not message.content.startswith('!'):
        user_id = str(message.author.id)
        # Only store non-command messages for context
        await memory_manager.add_short_term(
            user_id, 
            message.content[:200],  # Truncate long messages
            role="user"
        )

async def before_llm_call(messages, original_message):
    """Inject memory context into system prompt before LLM call."""
    user_id = str(original_message.author.id)
    server_id = str(original_message.guild.id) if original_message.guild else None
    
    # Extract mentioned users from the message
    mentioned_user_ids = memory_manager.extract_mentioned_users(
        original_message.content, 
        original_message
    )
    
    # Get optimized context with multi-user support
    memory_context = await memory_manager.get_context_for_llm(
        user_id,
        server_id=server_id,
        mentioned_user_ids=mentioned_user_ids
    )
    
    if memory_context and messages and messages[0]["role"] == "system":
        # Inject memory into system prompt
        memory_injection = f"\n\n--- Memory Context ---\n{memory_context}\n--- End Memory Context ---\n\nUse this information naturally when relevant. When users are mentioned, you have context about them too. Don't explicitly mention you're using memory unless asked."
        
        messages[0]["content"] += memory_injection
    
    return messages

async def after_llm_call(response_text, original_message):
    """Store bot responses in short-term memory."""
    user_id = str(original_message.author.id)
    
    # Store bot's response in context
    await memory_manager.add_short_term(
        user_id,
        response_text[:200],  # Truncate long responses
        role="assistant"
    )
    
    # Periodic cleanup (every ~20 messages)
    memory = await memory_manager.get_memory(user_id)
    if memory.get("statistics", {}).get("total_messages", 0) % 20 == 0:
        await memory_manager.cleanup_old_memories(user_id)

# ============================================================================
# SETUP
# ============================================================================

def setup():
    """Required setup function for plugin system."""
    return {
        "name": "Enhanced Memory",
        "version": "2.0",
        "description": "Production-ready memory system with multi-user context, global facts, and smart retrieval",
        "author": "Discord Bot Framework",
        "commands": list(commands.keys()),
        "hooks": ["on_bot_ready", "on_message_received", "before_llm_call", "after_llm_call"]
    }