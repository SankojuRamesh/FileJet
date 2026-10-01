#!/usr/bin/env bash
# Network impairment for WAN-like tests on Linux (run as root on ONE peer).
#   sudo bench/netem.sh eth0 wan        # 40 ms RTT, 0.1 % loss, 1 Gbit
#   sudo bench/netem.sh eth0 bad        # 150 ms RTT, 1 % loss
#   sudo bench/netem.sh eth0 slow       # 100 Mbit bottleneck
#   sudo bench/netem.sh eth0 off
# Then run the transfer / bench/benchmark.py against the other machine and compare.
set -euo pipefail
IF=${1:?interface}; MODE=${2:?mode}
tc qdisc del dev "$IF" root 2>/dev/null || true
case "$MODE" in
  wan)  tc qdisc add dev "$IF" root netem delay 20ms 2ms loss 0.1% rate 1gbit ;;
  bad)  tc qdisc add dev "$IF" root netem delay 75ms 10ms loss 1% rate 1gbit ;;
  slow) tc qdisc add dev "$IF" root netem delay 10ms rate 100mbit ;;
  flap) tc qdisc add dev "$IF" root netem delay 20ms loss 0.1%
        echo "dropping all traffic for 20 s every 60 s (tests reconnect/resume); Ctrl+C to stop"
        while true; do sleep 60; tc qdisc change dev "$IF" root netem loss 100%; sleep 20
              tc qdisc change dev "$IF" root netem delay 20ms loss 0.1%; done ;;
  off)  echo "impairment removed" ;;
  *)    echo "unknown mode $MODE"; exit 1 ;;
esac
tc qdisc show dev "$IF"
