@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Task #7: phase tracking (Studio reads Degas status, owns Drafting/Post Review)"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
