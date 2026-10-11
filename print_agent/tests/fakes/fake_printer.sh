#!/bin/sh
# Fake network printer for the e2e test: a CUPS server sharing one queue over
# IPP whose backend runs print_cmd.sh (bumps the page counter), plus the fake
# SNMP agent. Used instead of ippeveprinter, which needs IPv6 and DNS-SD.
set -e
cp /fakes/backend_fake /usr/lib/cups/backend/fake
chmod 0700 /usr/lib/cups/backend/fake
cat > /etc/cups/cupsd.conf <<'EOF'
Listen 0.0.0.0:631
Browsing Off
ServerAlias *
DefaultAuthType None
WebInterface No
<Location />
  Order allow,deny
  Allow all
</Location>
<Policy default>
  <Limit All>
    Order deny,allow
  </Limit>
</Policy>
EOF
mkdir -p /run/cups
cupsd
for i in $(seq 1 50); do lpstat -r >/dev/null 2>&1 && break; sleep 0.2; done
lpadmin -p print -E -v fake:/ -m drv:///sample.drv/generic.ppd -o printer-is-shared=true
python3 /fakes/fake_snmp.py &
exec tail -f /var/log/cups/error_log
