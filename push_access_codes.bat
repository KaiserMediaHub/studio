@echo off
cd /d "%~dp0"
echo Running tests...
python test_access_codes.py || goto :fail
python test_project_pipeline.py || goto :fail
python test_clip_post_link.py || goto :fail
python test_review_flags.py || goto :fail

echo.
echo Committing and pushing revocable access codes...
git add -A
git commit -m "Replace shared password with revocable, labeled access codes"
git push
echo.
echo Done. Press any key to close this window.
pause >nul
goto :eof

:fail
echo.
echo TESTS FAILED. Not committing. Read the output above.
pause >nul
