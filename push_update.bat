@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Also copy direct video uploads to Cloud KMG /imported for consistency"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
