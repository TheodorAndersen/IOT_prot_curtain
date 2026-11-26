#!/bin/sh
# Writes Mosquitto's password file from .env (USB_MQTT_USERNAME / USB_MQTT_PASSWORD), then starts it.
# Without a password the file stays empty: nobody can log in, so the USB link is off and TTN still works.
set -eu

PWFILE=/mosquitto/passwd
: > "$PWFILE"
if [ -n "${USB_MQTT_PASSWORD:-}" ]; then
  mosquitto_passwd -c -b "$PWFILE" "${USB_MQTT_USERNAME:-bridge}" "$USB_MQTT_PASSWORD"
  echo "USB link broker: user '${USB_MQTT_USERNAME:-bridge}' can log in."
else
  echo "USB link broker: USB_MQTT_PASSWORD is not set in .env, so nobody can log in (USB link off)."
fi
chown mosquitto:mosquitto "$PWFILE"
chmod 0600 "$PWFILE"

exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
