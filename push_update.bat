@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Fix caption text color, add loading indicator, link posts to their clip's exported video for YouTube/IG scheduling"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
