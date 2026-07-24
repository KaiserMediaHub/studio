@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Add Context box + more visible Regenerate on Write Post screens"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
