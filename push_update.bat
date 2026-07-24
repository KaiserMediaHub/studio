@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Calendar: full month grid, click-to-add-post, channel selection"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
