@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Add video preview next to transcript in Review and Review All screens"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
