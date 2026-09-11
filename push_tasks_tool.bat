@echo off
cd /d "%~dp0"
echo Running new Tasks tests...
python test_tasks.py || goto :fail

echo.
echo Re-running project assignment tests (regression check -- shares the Access Codes identity system)...
python test_project_assignment.py || goto :fail

echo.
echo Re-running access codes tests (regression check)...
python test_access_codes.py || goto :fail

echo.
echo Re-running project pipeline tests (regression check)...
python test_project_pipeline.py || goto :fail

echo.
echo Syntax-checking app.py and database.py...
python -c "import ast; [ast.parse(open(f).read()) for f in ['app.py','database.py']]; print('OK')" || goto :fail

echo.
echo Committing and pushing...
git add -A
git commit -m "Add internal Tasks tool: team-wide task/project tracker, list view with assignees and due dates, separate from the client content pipeline"
git push
echo.
echo Done. Now deploy on the server:
echo   1. ssh root@178.104.152.111
echo   2. cd /var/www/studio ^&^& git pull ^&^& systemctl restart studio
echo   3. systemctl status studio   (confirm "active (running)")
echo.
echo Then click "Tasks" in the Studio sidebar (below Glossary) to try it.
pause >nul
goto :eof

:fail
echo.
echo TESTS FAILED. Not committing. Read the output above.
pause >nul
