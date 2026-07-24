@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Project workspace: upload, transcribe, review, export, write posts; archive/delete"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
