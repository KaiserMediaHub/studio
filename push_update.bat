@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Connect Studio to Cloud KMG (Nextcloud): pull raw footage into projects, archive exports back out"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
