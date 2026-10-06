@echo off
cd /d "%~dp0"
echo Checking .env.example for real secrets...
findstr /C:"sk-ant-" .env.example >nul && (echo REAL KEY FOUND IN .env.example -- remove it first. Not committing. & goto :fail)

echo.
echo Running e-book gate tests...
python test_ebook.py || goto :fail

echo.
echo Re-running regression tests...
python test_clip_post_link.py || goto :fail
python test_project_pipeline.py || goto :fail
python test_review_flags.py || goto :fail

echo.
echo Syntax-checking app.py, database.py...
python -c "import ast; [ast.parse(open(f).read()) for f in ['app.py','database.py']]; print('OK')" || goto :fail

echo.
echo Committing and pushing...
git add -A
git commit -m "Add public e-book email gate at kmgtools.us/ebooks/content-playbook: email unlocks download, leads stored in ebook_leads, admin view/CSV at /settings/ebook-leads, nginx rate limit 10/min + X-Accel-Redirect file serving (Ben's ask 2026-10-06). PDF stays server-side only, never in the public repo."
git push
echo.
echo Pushed. Deploy steps are in the chat message -- follow them in order.
pause >nul
goto :eof

:fail
echo.
echo FAILED. Not committing. Read the output above.
pause >nul
