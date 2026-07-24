@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Calendar: media upload + Instagram/YouTube support in Add Post"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
