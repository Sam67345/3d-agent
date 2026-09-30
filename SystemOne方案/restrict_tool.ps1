$folder = Get-ChildItem 'D:\' -Directory | Where-Object { [int][char]$_.Name[0] -gt 127 -and (Get-ChildItem $_.FullName -Filter *.exe -ErrorAction SilentlyContinue) }
Write-Output ("Target folder: " + $folder.FullName)
$exe = Get-ChildItem $folder.FullName -Filter *.exe | Select-Object -First 1
Write-Output ("Target exe: " + $exe.FullName)
$acl = Get-Acl $exe.FullName
$sid = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-21-3935372626-2646828126-1866285873-1001')
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'Write', 'Deny')
$acl.AddAccessRule($rule)
Set-Acl -Path $exe.FullName -AclObject $acl
Write-Output 'ACL deny-write applied. Verifying:'
(Get-Acl $exe.FullName).Access | Where-Object { $_.AccessControlType -eq 'Deny' } | Format-Table IdentityReference, FileSystemRights, AccessControlType -AutoSize
