import face_recognition
import os
import pickle

class SimpleFaceTester:
    def __init__(self):
        self.known_faces = {}
        self.storage_file = "face_data.pkl"
        self.load_faces()
    
    def load_faces(self):
        """Load stored face data"""
        if os.path.exists(self.storage_file):
            with open(self.storage_file, 'rb') as f:
                self.known_faces = pickle.load(f)
            print(f"Loaded {len(self.known_faces)} known faces")
        else:
            print("No existing face data found")
    
    def save_faces(self):
        """Save face data"""
        with open(self.storage_file, 'wb') as f:
            pickle.dump(self.known_faces, f)
        print("Face data saved")
    
    def add_face(self, image_path, name):
        """Add a face to the database"""
        if not os.path.exists(image_path):
            print(f"Error: Image file {image_path} not found")
            return False
        
        try:
            image = face_recognition.load_image_file(image_path)
            encodings = face_recognition.face_encodings(image)
            
            if not encodings:
                print("No faces found in the image")
                return False
            
            if len(encodings) > 1:
                print("Multiple faces found. Using the first face.")
            
            if name not in self.known_faces:
                self.known_faces[name] = []
            
            self.known_faces[name].append(encodings[0])
            self.save_faces()
            print(f"✅ Added face for: {name}")
            return True
            
        except Exception as e:
            print(f"Error: {e}")
            return False
    
    def recognize_face(self, image_path):
        """Recognize faces in an image"""
        if not os.path.exists(image_path):
            print(f"Error: Image file {image_path} not found")
            return
        
        try:
            # Load test image
            test_image = face_recognition.load_image_file(image_path)
            test_encodings = face_recognition.face_encodings(test_image)
            
            if not test_encodings:
                print("No faces found in the test image")
                return
            
            print(f"Found {len(test_encodings)} face(s) in the image")
            print("-" * 50)
            
            # Prepare known encodings and names
            known_encodings = []
            known_names = []
            
            for name, encodings in self.known_faces.items():
                for encoding in encodings:
                    known_encodings.append(encoding)
                    known_names.append(name)
            
            # Test each face found
            for i, test_encoding in enumerate(test_encodings):
                print(f"Face {i+1}:")
                
                if not known_encodings:
                    print("  No known faces to compare against")
                    continue
                
                # Compare faces
                matches = face_recognition.compare_faces(known_encodings, test_encoding, tolerance=0.6)
                face_distances = face_recognition.face_distance(known_encodings, test_encoding)
                
                # Find best match
                best_match_index = face_distances.argmin()
                best_match_distance = face_distances[best_match_index]
                best_match_name = known_names[best_match_index]
                confidence = (1 - best_match_distance) * 100
                
                if matches[best_match_index]:
                    print(f"  ✅ Match: {best_match_name}")
                    print(f"  Confidence: {confidence:.1f}%")
                    print(f"  Distance: {best_match_distance:.4f}")
                else:
                    print(f"  ❌ No match found")
                    print(f"  Closest: {best_match_name} (confidence: {confidence:.1f}%)")
                
                print()
                
        except Exception as e:
            print(f"Error during recognition: {e}")
    
    def list_faces(self):
        """List all stored faces"""
        if not self.known_faces:
            print("No faces stored yet")
            return
        
        print("Stored faces:")
        for name, encodings in self.known_faces.items():
            print(f"  {name}: {len(encodings)} image(s)")
    
    def clear_faces(self):
        """Clear all stored faces"""
        self.known_faces = {}
        if os.path.exists(self.storage_file):
            os.remove(self.storage_file)
        print("All face data cleared")

def main():
    tester = SimpleFaceTester()
    
    while True:
        print("\n" + "="*50)
        print("FACE RECOGNITION TESTER")
        print("="*50)
        print("1. Add a face")
        print("2. Recognize faces in image")
        print("3. List stored faces")
        print("4. Clear all faces")
        print("5. Exit")
        
        choice = input("\nChoose option (1-5): ").strip()
        
        if choice == '1':
            image_path = input("Enter image path: ").strip()
            name = input("Enter person's name: ").strip()
            tester.add_face(image_path, name)
        
        elif choice == '2':
            image_path = input("Enter image path to test: ").strip()
            tester.recognize_face(image_path)
        
        elif choice == '3':
            tester.list_faces()
        
        elif choice == '4':
            confirm = input("Clear ALL face data? (y/n): ").strip().lower()
            if confirm == 'y':
                tester.clear_faces()
        
        elif choice == '5':
            print("Goodbye!")
            break
        
        else:
            print("Invalid choice")

if __name__ == "__main__":
    main()