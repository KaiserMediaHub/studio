@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Add Review All: bulk transcript review/save across every clip in a project"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
