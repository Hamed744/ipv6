#!/bin/bash

# --- Stronger startup script for PaaS environments like Render ---

echo "--- Starting Robust Network Setup ---"

# 1. Wait a bit longer for the network to initialize
echo "Waiting for network interface to be fully up..."
sleep 10

# 2. Find the main network interface more robustly
INTERFACE=""
# Try to find the interface connected to the default route
iface_test=$(ip route | grep default | awk '{print $5}')
if [ -n "$iface_test" ]; then
    INTERFACE=$iface_test
    echo "Found interface via 'ip route': $INTERFACE"
else
    # Fallback to finding the first non-loopback interface
    INTERFACE=$(ip -o link show | awk -F': ' '{print $2}' | grep -v 'lo' | head -n 1)
    echo "Found interface via fallback method: $INTERFACE"
fi

if [ -z "$INTERFACE" ]; then
    echo "CRITICAL: Could not find any network interface. Exiting setup."
    # We still need to start the app, or Render will think it failed.
    echo "Starting Uvicorn server without network setup..."
    uvicorn app:app --host 0.0.0.0 --port 7860 --workers 1
    exit 0
fi

# 3. Retry loop to find the IPv6 address
IPV6_BASE_ADDR=""
RETRY_COUNT=0
MAX_RETRIES=5 # Try for 25 seconds (5 * 5s)
while [ -z "$IPV6_BASE_ADDR" ] && [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    echo "Attempting to find IPv6 address (Attempt $((RETRY_COUNT+1))/$MAX_RETRIES)..."
    
    # Try to get the first global IPv6 address that is not a temporary one (mngtmpaddr)
    IPV6_BASE_ADDR=$(ip -6 addr show dev $INTERFACE scope global | grep -v "mngtmpaddr" | awk '{print $2}' | cut -d'/' -f1 | head -n 1)

    if [ -z "$IPV6_BASE_ADDR" ]; then
        echo "No global IPv6 address found yet. Waiting 5 seconds..."
        sleep 5
    fi
    RETRY_COUNT=$((RETRY_COUNT+1))
done


IPV6_LIST_FILE="/app/ipv6_ips.txt"
echo "" > $IPV6_LIST_FILE # Clear the file

if [ -z "$IPV6_BASE_ADDR" ]; then
  echo "ERROR: After all retries, no IPv6 address found. Cannot use IPv6 rotation."
  echo "--- DUMPING NETWORK INFO FOR DEBUGGING ---"
  echo "--- 'ip addr' output: ---"
  ip addr
  echo "--- 'ip -6 route' output: ---"
  ip -6 route
  echo "--- END OF DEBUG INFO ---"
else
  echo "SUCCESS: Found IPv6 Base Address: $IPV6_BASE_ADDR"
  # We generate 200 different IPs based on the main one
  # The prefix is usually the first 4 parts of the IPv6 address (a /64 network)
  PREFIX=$(echo $IPV6_BASE_ADDR | cut -d':' -f1-4)
  echo "Detected IPv6 Prefix for /64 network: $PREFIX"

  # Add the base IP itself to the list
  echo "$IPV6_BASE_ADDR" >> $IPV6_LIST_FILE

  # Add 199 additional IPs (from ::2 to ::200)
  for i in $(seq 2 200); do
    IP_TO_ADD="${PREFIX}::${i}"
    # Using `|| true` to prevent script from failing if `ip -6 addr add` is forbidden
    # Adding a log to see if it succeeds or fails
    if ip -6 addr add ${IP_TO_ADD}/64 dev $INTERFACE; then
        echo "Successfully added IP: ${IP_TO_ADD}"
    else
        echo "WARNING: Failed to add IP: ${IP_TO_ADD}. This might be a permission issue."
    fi
    # We still add it to the list, as the failure might be silent.
    echo "${IP_TO_ADD}" >> $IPV6_LIST_FILE
  done
  echo "Finished attempting to add 200 IPv6 addresses and saved to $IPV6_LIST_FILE."
fi

# Now, start the main application regardless of the outcome
echo "--- Network setup finished. Starting Uvicorn server... ---"
uvicorn app:app --host 0.0.0.0 --port 7860 --workers 1
