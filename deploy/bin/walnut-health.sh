#!/bin/bash
# Health check: services, local and public health endpoint, certificate days left, disk, last backup.
URL=$(cat /var/lib/walnut-desk/public_url.txt 2>/dev/null || echo https://77-42-69-194.sslip.io)
H=${URL#https://}; rc=0
for s in walnut-desk caddy walnut-backup.timer walnut-rules.timer; do printf "%-22s %s\n" "$s" "$(systemctl is-active $s)"; [ "$(systemctl is-active $s)" = active ] || rc=1; done
printf "%-22s %s\n" local "$(curl -s -m 5 http://127.0.0.1:8000/healthz || echo DOWN)"
printf "%-22s %s\n" public "$(curl -s -m 10 $URL/healthz || echo DOWN)"
end=$(echo | openssl s_client -connect $H:443 -servername $H 2>/dev/null | openssl x509 -noout -enddate | cut -d= -f2)
[ -n "$end" ] && printf "%-22s %s days\n" cert_days_left $(( ( $(date -d "$end" +%s) - $(date +%s) ) / 86400 ))
printf "%-22s %s\n" disk_used "$(df -h / | awk 'NR==2{print $5}')"
printf "%-22s %s\n" last_backup "$(ls -1t /var/lib/walnut-desk/backups/app-*.db.gz 2>/dev/null | head -1 | xargs -r basename)"
exit $rc
