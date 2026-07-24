@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Fix upload: wait for all files to finish before reloading, not just the first"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
