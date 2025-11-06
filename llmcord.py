import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime as dt, timedelta
import json
import logging
import requests
from typing import Optional
import re
import discord
from openai import AsyncOpenAI
import os
import importlib.util
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

with open("config.json", "r", encoding="utf-8") as file:
    config = json.load(file)

OTHER_BOT_IDS = ["1385978035861459047", "1294381286294818816"]

llm_settings = config["llm_settings"]
discord_settings = config["discord_settings"]
provider, model = llm_settings["model"].split("/", 1)
base_url = llm_settings["providers"][provider]["base_url"]
api_key = llm_settings["providers"][provider].get("api_key", "None")
system_prompt = llm_settings["system_prompt"]
# Voice connection optimization
discord.voice_client.VOICE_RECONNECT_DELAY = 1.0
discord.voice_client.VOICE_CONNECT_TIMEOUT = 10.0

# Reduce voice packet frequency for stability
os.environ['DISCORD_VOICE_SEND_OPUS'] = 'false'

try:
    with open("user_data.json", "r") as file:
        user_data = json.load(file)
except FileNotFoundError:
    user_data = {}

user_data_lock = asyncio.Lock()


intents = discord.Intents.default()
intents.message_content = True
activity = discord.CustomActivity(name=discord_settings["status_message"][:128])
discord_client = discord.Client(intents=intents, activity=activity)
openai_client = AsyncOpenAI(base_url=base_url, api_key=api_key)

ALLOWED_CHANNEL_IDS = discord_settings["allowed_channel_ids"]
ALLOWED_ROLE_IDS = discord_settings["allowed_role_ids"]
ALLOWED_CHANNEL_TYPES = (discord.ChannelType.text, discord.ChannelType.public_thread, discord.ChannelType.private_thread, discord.ChannelType.private)
MAX_TEXT = discord_settings["max_text"]
MAX_IMAGES = discord_settings["max_images"]
MAX_MESSAGES = discord_settings["max_messages"]
USE_PLAIN_RESPONSES = discord_settings["use_plain_responses"]
STREAMING_INDICATOR = " ⚪"
EDIT_DELAY_SECONDS = 1
MAX_MESSAGE_LENGTH = 3000 if USE_PLAIN_RESPONSES else (4096 - len(STREAMING_INDICATOR))
EMBED_COLOR_COMPLETE = discord.Color.dark_green()
EMBED_COLOR_INCOMPLETE = discord.Color.orange()
MAX_MESSAGE_NODES = 100
LLM_ACCEPTS_IMAGES = any(x in model for x in ("gpt-4o", "claude-3", "gemini", "pixtral", "llava", "vision", "llama3.2-vision"))
LLM_ACCEPTS_NAMES = "openai/" in llm_settings["model"]
ALLOWED_FILE_TYPES = ("image", "text")

MESSAGE_COUNT = 0
SPEAK_EVERY_TURNS = 50
converse_state = {"active": False, "exchanges_left": 0, "channel_id": None}

loaded_plugins = {}
plugin_hooks = {
    "on_bot_ready": [],
    "on_message_received": [],
    "process_attachment": [],
    "before_llm_call": [],
    "after_llm_response": [],
    "custom_commands": {},
}

@dataclass
class MsgNode:
    data: dict = field(default_factory=dict)
    next_msg: Optional[discord.Message] = None
    too_much_text: bool = False
    too_many_images: bool = False
    has_bad_attachments: bool = False
    fetch_next_failed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

msg_nodes = {}
last_task_time = None

def load_plugins():
    mods_path = Path("mods")
    if not mods_path.exists():
        os.makedirs(mods_path)
        logging.info("Created mods folder")
        return
    
    logging.info(f"Loading plugins from: {mods_path.resolve()}")
    for mod_file in mods_path.glob("*.py"):
        if mod_file.name.startswith("_"):
            logging.info(f"Skipping plugin with underscore: {mod_file.name}")
            continue
        
        try:
            module_name = mod_file.stem
            logging.info(f"Loading plugin: {module_name}")
            spec = importlib.util.spec_from_file_location(module_name, mod_file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            
            if hasattr(module, "setup"):
                plugin_info = module.setup()
                loaded_plugins[module_name] = {"module": module, "info": plugin_info}
                logging.info(f"Plugin {module_name} has setup function.")
                
                for hook_name in plugin_hooks.keys():
                    if hook_name == "custom_commands":
                        if hasattr(module, "commands"):
                            for cmd_name, cmd_func in module.commands.items():
                                plugin_hooks["custom_commands"][cmd_name] = cmd_func
                                logging.info(f"  - Registered command: !{cmd_name}")
                    elif hasattr(module, hook_name):
                        plugin_hooks[hook_name].append(getattr(module, hook_name))
                        logging.info(f"  - Registered hook: {hook_name}")
                
                logging.info(f"✓ Loaded plugin: {module_name} - {plugin_info.get('description', 'No description')}")
            else:
                logging.warning(f"⚠ Plugin {module_name} missing setup() function")
        
        except Exception as e:
            logging.error(f"✗ Failed to load plugin {mod_file.name}: {e}")

def get_system_prompt():
    system_prompt_extras = [f"Today's date: {dt.now().strftime('%B %d %Y')}."]
    if LLM_ACCEPTS_NAMES:
        system_prompt_extras += ["User's names are their Discord IDs and should be typed as '<@ID>'."]
    system_prompt_extras += ["You are in a group chat. Respond to the latest message while considering relevant conversation context."]
    return {"role": "system", "content": "\n".join([system_prompt] + system_prompt_extras)}

@discord_client.event
async def on_ready():
    print(f"Bot is ready as {discord_client.user.name}")
    if discord_settings["client_id"] != 123456789:
        print(f"\nBOT INVITE URL:\nhttps://discord.com/api/oauth2/authorize?client_id={discord_settings['client_id']}&permissions=412317273088&scope=bot\n")
    
    for hook in plugin_hooks["on_bot_ready"]:
        try:
            await hook(discord_client)
        except Exception as e:
            logging.error(f"Error in on_bot_ready hook: {e}")
    
    asyncio.create_task(birthday_checker())



async def handle_bot_conversation(channel, exchanges, user_id, initial_message):
    global converse_state
    converse_state = {"active": True, "exchanges_left": exchanges, "channel_id": channel.id}
    last_message = initial_message

    while converse_state["exchanges_left"] > 0 and converse_state["active"]:
        conversation_history = []
        curr_node = msg_nodes.get(last_message.id)
        if curr_node and curr_node.data:
            conversation_history.append(curr_node.data)

        messages = [get_system_prompt()] + conversation_history
        response_content = ""
        try:
            async with channel.typing():
                async for chunk in await openai_client.chat.completions.create(model=model, messages=messages, stream=True, **llm_settings["extra_api_parameters"]):
                    response_content += chunk.choices[0].delta.content or ""
            response_msg = await channel.send(response_content)
            msg_nodes[response_msg.id] = MsgNode(data={"content": response_content, "role": "assistant", "name": str(discord_client.user.id) if LLM_ACCEPTS_NAMES else None})
            last_message = response_msg
            converse_state["exchanges_left"] -= 1
            await asyncio.sleep(1)
        except Exception as e:
            logging.error(f"Error in bot conversation: {e}")
            await channel.send("Oops, something broke during our chat!")
            break

    converse_state = {"active": False, "exchanges_left": 0, "channel_id": None}

@discord_client.event
async def on_message(new_msg):
    global msg_nodes, last_task_time, converse_state, MESSAGE_COUNT

    if new_msg.author.id == discord_client.user.id:
        return

    MESSAGE_COUNT += 1
    logging.info(f"Message received: {new_msg.content}")

    stop_processing = False
    for hook in plugin_hooks["on_message_received"]:
        try:
            result = await hook(new_msg)
            if result is False:
                stop_processing = True
                break
        except Exception as e:
            logging.error(f"Error in on_message_received hook: {e}")

    if stop_processing:
        return

    if new_msg.content.startswith("//"):
        return

    if new_msg.content.startswith("!"):
        command = new_msg.content[1:].split()[0].lower()
        user_id = str(new_msg.author.id)
        
        if command in plugin_hooks["custom_commands"]:
            try:
                await plugin_hooks["custom_commands"][command](new_msg, user_id)
                return
            except Exception as e:
                logging.error(f"Error in custom command {command}: {e}")
                await new_msg.channel.send(f"Oops, something went wrong with that command, sweetie!")
                return
        
        if command == "plugins":
            if loaded_plugins:
                response = "**Loaded Plugins:**\n"
                for name, data in loaded_plugins.items():
                    info = data["info"]
                    response += f"• **{name}** v{info.get('version', '1.0')}: {info.get('description', 'No description')}\n"
            else:
                response = "No plugins loaded, honey."
            await new_msg.channel.send(response)
            return
        elif command == "converse":
            args = new_msg.content.split()[1:]
            if len(args) == 1:
                try:
                    exchanges = int(args[0])
                    if 1 <= exchanges <= 10:
                        await new_msg.channel.send(f"Starting conversation between bots for {exchanges} exchanges!")
                        await handle_bot_conversation(new_msg.channel, exchanges, user_id, new_msg)
                    else:
                        await new_msg.channel.send("Please specify a number between 1 and 10, dear.")
                except ValueError:
                    await new_msg.channel.send("Please provide a valid number, sweetie!")
            else:
                await new_msg.channel.send("Usage: !converse <number>, honey!")
            return

        return

    bot_mentioned = discord_client.user in new_msg.mentions
    name_trigger = re.search(rf'\b{re.escape(discord_client.user.name)}\b', new_msg.content, re.IGNORECASE)
    mom_trigger = re.search(r'\bmom\b', new_msg.content, re.IGNORECASE)
    is_reply_to_bot = False
    if new_msg.reference:
        try:
            referenced_msg = new_msg.reference.cached_message or await new_msg.channel.fetch_message(new_msg.reference.message_id)
            if referenced_msg.author == discord_client.user:
                is_reply_to_bot = True
        except (discord.NotFound, discord.HTTPException):
            logging.exception("Error fetching referenced message")
    is_new_command = new_msg.content.startswith("!new")

    is_from_other_bot = (str(new_msg.author.id) in OTHER_BOT_IDS and new_msg.author.id != discord_client.user.id and converse_state["active"] and converse_state["channel_id"] == new_msg.channel.id)
    random_response = (MESSAGE_COUNT % SPEAK_EVERY_TURNS == 0)

    if not (bot_mentioned or name_trigger or mom_trigger or is_reply_to_bot or is_new_command or is_from_other_bot or random_response):
        return

    if ALLOWED_CHANNEL_IDS and str(new_msg.channel.id) not in ALLOWED_CHANNEL_IDS:
        return
    if ALLOWED_ROLE_IDS and isinstance(new_msg.author, discord.Member):
        if not any(role.id in ALLOWED_ROLE_IDS for role in new_msg.author.roles):
            return
    if new_msg.channel.type not in ALLOWED_CHANNEL_TYPES:
        return

    ignore_history = is_new_command
    if is_new_command:
        effective_content = new_msg.content[len("!new "):].strip() if len(new_msg.content) > len("!new ") else ""
    else:
        effective_content = new_msg.content

    user_id = str(new_msg.author.id)
    conversation_history = []
    user_warnings = set()

    if not ignore_history and new_msg.reference:
        current_msg = new_msg
        while current_msg.reference:
            try:
                ref_msg = current_msg.reference.cached_message or await current_msg.channel.fetch_message(current_msg.reference.message_id)
                ref_node = msg_nodes.setdefault(ref_msg.id, MsgNode())
                async with ref_node.lock:
                    if not ref_node.data:
                        good_attachments = {type: [att for att in ref_msg.attachments if att.content_type and type in att.content_type and att.size <= 10_000_000] for type in ALLOWED_FILE_TYPES}
                        image_parts = []
                        text_parts = []
                        if ref_msg.content:
                            text_parts.append(ref_msg.content)
                        
                        for att in good_attachments["image"][:MAX_IMAGES]:
                            processed = False
                            for hook in plugin_hooks["process_attachment"]:
                                try:
                                    result = await hook(att, LLM_ACCEPTS_IMAGES, message=ref_msg)
                                    if result:
                                        if "caption" in result and result["caption"]:
                                            text_parts.append(f"[Image description: {result['caption']}]")
                                        if "image_data" in result and LLM_ACCEPTS_IMAGES:
                                            image_parts.append({"type": "image_url", "image_url": {"url": f"data:{att.content_type};base64,{result['image_data']}"}})
                                        processed = True
                                        break
                                except Exception as e:
                                    logging.error(f"Error in process_attachment hook: {e}")
                            
                            if not processed and LLM_ACCEPTS_IMAGES:
                                image_data = base64.b64encode(requests.get(att.url).content).decode('utf-8')
                                image_parts.append({"type": "image_url", "image_url": {"url": f"data:{att.content_type};base64,{image_data}"}})
                        
                        text_parts += [embed.description for embed in ref_msg.embeds if embed.description]
                        text_parts += [requests.get(att.url).text for att in good_attachments["text"] if att.size <= 10_000_000]
                        text = "\n".join(text_parts)
                        if len(text) > MAX_TEXT:
                            text = text[:MAX_TEXT]
                            ref_node.too_much_text = True
                        
                        if image_parts:
                            content = ([{"type": "text", "text": text}] if text else []) + image_parts
                        else:
                            content = text
                        
                        data = {"content": content, "role": "assistant" if ref_msg.author == discord_client.user else "user"}
                        if LLM_ACCEPTS_NAMES:
                            data["name"] = str(ref_msg.author.id)
                        ref_node.data = data
                if ref_node.data["content"]:
                    conversation_history.append(ref_node.data)
                current_msg = ref_msg
            except (discord.NotFound, discord.HTTPException):
                break
        conversation_history.reverse()
    
    elif not ignore_history:
        async for prev_msg in new_msg.channel.history(before=new_msg, limit=MAX_MESSAGES):
            if prev_msg.author == discord_client.user and not prev_msg.content and not prev_msg.embeds:
                continue
            time_diff = (new_msg.created_at - prev_msg.created_at).total_seconds()
            if time_diff > 300:
                break
            prev_node = msg_nodes.setdefault(prev_msg.id, MsgNode())
            async with prev_node.lock:
                if not prev_node.data:
                    good_attachments = {type: [att for att in prev_msg.attachments if att.content_type and type in att.content_type and att.size <= 10_000_000] for type in ALLOWED_FILE_TYPES}
                    image_parts = []
                    text_parts = []
                    if prev_msg.content:
                        text_parts.append(prev_msg.content)
                    
                    for att in good_attachments["image"][:MAX_IMAGES]:
                        processed = False
                        for hook in plugin_hooks["process_attachment"]:
                            try:
                                result = await hook(att, LLM_ACCEPTS_IMAGES)
                                if result:
                                    if "caption" in result and result["caption"]:
                                        text_parts.append(f"[Image description: {result['caption']}]")
                                    if "image_data" in result and LLM_ACCEPTS_IMAGES:
                                        image_parts.append({"type": "image_url", "image_url": {"url": f"data:{att.content_type};base64,{result['image_data']}"}})
                                    processed = True
                                    break
                            except Exception as e:
                                logging.error(f"Error in process_attachment hook: {e}")
                            
                            if not processed and LLM_ACCEPTS_IMAGES:
                                image_data = base64.b64encode(requests.get(att.url).content).decode('utf-8')
                                image_parts.append({"type": "image_url", "image_url": {"url": f"data:{att.content_type};base64,{image_data}"}})
                    
                    text_parts += [embed.description for embed in prev_msg.embeds if embed.description]
                    text_parts += [requests.get(att.url).text for att in good_attachments["text"] if att.size <= 10_000_000]
                    text = "\n".join(text_parts)
                    if len(text) > MAX_TEXT:
                        text = text[:MAX_TEXT]
                        prev_node.too_much_text = True
                    
                    if image_parts:
                        content = ([{"type": "text", "text": text}] if text else []) + image_parts
                    else:
                        content = text
                    
                    data = {"content": content, "role": "assistant" if prev_msg.author == discord_client.user else "user"}
                    if LLM_ACCEPTS_NAMES:
                        data["name"] = str(prev_msg.author.id)
                    prev_node.data = data
                if prev_node.data["content"]:
                    conversation_history.append(prev_node.data)
        conversation_history.reverse()

    curr_node = msg_nodes.setdefault(new_msg.id, MsgNode())
    async with curr_node.lock:
        if not curr_node.data:
            logging.info(f"Processing current message. Attachments: {len(new_msg.attachments)}")
            for att in new_msg.attachments:
                logging.info(f"  - {att.filename} ({att.content_type}, {att.size} bytes)")
            
            good_attachments = {type: [att for att in new_msg.attachments if att.content_type and type in att.content_type and att.size <= 10_000_000] for type in ALLOWED_FILE_TYPES}
            logging.info(f"Good image attachments: {len(good_attachments['image'])}")
            
            image_parts = []
            text_parts = []
            if effective_content:
                text_parts.append(effective_content)
            
            for att in good_attachments["image"][:MAX_IMAGES]:
                processed = False
                logging.info(f"Processing image attachment: {att.filename}")
                for hook in plugin_hooks["process_attachment"]:
                    try:
                        logging.info(f"Calling plugin hook: {hook.__name__}")
                        result = await hook(att, LLM_ACCEPTS_IMAGES, message=new_msg)
                        logging.info(f"Plugin result: {result}")
                        if result:
                            if "caption" in result and result["caption"]:
                                caption_text = f"[Image description: {result['caption']}]"
                                text_parts.append(caption_text)
                                logging.info(f"Added caption to text: {caption_text[:100]}...")
                            if "image_data" in result and LLM_ACCEPTS_IMAGES:
                                image_parts.append({"type": "image_url", "image_url": {"url": f"data:{att.content_type};base64,{result['image_data']}"}})
                                logging.info("Added image data to image_parts")
                            processed = True
                            break
                    except Exception as e:
                        logging.error(f"Error in process_attachment hook: {e}")
                
                if not processed and LLM_ACCEPTS_IMAGES:
                    logging.info("No plugin processed image, using default base64 encoding")
                    image_data = base64.b64encode(requests.get(att.url).content).decode('utf-8')
                    image_parts.append({"type": "image_url", "image_url": {"url": f"data:{att.content_type};base64,{image_data}"}})
            
            text_parts += [embed.description for embed in new_msg.embeds if embed.description]
            text_parts += [requests.get(att.url).text for att in good_attachments["text"] if att.size <= 10_000_000]
            text = "\n".join(text_parts)
            if len(text) > MAX_TEXT:
                text = text[:MAX_TEXT]
                curr_node.too_much_text = True
                user_warnings.add(f"⚠️ Max {MAX_TEXT:,} characters per message")
            
            if image_parts:
                content = ([{"type": "text", "text": text}] if text else []) + image_parts
            else:
                content = text
            
            data = {"content": content, "role": "user"}
            if LLM_ACCEPTS_NAMES:
                data["name"] = user_id
            curr_node.data = data
            curr_node.too_many_images = len(good_attachments["image"]) > MAX_IMAGES
            curr_node.has_bad_attachments = len(new_msg.attachments) > sum(len(att_list) for att_list in good_attachments.values())
            if curr_node.too_many_images:
                user_warnings.add(f"⚠️ Max {MAX_IMAGES} image{'' if MAX_IMAGES == 1 else 's'} per message" if MAX_IMAGES > 0 else "⚠️ Can't see images")
            if curr_node.has_bad_attachments:
                user_warnings.add("⚠️ Unsupported attachments")

    if curr_node.data["content"]:
        conversation_history.append(curr_node.data)

    messages = ([get_system_prompt()] + conversation_history)[:MAX_MESSAGES + 1]
    
    for hook in plugin_hooks["before_llm_call"]:
        try:
            messages = await hook(messages, new_msg) or messages
        except Exception as e:
            logging.error(f"Error in before_llm_call hook: {e}")

    logging.info(f"Processing message (user ID: {new_msg.author.id}, channel ID: {new_msg.channel.id}, history length: {len(conversation_history)}):\n{effective_content}")

    response_msgs = []
    response_contents = []
    edit_task = None
    kwargs = dict(model=model, messages=messages, stream=True, **llm_settings["extra_api_parameters"])
    
    try:
        async with new_msg.channel.typing():
            async for curr_chunk in await openai_client.chat.completions.create(**kwargs):
                curr_content = curr_chunk.choices[0].delta.content or ""
                finish_reason = curr_chunk.choices[0].finish_reason

                if not response_contents:
                    response_contents = [""]
                    if not USE_PLAIN_RESPONSES:
                        embed = discord.Embed(description=STREAMING_INDICATOR, color=EMBED_COLOR_INCOMPLETE)
                        for warning in sorted(user_warnings):
                            embed.add_field(name=warning, value="", inline=False)
                        response_msg = await new_msg.channel.send(embed=embed)
                        msg_nodes[response_msg.id] = MsgNode()
                        await msg_nodes[response_msg.id].lock.acquire()
                        last_task_time = dt.now().timestamp()
                        response_msgs.append(response_msg)
                    else:
                        response_msg = await new_msg.channel.send(STREAMING_INDICATOR)
                        msg_nodes[response_msg.id] = MsgNode()
                        await msg_nodes[response_msg.id].lock.acquire()
                        last_task_time = dt.now().timestamp()
                        response_msgs.append(response_msg)

                if len(response_contents[-1] + curr_content) > MAX_MESSAGE_LENGTH:
                    response_contents.append("")
                    if not USE_PLAIN_RESPONSES:
                        embed = discord.Embed(description=STREAMING_INDICATOR, color=EMBED_COLOR_INCOMPLETE)
                        response_msg = await response_msgs[-1].channel.send(embed=embed)
                        msg_nodes[response_msg.id] = MsgNode()
                        await msg_nodes[response_msg.id].lock.acquire()
                        response_msgs.append(response_msg)
                    else:
                        response_msg = await response_msgs[-1].channel.send(STREAMING_INDICATOR)
                        msg_nodes[response_msg.id] = MsgNode()
                        await msg_nodes[response_msg.id].lock.acquire()
                        response_msgs.append(response_msg)

                response_contents[-1] += curr_content

                if not USE_PLAIN_RESPONSES:
                    should_update = (finish_reason is not None or (not edit_task or edit_task.done()) and dt.now().timestamp() - last_task_time >= EDIT_DELAY_SECONDS)
                    if should_update:
                        while edit_task and not edit_task.done():
                            await asyncio.sleep(0)
                        embed.description = response_contents[-1]
                        if not finish_reason:
                            embed.description += STREAMING_INDICATOR
                        embed.color = EMBED_COLOR_COMPLETE if finish_reason == "stop" else EMBED_COLOR_INCOMPLETE
                        edit_task = asyncio.create_task(response_msgs[-1].edit(embed=embed))
                        last_task_time = dt.now().timestamp()
                else:
                    should_update = (finish_reason is not None or (not edit_task or edit_task.done()) and dt.now().timestamp() - last_task_time >= EDIT_DELAY_SECONDS)
                    if should_update:
                        while edit_task and not edit_task.done():
                            await asyncio.sleep(0)
                        content = response_contents[-1]
                        if not finish_reason:
                            content += STREAMING_INDICATOR
                        edit_task = asyncio.create_task(response_msgs[-1].edit(content=content))
                        last_task_time = dt.now().timestamp()

            if USE_PLAIN_RESPONSES and not response_msgs:
                for content in response_contents:
                    response_msg = await new_msg.channel.send(content=content)
                    msg_nodes[response_msg.id] = MsgNode()
                    await msg_nodes[response_msg.id].lock.acquire()
                    response_msgs.append(response_msg)
    except Exception as e:
        logging.error(f"Error while generating response: {e}")
        await new_msg.channel.send("Oh dear, something went wrong! Try again later, sweetie.")

    full_response = "".join(response_contents)
    
    for hook in plugin_hooks["after_llm_response"]:
        try:
            await hook(new_msg, full_response)
        except Exception as e:
            logging.error(f"Error in after_llm_response hook: {e}")

    data = {"content": full_response, "role": "assistant"}
    if LLM_ACCEPTS_NAMES:
        data["name"] = str(discord_client.user.id)

    for msg in response_msgs:
        msg_nodes[msg.id].data = data
        msg_nodes[msg.id].lock.release()

    if (num_nodes := len(msg_nodes)) > MAX_MESSAGE_NODES:
        for msg_id in sorted(msg_nodes.keys())[:num_nodes - MAX_MESSAGE_NODES]:
            async with msg_nodes.setdefault(msg_id, MsgNode()).lock:
                del msg_nodes[msg_id]

async def birthday_checker():
    while True:
        now = dt.now()
        today = now.strftime("%m-%d")
        async with user_data_lock:
            for user_id, data in user_data.items():
                if "birthday" in data:
                    birthday = dt.strptime(data["birthday"], "%Y-%m-%d").strftime("%m-%d")
                    if birthday == today:
                        try:
                            user = await discord_client.fetch_user(int(user_id))
                            await user.send("Happy Birthday, sweetie! Have a wonderful day!")
                        except (discord.NotFound, discord.HTTPException):
                            logging.warning(f"Could not send birthday message to user {user_id}")
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        await asyncio.sleep((tomorrow - now).total_seconds())

async def main():
    load_plugins()
    await discord_client.start(discord_settings["bot_token"])

if __name__ == "__main__":
    asyncio.run(main())
