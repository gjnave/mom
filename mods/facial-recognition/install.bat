@echo off
echo Installing Face Recognition for Discord Bot...
echo.

git clone https://github.com/ageitgey/face_recognition
cd face_recognition
python -m venv venv 
call venv\scripts\activate

:: Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found in PATH!
    echo Please install Python 3.8+ from python.org or ensure it's in your PATH
    pause
    exit /b 1
)

:: Check for CMake
cmake --version >nul 2>&1
if errorlevel 1 (
    echo CMake not found in PATH!
    echo But you said you have it installed? Check your environment variables.
    pause
    exit /b 1
)

echo Found Python and CMake - proceeding with installation...
echo.

:: Upgrade pip first
echo Upgrading pip...
python -m pip install --upgrade pip

:: Install core dependencies in order
pip install setuptools pip --upgrade
echo Installing numpy...
pip install numpy

echo Installing scipy...
pip install scipy

echo Installing other image processing libraries...
pip install pillow
pip install scikit-image

:: Install dlib with CMake
echo Installing dlib (this may take a while)...
pip install dlib

:: Install face_recognition and discord
echo Installing face_recognition...
pip install face_recognition

echo Installing discord.py...
pip install discord.py

:: Test the installation
echo.
echo Testing installation...
python -c "import face_recognition; print('✓ face_recognition imported successfully')" && echo.
python -c "import discord; print('✓ discord.py imported successfully')" && echo.

echo.
echo ========================================
echo Installation complete!
echo You can now start building your Discord bot.
echo ========================================
echo.

pause