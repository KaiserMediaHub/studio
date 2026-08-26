@echo off
cd /d "%~dp0"
echo Committing and pushing "Export all posts to Doc" feature...
git add -A
git commit -m "Add button to export all of a project's posts into one .docx"
git push
echo.
echo Done. Press any key to close this window.
pause >nul
