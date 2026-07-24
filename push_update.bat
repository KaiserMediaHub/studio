@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Nav rework: client picker + persistent sidebar menu, clickable post links"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
