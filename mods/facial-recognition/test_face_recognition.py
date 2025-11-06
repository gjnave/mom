import face_recognition
import os

# Create a dummy image for testing
from PIL import Image

# Create a 100x100 black image
img = Image.new('RGB', (100, 100), color = 'black')
img.save('test_image.png')


image_path = "test_image.png"

print(f"Loading image: {image_path}")

# Load the image file
image = face_recognition.load_image_file(image_path)

# Find all the faces in the image
face_locations = face_recognition.face_locations(image)

print(f"Found {len(face_locations)} face(s) in this photograph.")

if len(face_locations) > 0:
    print("Face recognition library is working correctly.")
else:
    print("Could not find a face in the test image. This is expected for a black image.")
    print("If you have an image with a face, replace 'test_image.png' with the path to that image and run the script again.")

# Clean up the dummy image
os.remove(image_path)
