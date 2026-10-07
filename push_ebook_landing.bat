@echo off
cd /d "%~dp0"
echo Checking .env.example for real secrets...
findstr /C:"sk-ant-" .env.example >nul && (echo REAL KEY FOUND IN .env.example -- remove it first. Not committing. & goto :fail)

echo.
echo Making sure no PDF is about to be committed (the Playbook must stay off GitHub)...
git ls-files --others --exclude-standard | findstr /I /C:".pdf" >nul && (echo A PDF is untracked and not ignored -- stopping. & goto :fail)

echo.
echo Running e-book tests...
python test_ebook.py || goto :fail

echo.
echo Re-running regression tests...
python test_clip_post_link.py || goto :fail
python test_project_pipeline.py || goto :fail
python test_review_flags.py || goto :fail
python test_context_regen.py || goto :fail
python test_calendar_delete_and_multi_image.py || goto :fail

echo.
echo Syntax-checking app.py, database.py...
python -c "import ast; [ast.parse(open(f).read()) for f in ['app.py','database.py']]; print('OK')" || goto :fail

echo.
echo Committing and pushing...
if exist ".git\index.lock" (
  echo Found a stale .git\index.lock file -- deleting it.
  del ".git\index.lock"
)
git add -A || goto :fail
git commit -m "Playbook: real landing page (value outline from the PDF, proof, about, form) and a thank-you page that starts the download; cover image served under /ebooks/content-playbook/ so it works on kmgtools.us (Ben's ask 2026-10-07)." || goto :fail
git push || goto :fail
echo.
echo Pushed. On the server run:  cd /var/www/studio ^&^& git pull ^&^& systemctl restart studio
echo (No nginx change needed for this one.)
pause >nul
goto :eof

:fail
echo.
echo FAILED. Not committing. Read the output above.
pause >nul
goto :eof
