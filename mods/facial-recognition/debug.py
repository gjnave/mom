import sys
import os
import site
import subprocess
import pkg_resources

def debug_models():
    print("=== FACE RECOGNITION MODELS DEBUG ===")
    print(f"Python: {sys.executable}")
    print(f"Python path: {sys.prefix}")
    print()
    
    # Check all site packages directories
    print("Site packages directories:")
    for path in site.getsitepackages():
        print(f"  {path}")
        if os.path.exists(path):
            # Look for face_recognition related packages
            for item in os.listdir(path):
                if 'face' in item.lower() or 'dlib' in item.lower():
                    print(f"    📁 {item}")
    print()
    
    # Check if face_recognition_models is installed
    print("Checking installed packages:")
    installed_packages = [pkg.key for pkg in pkg_resources.working_set]
    for pkg in ['face-recognition', 'face-recognition-models', 'dlib']:
        if pkg in installed_packages:
            print(f"  ✅ {pkg} is installed")
        else:
            print(f"  ❌ {pkg} is NOT installed")
    print()
    
    # Try to import and find models
    print("Trying to import face_recognition_models...")
    try:
        import face_recognition_models
        print(f"  ✅ face_recognition_models imported!")
        print(f"  Location: {face_recognition_models.__file__}")
        
        # Check models directory
        models_dir = os.path.join(os.path.dirname(face_recognition_models.__file__), 'models')
        if os.path.exists(models_dir):
            print(f"  ✅ Models directory found: {models_dir}")
            models = os.listdir(models_dir)
            print(f"  Models found: {len(models)}")
            for model in models:
                print(f"    📄 {model}")
        else:
            print(f"  ❌ Models directory NOT found at: {models_dir}")
            
    except ImportError as e:
        print(f"  ❌ Could not import face_recognition_models: {e}")
    
    print("\nTrying to import face_recognition...")
    try:
        import face_recognition
        print("  ✅ face_recognition imported successfully!")
        return True
    except Exception as e:
        print(f"  ❌ face_recognition import failed: {e}")
        return False

if __name__ == "__main__":
    success = debug_models()
    if success:
        print("\n🎉 Everything is working!")
    else:
        print("\n🔧 There are issues to fix...")