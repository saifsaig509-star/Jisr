@echo off
:: Run as Administrator once. Lets the phone reach the PC on the local network.
:: (Rules from the old "BTBridge" name are removed first so they are not duplicated.)
netsh advfirewall firewall delete rule name="BTBridge TCP" >nul 2>&1
netsh advfirewall firewall delete rule name="BTBridge UDP" >nul 2>&1
netsh advfirewall firewall delete rule name="Jisr TCP" >nul 2>&1
netsh advfirewall firewall delete rule name="Jisr UDP" >nul 2>&1
netsh advfirewall firewall add rule name="Jisr TCP" dir=in action=allow protocol=TCP localport=47810 profile=private
netsh advfirewall firewall add rule name="Jisr UDP" dir=in action=allow protocol=UDP localport=47810 profile=private
echo Done.
pause
