@echo off
rem Windows entry for this project's Praukron. PowerShell and cmd run this file
rem in place of the POSIX `praukron` beside it, which Windows cannot execute.
setlocal
for %%P in ("py -3" python3 python) do (
  %%~P -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1 && (
    set "python=%%~P"
    goto run
  )
)
>&2 echo Praukron needs Python 3.9 or newer on PATH as py, python3 or python.
exit /b 127
:run
%python% "%~dp0praukron" %*
exit /b %errorlevel%
