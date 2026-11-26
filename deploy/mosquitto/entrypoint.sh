#!/bin/sh
# Builds Mosquitto's runtime config from mosquitto.conf + .env, then starts it.
#   USB_MQTT_PASSWORD empty (default): no login needed.
#   USB_MQTT_PASSWORD set: clients must log in as USB_MQTT_USERNAME (default "bridge") with that password.
# Either way clients may only use the curtain's topics (v3/... and curtain/...), so the broker
# can't be used to relay anything else.
set -eu

RUN=/mosquitto/run
mkdir -p "$RUN"
cat > "$RUN/acl" <<'ACL'
pattern readwrite v3/#
pattern readwrite curtain/#
ACL

if [ -n "${USB_MQTT_PASSWORD:-}" ]; then
  mosquitto_passwd -c -b "$RUN/passwd" "${USB_MQTT_USERNAME:-bridge}" "$USB_MQTT_PASSWORD"
  printf 'allow_anonymous false\npassword_file %s/passwd\n' "$RUN" > "$RUN/mosquitto.conf"
  echo "USB link broker: login required (user '${USB_MQTT_USERNAME:-bridge}')."
else
  printf 'allow_anonymous true\n' > "$RUN/mosquitto.conf"
  echo "USB link broker: no login needed (set USB_MQTT_PASSWORD in .env to require one)."
fi
printf 'acl_file %s/acl\n' "$RUN" >> "$RUN/mosquitto.conf"
cat /mosquitto/config/mosquitto.conf >> "$RUN/mosquitto.conf"

chown -R mosquitto:mosquitto "$RUN"
chmod 0700 "$RUN"
chmod 0600 "$RUN"/*

exec /usr/sbin/mosquitto -c "$RUN/mosquitto.conf"
