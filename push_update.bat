@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Nav: Settings dropdown (nests Postiz Setup); rename Quick Posts to Write Post"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
