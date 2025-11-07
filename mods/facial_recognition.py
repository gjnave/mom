"""
Facial Recognition Plugin
"""

import logging
import os
import pickle
import face_recognition
import aiohttp
import asyncio

# ============================================================================
# Face Recognition Engine
# ============================================================================

class FaceEngine:
    def __init__(self, storage_path="mods/facial-recognition/face_data.pkl"):
        self.known_faces = {}
        self.storage_file = storage_path
        self.load_faces()

    def load_faces(self):
        """Load stored face data"""
        if os.path.exists(self.storage_file):
            with open(self.storage_file, 'rb') as f:
                self.known_faces = pickle.load(f)
            logging.info(f"Loaded {len(self.known_faces)} known faces")
        else:
            logging.info("No existing face data found")

    def save_faces(self):
        """Save face data"""
        os.makedirs(os.path.dirname(self.storage_file), exist_ok=True)
        with open(self.storage_file, 'wb') as f:
            pickle.dump(self.known_faces, f)
        logging.info("Face data saved")

    def add_face(self, image_path, name):
        """Add a face to the database"""
        if not os.path.exists(image_path):
            logging.error(f"Image file {image_path} not found")
            return False

        try:
            image = face_recognition.load_image_file(image_path)
            encodings = face_recognition.face_encodings(image)

            if not encodings:
                logging.warning("No faces found in the image")
                return False

            if len(encodings) > 1:
                logging.warning("Multiple faces found. Using the first face.")

            if name not in self.known_faces:
                self.known_faces[name] = []

            self.known_faces[name].append(encodings[0])
            self.save_faces()
            logging.info(f"Added face for: {name}")
            return True

        except Exception as e:
            logging.error(f"Error adding face: {e}")
            return False

    def recognize_face(self, image_path):
        """Recognize faces in an image"""
        if not os.path.exists(image_path):
            logging.error(f"Image file {image_path} not found")
            return None, None

        try:
            test_image = face_recognition.load_image_file(image_path)
            test_encodings = face_recognition.face_encodings(test_image)

            if not test_encodings:
                logging.warning("No faces found in the test image")
                return None, None

            known_encodings = []
            known_names = []

            for name, encodings in self.known_faces.items():
                for encoding in encodings:
                    known_encodings.append(encoding)
                    known_names.append(name)

            if not known_encodings:
                return None, None

            for test_encoding in test_encodings:
                matches = face_recognition.compare_faces(known_encodings, test_encoding, tolerance=0.6)
                face_distances = face_recognition.face_distance(known_encodings, test_encoding)
                best_match_index = face_distances.argmin()

                if matches[best_match_index]:
                    confidence = (1 - face_distances[best_match_index]) * 100
                    return known_names[best_match_index], confidence

            return None, None

        except Exception as e:
            logging.error(f"Error during recognition: {e}")
            return None, None

    def list_faces(self):
        """List all stored faces"""
        if not self.known_faces:
            return "No faces stored yet"

        face_list = "Stored faces:\n"
        for name, encodings in self.known_faces.items():
            face_list += f"  {name}: {len(encodings)} image(s)\n"
        return face_list

    def delete_face(self, name):
        """Delete a face profile"""
        if name in self.known_faces:
            del self.known_faces[name]
            self.save_faces()
            return True
        return False

face_engine = FaceEngine()

# ============================================================================
# CUSTOM COMMANDS
# ============================================================================
async def set_image_command(message, user_id):
    """Command: !setimage or !setimage <userid>"""
    parts = message.content.split()
    target_user_id = ""
    is_owner = message.author.id == message.guild.owner_id

    if len(parts) > 1:
        if not is_owner:
            await message.channel.send("Only the server owner can set an image for another user.")
            return
        target_user_id = parts[1].strip('<@!>')
    else:
        target_user_id = str(user_id)

    if not message.attachments:
        await message.channel.send("Please attach an image to use this command.")
        return

    attachment = message.attachments[0]
    temp_image_path = f"mods/facial-recognition/temp_{attachment.filename}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                if resp.status == 200:
                    with open(temp_image_path, 'wb') as f:
                        f.write(await resp.read())

        if face_engine.add_face(temp_image_path, target_user_id):
            await message.channel.send(f"Face image stored for <@{target_user_id}>.")
        else:
            await message.channel.send("Could not store the face. Make sure the image is clear.")
    finally:
        if os.path.exists(temp_image_path):
            os.remove(temp_image_path)

async def list_faces_command(message, user_id):
    """Command: !listfaces"""
    if message.author.id != message.guild.owner_id:
        await message.channel.send("Only the server owner can use this command.")
        return

    await message.channel.send(face_engine.list_faces())

async def match_image_command(message, user_id):
    """Command: !matchimage or !matchface"""
    if not message.attachments:
        await message.channel.send("Please attach an image to use this command.")
        return

    attachment = message.attachments[0]
    temp_image_path = f"mods/facial-recognition/temp_{attachment.filename}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                if resp.status == 200:
                    with open(temp_image_path, 'wb') as f:
                        f.write(await resp.read())

        match, confidence = face_engine.recognize_face(temp_image_path)

        if match:
            await message.channel.send(f"Match found: <@{match}> with {confidence:.1f}% confidence.")
        else:
            await message.channel.send("No match found.")
    finally:
        if os.path.exists(temp_image_path):
            os.remove(temp_image_path)

async def delete_image_command(message, user_id):
    """Command: !deleteimage <userid>"""
    if message.author.id != message.guild.owner_id:
        await message.channel.send("Only the server owner can use this command.")
        return

    parts = message.content.split()
    if len(parts) < 2:
        await message.channel.send("Please specify a user ID to delete.")
        return

    target_user_id = parts[1].strip('<@!>')
    if face_engine.delete_face(target_user_id):
        await message.channel.send(f"Image profile for <@{target_user_id}> has been deleted.")
    else:
        await message.channel.send(f"No image profile found for <@{target_user_id}>.")

commands = {
    "setimage": set_image_command,
    "listfaces": list_faces_command,
    "matchimage": match_image_command,
    "matchface": match_image_command,
    "deleteimage": delete_image_command,
}

# ============================================================================
# HOOK IMPLEMENTATIONS
# ============================================================================

async def on_bot_ready(discord_client):
    """
    Called when the bot is ready and connected to Discord.
    """
    logging.info("Facial Recognition plugin loaded successfully!")

async def on_message_received(message):
    """
    Called for every message the bot receives.
    """
    if "who is this" in message.content.lower() or "who is in this image" in message.content.lower():
        if message.attachments:
            await match_image_command(message, message.author.id)
            return False
    return None

# ============================================================================
# SETUP (REQUIRED)
# =================================_===========================================

def setup():
    """
    REQUIRED: This function must return a dict with plugin metadata.
    """
    return {
        "name": "Facial Recognition",
        "version": "1.0",
        "description": "A plugin for storing and recognizing faces.",
        "author": "Jules"
    }
