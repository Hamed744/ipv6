#!/bin/bash

# Wait for the network interface to be fully up
echo "Waiting for network interface..."
sleep 5

# Find the main network interface (usually eth0)
INTERFACE=$(ip route | grep default | awk '{print $5}')
echo "Main interface found: $INTERFACE"

# Get the base IPv6 address of the container
# We take the first address from the assigned /64 block
IPV6_BASE_ADDR=$(ip -6 addr show dev $INTERFACE scope global | grep inet6 | awk '{print $2}' | cut -d'/' -f1 | head -n 1)

IPV6_LIST_FILE="/app/ipv6_ips.txt" # Changed to /app to match WORKDIR
echo "" > $IPV6_LIST_FILE # Clear the file

if [ -z "$IPV6_BASE_ADDR" ]; then
  echo "ERROR: No IPv6 address found. Cannot use IPv6 rotation. Using default network."
else
  # We generate 200 different IPs based on the main one
  # You can increase this number if needed, but 200 is a good start.
  PREFIX=$(echo $IPV6_BASE_ADDR | cut -d':' -f1-4)
  echo "Found IPv6 Prefix: $PREFIX"

  # Add the base IP itself to the list
  echo "$IPV6_BASE_ADDR" >> $IPV6_LIST_FILE

  # Add 199 additional IPs (from ::2 to ::200)
  for i in $(seq 2 200); do
    IP_TO_ADD="${PREFIX}::${i}"
    # Using `|| true` to prevent script from failing if `ip -6 addr add` is forbidden
    ip -6 addr add ${IP_TO_ADD}/64 dev $INTERFACE || true 
    echo "${IP_TO_ADD}" >> $IPV6_LIST_FILE
  done
  echo "Successfully added 200 IPv6 addresses (or attempted) and saved to $IPV6_LIST_FILE."
fi

# Now, start the main application
echo "Starting Uvicorn server..."
# Uvicorn will listen on 0.0.0.0:7860 as required by Render
uvicorn app:app --host 0.0.0.0 --port 7860 --workers 1
