$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (!(Test-Path './.venv')) { py -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt
if (!(Test-Path './data/catalog.sqlite3')) {
    Write-Host 'Создайте первую учетную запись администратора.'
    & .\.venv\Scripts\python.exe setup_admin.py
}
$env:DOODA_SECRET_KEY='local-development-only-change-on-server'
$env:DOODA_LOCAL_HTTP='1'
& .\.venv\Scripts\python.exe -m waitress --host=127.0.0.1 --port=5000 app:app
