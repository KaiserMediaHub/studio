@echo off
cd /d "%~dp0"
echo Committing and pushing Studio post-title fix...
git add -A
git commit -m "Add post title so Ben can tell which video each post came from"
git push
echo.
echo Done. Press any key to close this window.
pause >nul
