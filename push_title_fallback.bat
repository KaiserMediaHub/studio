@echo off
cd /d "%~dp0"
echo Running tests...
python test_clip_post_link.py || goto :fail
python test_project_pipeline.py || goto :fail
python test_context_regen.py || goto :fail
python test_project_assignment.py || goto :fail

echo.
echo Committing and pushing title fallback fix...
git add -A
git commit -m "Fall back to clip filename for the post title badge when the title DB column is empty"
git push
echo.
echo Done. Press any key to close this window.
pause >nul
goto :eof

:fail
echo.
echo TESTS FAILED. Not committing. Read the output above.
pause >nul
