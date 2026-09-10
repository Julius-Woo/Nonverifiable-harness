#!/bin/sh
set -eu
# The controller installs this firewall before dropping NET_ADMIN permanently.
# Block Docker's embedded DNS, the bridge host, peers, and all other egress.
iptables -P OUTPUT DROP
iptables -A OUTPUT -d "$GATEWAY_IP" -p tcp --dport 8080 -j ACCEPT
iptables -P INPUT DROP
iptables -A INPUT -s "$GATEWAY_IP" -p tcp --sport 8080 \
    -m conntrack --ctstate ESTABLISHED -j ACCEPT
ip6tables -P OUTPUT DROP
ip6tables -P INPUT DROP
exec python3 /usr/local/bin/drop-privileges.py
