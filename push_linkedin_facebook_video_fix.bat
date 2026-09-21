@echo off
cd /d "%~dp0"
echo Running clip/post link tests (includes new LinkedIn/Facebook video-attach tests)...
python test_clip_post_link.py || goto :fail

echo.
echo Re-running project pipeline tests (regression check)...
python test_project_pipeline.py || goto :fail

echo.
echo Re-running context/regen tests (regression check)...
python test_context_regen.py || goto :fail

echo.
echo Re-running review flags tests (regression check)...
python test_review_flags.py || goto :fail

echo.
echo Re-running calendar delete/multi-image tests (regression check -- calendar_create_post
echo route was reviewed for the same bug class and found NOT affected, still confirming
echo nothing else broke)...
python test_calendar_delete_and_multi_image.py || goto :fail

echo.
echo Syntax-checking app.py...
python -c "import ast; ast.parse(open('app.py').read()); print('OK')" || goto :fail

echo.
echo Committing and pushing...
git add -A
git commit -m "Fix LinkedIn/Facebook posting without video despite a linked, exported clip (Ben's report 2026-09-21). Video fetch/attach was gated only on MEDIA_REQUIRED_IDENTIFIERS (Instagram/YouTube), so media-capable-but-not-required platforms like LinkedIn/Facebook never got a video attached even when one was available. Now fetches/attaches opportunistically for any MEDIA_CAPABLE_IDENTIFIERS channel; the hard block-with-error behavior is unchanged and still applies only to platforms that truly require media."
git push
echo.
echo Done. Now deploy on the server:
echo   1. ssh root@178.104.152.111
echo   2. cd /var/www/studio ^&^& git pull ^&^& systemctl restart studio
echo   3. systemctl status studio   (confirm "active (running)")
echo.
echo Then schedule a project-sourced post with an EXPORTED clip to LinkedIn or Facebook
echo (not YouTube) and confirm the video actually attaches this time, not just the caption.
pause >nul
goto :eof

:fail
echo.
echo TESTS FAILED. Not committing. Read the output above.
pause >nul
