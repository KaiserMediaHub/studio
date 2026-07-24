@echo off
cd /d "%~dp0"
echo Working in: %cd%
echo.

git add .
git commit -m "Fix upload: shrink chunk size to fit under nginx's 10MB body-size limit"
git push

echo.
echo DONE. Scroll up and check for any red error text.
pause
