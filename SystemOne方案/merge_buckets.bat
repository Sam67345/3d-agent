@echo off
robocopy "D:\AI_RecycleBin\{6A07CEC3-3257-4F40-A489-FFD229ECB457}\50\Program Files (x86)\WeGame" "D:\Program Files (x86)\WeGame" /E /XC /XN /XO /R:1 /W:1 /NFL /NDL /NP
robocopy "D:\AI_RecycleBin\{6A07CEC3-3257-4F40-A489-FFD229ECB457}\43\PDF\Foxit PDF Editor\plugins" "D:\PDF\Foxit PDF Editor\plugins" /E /XC /XN /XO /R:1 /W:1 /NFL /NDL /NP
robocopy "D:\AI_RecycleBin\{6A07CEC3-3257-4F40-A489-FFD229ECB457}\7\BaiduNetdiskDownload" "D:\BaiduNetdiskDownload" /E /XC /XN /XO /R:1 /W:1 /NFL /NDL /NP
echo MERGE_BATCH_DONE
