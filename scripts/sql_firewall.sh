#!/usr/bin/env bash
#
# Opens (or closes) a SQL firewall rule for the caller's current public IP, so a local
# run of set_passwords.py or bootstrap_demo_content.sh can actually reach
# mob-sql-server-02. Both of those scripts need this same firewall rule; this exists so
# it's one command instead of hand-copying an IP into an `az` command.
#
# Captures the IP ONCE into a variable and reuses it for both --start-ip-address and
# --end-ip-address, rather than calling curl twice inline (which, rarely, over a
# multi-homed or changing connection, could return two different addresses between
# the two calls and create a rule that doesn't actually match either).
#
# Usage:
#   ./scripts/sql_firewall.sh open     # create the rule for your current IP
#   ./scripts/sql_firewall.sh close    # delete it when you're done
#
# The rule name is fixed (not per-user), so running `open` twice from different IPs
# just moves the same rule rather than accumulating stale ones -- and `close` always
# knows exactly what to remove without you having to remember your IP from earlier.

set -euo pipefail

ACTION="${1:-}"
if [ "$ACTION" != "open" ] && [ "$ACTION" != "close" ]; then
  echo "Usage: $0 open|close" >&2
  exit 1
fi

RESOURCE_GROUP="MOB"
SERVER="mob-sql-server-02"
RULE_NAME="local-dev-temp"

if [ "$ACTION" = "open" ]; then
  MY_IP="$(curl -s https://api.ipify.org)"
  if [ -z "$MY_IP" ]; then
    echo "Could not determine your public IP (curl to api.ipify.org failed)." >&2
    exit 1
  fi
  echo "Your public IP: $MY_IP"
  az sql server firewall-rule create \
    --resource-group "$RESOURCE_GROUP" --server "$SERVER" \
    --name "$RULE_NAME" \
    --start-ip-address "$MY_IP" --end-ip-address "$MY_IP"
  echo
  echo "Firewall rule '$RULE_NAME' is open for $MY_IP."
  echo "Run '$0 close' when you're done -- don't leave this open indefinitely."
else
  az sql server firewall-rule delete \
    --resource-group "$RESOURCE_GROUP" --server "$SERVER" \
    --name "$RULE_NAME"
  echo "Firewall rule '$RULE_NAME' removed."
fi
