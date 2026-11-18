@echo off
setlocal
REM Push only commits that were already reviewed and committed.
REM This script does not stage files, commit, pull, or rebase.
cd /d "%~dp0"

git ls-files --error-unmatch workspace/.env >nul 2>&1
if not errorlevel 1 (
  echo [git_sync] workspace/.env is tracked. Aborting to protect the API key.
  exit /b 1
)

git diff --cached --quiet
if errorlevel 1 (
  echo [git_sync] Staged changes exist. Review and commit them before pushing.
  exit /b 1
)

git push -u origin main
if errorlevel 1 (
  echo [git_sync] Push failed. Check the remote branch before retrying.
  exit /b 1
)
echo [git_sync] done.
