@echo off
cd /d "%~dp0"
echo Running project pipeline tests (includes new top-up write-posts tests)...
python test_project_pipeline.py || goto :fail

echo.
echo Re-running clip/post link tests (regression check)...
python test_clip_post_link.py || goto :fail

echo.
echo Re-running context/regen tests (regression check)...
python test_context_regen.py || goto :fail

echo.
echo Re-running review flags tests (regression check)...
python test_review_flags.py || goto :fail

echo.
echo Syntax-checking app.py...
python -c "import ast; ast.parse(open('app.py').read()); print('OK')" || goto :fail

echo.
echo Committing and pushing...
git add -A
git commit -m "Allow writing posts for newly-added clips in a project that already has posts (Ben's report 2026-09-17), without duplicating posts for clips already written"
git push
echo.
echo Done. Now deploy on the server:
echo   1. ssh root@178.104.152.111
echo   2. cd /var/www/studio ^&^& git pull ^&^& systemctl restart studio
echo   3. systemctl status studio   (confirm "active (running)")
echo.
echo Then open a project that already has posts, add a new clip, transcribe/review it,
echo and confirm "Write posts for new clips" reappears and only writes the new one.
pause >nul
goto :eof

:fail
echo.
echo TESTS FAILED. Not committing. Read the output above.
pause >nul
