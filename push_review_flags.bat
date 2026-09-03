@echo off
cd /d "%~dp0"
echo Running tests...
python test_review_flags.py || goto :fail
python test_project_pipeline.py || goto :fail
python test_clip_post_link.py || goto :fail

echo.
echo Committing and pushing "Review Transcript / Review Video" flags...
git add -A
git commit -m "Add transcript/video review flags with dashboard badge (orange/purple checkboxes, Approved clears)"
git push
echo.
echo Done. Press any key to close this window.
pause >nul
goto :eof

:fail
echo.
echo TESTS FAILED. Not committing. Read the output above.
pause >nul
