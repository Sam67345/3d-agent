Write-Output '=== Exes with size 1966080 under D:\ ==='
$exes = Get-ChildItem 'D:\' -Recurse -Depth 2 -Filter *.exe -ErrorAction SilentlyContinue | Where-Object { $_.Length -eq 1966080 }
foreach ($e in $exes) {
  Write-Output ("FILE: {0}" -f $e.FullName)
  Write-Output ("  Modified: {0}" -f $e.LastWriteTime)
  $s = Get-AuthenticodeSignature $e.FullName
  Write-Output ("  SigStatus: {0}" -f $s.Status)
  Write-Output ("  Publisher: {0}" -f $s.SignerCertificate.Subject)
}
Write-Output '=== GUID folder full listing ==='
Get-ChildItem 'D:\0ac3967-03ab-ae7-a198-b552' -Recurse -Force -ErrorAction SilentlyContinue | Select-Object FullName, Length, LastWriteTime | Format-Table -AutoSize
