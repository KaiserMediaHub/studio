@echo off
cd /d "%~dp0"
echo Running review flags tests (includes new clip-title regression test)...
python test_review_flags.py || goto :fail

echo.
echo Re-running project pipeline tests (regression check)...
python test_project_pipeline.py || goto :fail

echo.
echo Re-running clip/post link tests (regression check)...
python test_clip_post_link.py || goto :fail

echo.
echo Syntax-checking app.py...
python -c "import ast; ast.parse(open('app.py').read()); print('OK')" || goto :fail

echo.
echo Committing and pushing...
git add -A
git commit -m "Show clip filename at top of single-clip Caption Review view (Bug #2, Ben's report 2026-09-11)"
git push
echo.
echo Done. Now deploy on the server:
echo   1. ssh root@178.104.152.111
echo   2. cd /var/www/studio ^&^& git pull ^&^& systemctl restart studio
echo   3. systemctl status studio   (confirm "active (running)")
echo.
echo Then open any clip's Caption Review page and check the filename shows above the transcript.
pause >nul
goto :eof

:fail
echo.
echo TESTS FAILED. Not committing. Read the output above.
pause >nul
