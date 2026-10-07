@echo off
cd /d "%~dp0"
echo Checking .env.example for real secrets...
findstr /C:"sk-ant-" .env.example >nul && (echo REAL KEY FOUND IN .env.example -- remove it first. Not committing. & goto :fail)

echo.
echo Running e-book tests (sanity check -- this change is nginx config only)...
python test_ebook.py || goto :fail

echo.
echo Committing and pushing...
if exist ".git\index.lock" (
  echo Found a stale .git\index.lock file -- deleting it.
  del ".git\index.lock"
)
git add -A || goto :fail
git commit -m "nginx-ebooks.conf: serve the e-book page on port 80 instead of redirecting to HTTPS (fixes Cloudflare Flexible redirect loop), keep 443 block for Full mode." || goto :fail
git push || goto :fail
echo.
echo Pushed. Server steps are in the chat message.
pause >nul
goto :eof

:fail
echo.
echo FAILED. Not committing. Read the output above.
pause >nul
goto :eof
