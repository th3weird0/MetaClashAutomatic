#!/bin/sh
# 文件作用：保留自定义防火墙脚本入口，目前仅记录启动提示日志。
. /usr/share/openclash/log.sh
. /lib/functions.sh

# This script is called by /etc/init.d/openclash
# Add your custom firewall rules here, they will be added after the end of the OpenClash iptables rules

LOG_OUT "Tip: Start Add Custom Firewall Rules..."

exit 0