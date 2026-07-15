@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git init
git add .
git commit -m "KMG Studio - foundation skeleton"
git remote add origin https://github.com/KaiserMediaHub/studio.git
git branch -M main
git push -u origin main

echo.
echo DONE. Scroll up and check for any red error text.
echo If you see one, copy it and send it to Ben's assistant.
pause
