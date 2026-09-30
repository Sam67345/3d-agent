Write-Output '=== Defender: detected threats ==='
Get-MpThreat | Select-Object ThreatName, ThreatID, SeverityID | Format-Table -AutoSize
Write-Output '=== Defender: detection records ==='
Get-MpThreatDetection | Select-Object ThreatName, InitialDetectionTime, ActionSuccess, @{n='Resource';e={$_.Resources -join '; '}} | Format-List
Write-Output '=== MpCmdRun.log tail (today) ==='
Get-Content 'C:\ProgramData\Microsoft\Windows Defender\Support\MpCmdRun.log' -Tail 60 -ErrorAction SilentlyContinue
