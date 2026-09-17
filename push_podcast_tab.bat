@echo off
cd /d "%~dp0"
echo Running podcast tab tests...
python test_podcast.py || goto :fail

echo.
echo Re-running project pipeline tests (regression check)...
python test_project_pipeline.py || goto :fail

echo.
echo Re-running review flags tests (regression check)...
python test_review_flags.py || goto :fail

echo.
echo Re-running tasks tests (regression check -- shares the sidebar nav template edits)...
python test_tasks.py || goto :fail

echo.
echo Syntax-checking app.py, degas_client.py, podcast_client.py, database.py...
python -c "import ast; [ast.parse(open(f).read()) for f in ['app.py','degas_client.py','podcast_client.py','database.py']]; print('OK')" || goto :fail

echo.
echo Committing and pushing...
git add -A
git commit -m "Merge the standalone podcast-page-generator tool into Studio as a new Podcast tab: chunked upload + async status polling (fixes the original Cloudflare 524 timeout), Claude-generated titles/quotes/topics via podcast_client.py, Whisper transcription proxied to Degas's new /transcribe-audio endpoint instead of loading a second model"
git push
echo.
echo Done. Now deploy on the server:
echo   1. Make sure Degas's half is already deployed first (push_podcast_audio_endpoint.bat
echo      in the degas-clips folder) -- Studio's podcast tab calls Degas's new endpoint and
echo      will error out until that side is live.
echo   2. ssh root@178.104.152.111
echo   3. cd /var/www/studio ^&^& git pull
echo   4. .venv/bin/pip install -r requirements.txt    (adds the new "anthropic" dependency --
echo      always use .venv/bin/pip here, never bare pip3, or gunicorn won't see the new package)
echo   5. Add ANTHROPIC_API_KEY to /var/www/studio/.env if it isn't already there
echo      (see .env.example for the exact line -- this is Studio's own Claude API key,
echo      separate from Hemingway's)
echo   6. systemctl restart studio
echo   7. systemctl status studio   (confirm "active (running)")
echo.
echo Then click "Podcast" in the Studio sidebar and generate a real episode page end to end.
pause >nul
goto :eof

:fail
echo.
echo TESTS FAILED. Not committing. Read the output above.
pause >nul
