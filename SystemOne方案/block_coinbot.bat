@echo off
chcp 65001 >nul
echo 正在为"纪念币抢购助手"添加网络封锁规则...
netsh advfirewall firewall add rule name="Block_CoinBot_Out" dir=out action=block program="D:\纪念币抢购助手升级版\纪念币抢购1.1.exe"
if %errorlevel%==0 (echo 成功：该程序已无法联网（更新和远程指令均被切断）) else (echo 失败，请确认是用"以管理员身份运行"打开的)
pause
