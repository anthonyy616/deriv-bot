$cur = Get-Location
Write-Host "Launching Signal Server..."
Start-Process powershell -ArgumentList "-NoExit", "-Command cd '$cur'; python main.py"
Write-Host "Launching MT5 Bridge..."
Start-Process powershell -ArgumentList "-NoExit", "-Command cd '$cur'; python mt5_bridge.py"
